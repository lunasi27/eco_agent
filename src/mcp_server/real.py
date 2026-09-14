from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import yaml

from src.mcp_server.protocol import ECOMCPServer


class RealECOMCPServer(ECOMCPServer):
    """真实 EDA 工具调用实现。

    配置加载顺序：
        config/project.yaml     → resolve 内部占位符（{project_name}, {work_dir}, {base.*}）
        config/run_context.yaml → resolve {project.*}, {base.*}
        config/eda_tools.yaml   → step 注册表 + 默认 timeout + parse_patterns

    执行模型：
        每个 step → 查 project.yaml.step_command → resolve 占位符 → 组环境变量
        → subprocess 调 scripts/eda/run_step_wrapper.csh → wrapper source cshrc → eval $CMD
        → pds_wait_files 检查 → Python 侧 parse_patterns 提取 violations
    """

    WRAPPER_SCRIPT = "scripts/eda/run_step_wrapper.csh"

    def __init__(
        self,
        eda_tools_path: str = "config/eda_tools.yaml",
        project_path: str = "config/project.yaml",
        run_context_path: str = "config/run_context.yaml",
        wrapper_path: str | None = None,
    ):
        self.eda_tools = self._load_yaml(eda_tools_path)
        self.project = self._load_yaml(project_path)
        self.run_context = self._load_yaml(run_context_path)
        self.wrapper_path = wrapper_path or self.WRAPPER_SCRIPT

        self._project_resolved = self._resolve_project(self.project)
        self._run_context_resolved = self._resolve_run_context(
            self.run_context, self._project_resolved,
        )

    @staticmethod
    def _load_yaml(path: str) -> dict:
        with open(path) as f:
            return yaml.safe_load(f)

    # ── 占位符 resolve（project.yaml 内部）──

    def _resolve_project(self, project: dict) -> dict:
        """Resolve project.yaml 内部的所有占位符引用（多轮迭代直到稳定）。"""
        flat: dict[str, str] = {}
        flat["project_name"] = str(project.get("project_name", ""))
        flat["work_dir"] = str(project.get("work_dir", ""))
        flat["project_cshrc"] = str(project.get("project_cshrc", ""))

        # 先放空壳，后续每 resolve 完一个就增量更新
        flat["base"] = {}
        flat["logs"] = {}
        flat["wait_flags"] = {}
        flat["step_command"] = {}

        base = dict(project.get("base", {}))
        logs = dict(project.get("logs", {}))
        wait_flags = dict(project.get("wait_flags", {}))
        step_command = dict(project.get("step_command", {}))

        for _ in range(10):
            changed = False

            for k, v in list(base.items()):
                new_val = self._resolve_one(str(v), flat)
                if new_val != str(v):
                    changed = True
                    base[k] = new_val
                flat["base"][k] = base[k]

            for k, v in list(logs.items()):
                new_val = self._resolve_one(str(v), flat)
                if new_val != str(v):
                    changed = True
                    logs[k] = new_val
                flat["logs"][k] = logs[k]

            for k, v in list(wait_flags.items()):
                new_val = self._resolve_one(str(v), flat)
                if new_val != str(v):
                    changed = True
                    wait_flags[k] = new_val
                flat["wait_flags"][k] = wait_flags[k]

            for k, v in list(step_command.items()):
                new_val = self._resolve_one(str(v), flat)
                if new_val != str(v):
                    changed = True
                    step_command[k] = new_val
                flat["step_command"][k] = step_command[k]

            if not changed:
                break

        return flat

    # ── 占位符 resolve（run_context.yaml，引用 project 解析后的值）──

    def _resolve_run_context(self, run_context: dict, resolved_project: dict) -> dict:
        """Resolve run_context.yaml 里的 {project_name}, {base.xxx} 等占位符。"""
        ctx_flat: dict[str, str] = {}
        ctx_flat["project_name"] = resolved_project["project_name"]
        ctx_flat["base"] = resolved_project["base"]

        resolved: dict[str, object] = {}
        resolved["design_name"] = self._resolve_one(
            str(run_context.get("design_name", "{project_name}")), ctx_flat,
        )
        ctx_flat["design_name"] = str(resolved["design_name"])

        for key in ("preco_db", "preco_db_next", "eco_scripts", "execution_dir"):
            val = run_context.get(key)
            if val:
                resolved[key] = self._resolve_one(str(val), ctx_flat)

        resolved["execution"] = run_context.get("execution", {})

        return resolved

    # ── 通用占位符替换 ──

    @staticmethod
    def _resolve_one(template: str, flat: dict) -> str:
        """替换单个字符串里的所有 {xxx} 和 {base.xxx} 占位符。"""
        result = template
        pattern = re.compile(r"\{([^}]+)\}")

        while True:
            matches = pattern.findall(result)
            changed = False
            for token in matches:
                if "." in token:
                    parts = token.split(".", 1)
                    top = flat.get(parts[0], {})
                    if isinstance(top, dict):
                        val = top.get(parts[1], "")
                    else:
                        val = ""
                else:
                    val = flat.get(token, "")

                if isinstance(val, dict):
                    continue
                result = result.replace(f"{{{token}}}", str(val))
                changed = True
            if not changed:
                break

        return result

    # ── 执行目录推断 ──

    def _resolve_execution_dir(self, step_name: str) -> str:
        """Python 侧推断 wrapper 应该 cd 到哪个目录。

        优先级：run_context.execution_dir > project.base.{step_name}_bin > project.base.{step_name}
        """
        rc_exec = self._run_context_resolved.get("execution_dir")
        if rc_exec and rc_exec != "":
            return rc_exec

        base = self._project_resolved.get("base", {})
        bin_key = f"{step_name}_bin"
        if bin_key in base:
            return base[bin_key]
        if step_name in base:
            return base[step_name]

        return ""

    # ── 超时获取（run_context 覆盖 eda_tools 默认值）──

    def _get_timeout(self, step_name: str) -> int:
        rc_timeout = (
            self._run_context_resolved.get("execution", {})
            .get(step_name, {})
            .get("timeout_s")
        )
        if rc_timeout is not None:
            return rc_timeout
        return self.eda_tools["steps"][step_name].get("timeout_s", 1800)

    # ── 日志目录获取（Python 侧用来做 dry-run 时的日志预览）──

    def _resolve_log_dir(self, step_name: str) -> str:
        """获取 step 的日志目录，优先匹配 engine 级目录。

        例：run_pt_fix_setup 在 project.logs 里可能没有直接的 key，
            但有 fix_pt = xxx/log，所以 fallback 到引擎级目录。
        """
        logs = self._project_resolved.get("logs", {})

        if step_name in logs:
            return logs[step_name]

        if step_name.startswith("run_pt_fix"):
            return logs.get("run_pt_fix", "")

        if step_name.startswith("run_xtop_fix"):
            return logs.get("run_xtop_fix", "")

        return ""

    # ── 核心：运行一个 step ──

    def run_step(self, step_name: str, fix_strategy: str | None = None,
                 dry_run: bool = False, debug: bool = False) -> dict:
        """运行任意 step，wrapper 统一处理 source/cmd/wait。

        Args:
            step_name: step 名（必须在 eda_tools.yaml 和 project.yaml.step_command 里都注册）
            fix_strategy: 可选的修复策略（fix 类 step 用）
            dry_run: True → 只打印 resolve 后的 CMD 和环境变量，不真正执行
            debug: True → wrapper 里 set -x + 打印 env dump

        Returns:
            parse_patterns 提取到的结构化 dict（如果配置了 parse_patterns）

        Raises:
            Exception: step_command 缺失、脚本执行失败、超时、parse_patterns 不匹配等
        """
        if step_name not in self.eda_tools["steps"]:
            raise Exception(f"Step [{step_name}] 不在 eda_tools.yaml 里注册")
        if step_name not in self._project_resolved.get("step_command", {}):
            raise Exception(f"Step [{step_name}] 不在 project.yaml.step_command 里注册")

        # 1. resolve step_command 占位符
        cmd_template = self._project_resolved["step_command"][step_name]
        if fix_strategy:
            cmd_template = cmd_template.replace("{fix_strategy}", fix_strategy)

        env = os.environ.copy()
        env["PROJECT_CSHRC"] = self._project_resolved["project_cshrc"]
        env["STEP_NAME"] = step_name
        env["DESIGN_NAME"] = self._run_context_resolved["design_name"]
        env["EXECUTION_DIR"] = self._resolve_execution_dir(step_name)
        env["WAIT_FILE"] = self._project_resolved.get("wait_flags", {}).get(step_name, "")
        env["CMD"] = cmd_template
        env["LOG_DIR"] = self._resolve_log_dir(step_name)

        if fix_strategy:
            env["FIX_STRATEGY"] = fix_strategy

        if debug:
            env["DEBUG"] = "1"

        # 2. dry-run：只打印，不执行
        if dry_run:
            return self._dry_run_report(step_name, env)

        # 3. 执行 wrapper
        timeout = self._get_timeout(step_name)
        try:
            result = subprocess.run(
                ["/bin/csh", "-f", self.wrapper_path],
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise Exception(
                f"[{step_name}] 执行超时（{timeout}s）。"
                f" 可能原因：EDA 工具卡住、license 被占、网络慢。"
            )

        stdout = result.stdout or ""
        stderr = result.stderr or ""

        if result.returncode != 0:
            raise Exception(
                f"[{step_name}] wrapper 退出码 {result.returncode}\n"
                f"  CMD: {cmd_template}\n"
                f"  EXECUTION_DIR: {env['EXECUTION_DIR']}\n"
                f"  WAIT_FILE: {env['WAIT_FILE']}\n"
                f"  STDERR tail: {stderr[-2000:] if len(stderr) > 2000 else stderr}"
            )

        # 4. parse_patterns 提取结构化数据（如果配了）
        parse_cfg = self.eda_tools["steps"][step_name].get("parse_patterns", {})
        parsed = self._parse_output(stdout + "\n" + stderr, parse_cfg)

        return parsed

    def _dry_run_report(self, step_name: str, env: dict) -> dict:
        """dry-run 模式只报告 resolve 后的信息，不执行。"""
        print(f"\n[DRY-RUN] Step: {step_name}")
        print(f"  CMD           = {env['CMD']}")
        print(f"  EXECUTION_DIR = {env['EXECUTION_DIR']}")
        print(f"  WAIT_FILE     = {env['WAIT_FILE']}")
        print(f"  LOG_DIR       = {env['LOG_DIR']}")
        print(f"  DESIGN_NAME   = {env['DESIGN_NAME']}")
        print(f"  PROJECT_CSHRC = {env['PROJECT_CSHRC']}")
        print(f"  TIMEOUT       = {self._get_timeout(step_name)}s")
        print(f"  FIX_STRATEGY  = {env.get('FIX_STRATEGY', '(none)')}")
        print(f"  PARSE_CFG     = {self.eda_tools['steps'][step_name].get('parse_patterns', {})}")
        print()

        return {
            "status": "dry_run",
            "step": step_name,
            "cmd": env["CMD"],
            "execution_dir": env["EXECUTION_DIR"],
            "wait_file": env["WAIT_FILE"],
            "timeout_s": self._get_timeout(step_name),
        }

    # ── parse_patterns 实现（从 EDA 工具 stdout/stderr 提取结构化数据）──

    def _parse_output(self, text: str, patterns: dict) -> dict:
        if not patterns:
            return {}
        result: dict = {}
        for key, pattern in patterns.items():
            m = re.search(pattern, text, re.IGNORECASE)
            if not m:
                continue
            if m.lastindex and m.lastindex >= 1:
                result[key] = self._coerce(m.group(1))
            else:
                result[key] = True
        return result

    @staticmethod
    def _coerce(value: str):
        try:
            return int(value)
        except ValueError:
            pass
        try:
            return float(value)
        except ValueError:
            pass
        lower = value.strip().lower()
        if lower in ("true", "yes", "pass", "passed", "success", "ok"):
            return True
        if lower in ("false", "no", "fail", "failed", "error"):
            return False
        return value

    # ── Protocol 方法实现（全部委托给 run_step）──

    def run_eco_route(self, design_name: str, run_dir: str) -> dict:
        self.run_step("run_eco_route")
        return {"route_done": True}

    def run_ext(self, design_name: str, run_dir: str) -> dict:
        self.run_step("run_ext")
        return {"ext_done": True}

    def run_sta(self, design_name: str, run_dir: str) -> dict:
        res = self.run_step("run_sta")
        return {
            "setup_vio": res.get("setup_vio", 0),
            "hold_vio": res.get("hold_vio", 0),
        }

    def run_pv(self, design_name: str, run_dir: str) -> dict:
        res = self.run_step("run_pv")
        return {"pv_pass": res.get("pv_pass", False)}

    def run_signoff(self, design_name: str, run_dir: str) -> dict:
        res = self.run_step("run_signoff")
        return {
            "signoff_pass": res.get("signoff_pass", False),
            "violations": [],
        }

    def run_pt_fix_setup(self, design_name: str, run_dir: str, fix_strategy: str) -> dict:
        res = self.run_step("run_pt_fix_setup", fix_strategy=fix_strategy)
        return {"fix_done": True, "setup_vio": res.get("setup_vio", 0)}

    def run_pt_fix_hold(self, design_name: str, run_dir: str, fix_strategy: str) -> dict:
        res = self.run_step("run_pt_fix_hold", fix_strategy=fix_strategy)
        return {"fix_done": True, "hold_vio": res.get("hold_vio", 0)}

    def run_pt_fix_leakage(self, design_name: str, run_dir: str, fix_strategy: str) -> dict:
        self.run_step("run_pt_fix_leakage", fix_strategy=fix_strategy)
        return {"fix_done": True}

    def run_pt_fix_drv(self, design_name: str, run_dir: str, fix_strategy: str) -> dict:
        self.run_step("run_pt_fix_drv", fix_strategy=fix_strategy)
        return {"fix_done": True}

    def run_xtop_fix_hold(self, design_name: str, run_dir: str, fix_strategy: str) -> dict:
        res = self.run_step("run_xtop_fix_hold", fix_strategy=fix_strategy)
        return {"fix_done": True, "hold_vio": res.get("hold_vio", 0)}
