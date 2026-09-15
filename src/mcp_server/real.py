from __future__ import annotations

import os
import re
import subprocess
import tempfile

import yaml

from src.mcp_server.parsers import parse_output
from src.mcp_server.protocol import STEP_NAMES, ECOMCPServer

# project.yaml.timeout_s 未配置某 step 时的兜底值，防止 subprocess 永久挂死
DEFAULT_TIMEOUT_S = 3600

# csh 可执行文件路径：EDA 机器通常是 /bin/csh（RHEL 上一般软链 tcsh）；
# 只有 /usr/bin/csh 或想用 tcsh 时，用环境变量 ECO_CSH_BIN 覆盖。
DEFAULT_CSH_BIN = "/bin/csh"


class RealECOMCPServer(ECOMCPServer):
    """真实 EDA 工具调用实现。

    配置（2 个 yaml，按生命周期分层）：
        config/project.yaml     项目级：project_name / base / logs / wait_flags /
                                step_command（str 或 list）/ timeout_s
        config/run_context.yaml 运行级：design_name / execution_dir / 输入输出文件

    resolve 顺序：
        design_name（只依赖 project_name）
        → project.yaml 多轮迭代 resolve（step_command 里可以用 {design_name}）
        → run_context.yaml resolve

    执行模型：
        每个 step → 取 step_command（str 归一化成 1 条，list 保持多条）
        → resolve 占位符 / 替换 {fix_strategy} → 逐行写临时命令文件
        → subprocess 调 run_step_wrapper.csh（CMDS_FILE 环境变量传文件路径）
        → wrapper 逐行 eval、fail-fast，全部成功后 pds_wait_files
        → Python 侧 parsers.parse_output 提取 violations
    """

    WRAPPER_SCRIPT = "scripts/eda/run_step_wrapper.csh"

    def __init__(
        self,
        project_path: str = "config/project.yaml",
        run_context_path: str = "config/run_context.yaml",
        wrapper_path: str | None = None,
    ):
        self.project = self._load_yaml(project_path)
        self.run_context = self._load_yaml(run_context_path)
        self.wrapper_path = wrapper_path or self.WRAPPER_SCRIPT

        # 1. design_name 只依赖 project_name，先解析出来，
        #    这样 project.yaml.step_command 里的 {design_name} 才能正确展开
        self._design_name = self._resolve_one(
            str(self.run_context.get("design_name", "{project_name}")),
            {"project_name": str(self.project.get("project_name", ""))},
        )

        # 2. resolve project.yaml（多轮迭代）
        self._project_resolved = self._resolve_project(self.project, self._design_name)

        # 3. resolve run_context.yaml
        self._run_context_resolved = self._resolve_run_context(
            self.run_context, self._project_resolved, self._design_name,
        )

        # 4. 启动期校验：配置里的 step 名必须是协议注册过的
        self._validate_step_commands()

    @staticmethod
    def _load_yaml(path: str) -> dict:
        with open(path) as f:
            return yaml.safe_load(f)

    # ── 启动期校验 ──

    def _validate_step_commands(self) -> None:
        commands = self._project_resolved.get("step_command", {})

        unknown = set(commands) - set(STEP_NAMES)
        if unknown:
            raise Exception(
                f"project.yaml.step_command 里有未注册的 step: {sorted(unknown)}；"
                f"合法 step: {list(STEP_NAMES)}"
            )

        for step_name, cmd in commands.items():
            if isinstance(cmd, list):
                if not cmd or any(not str(item).strip() for item in cmd):
                    raise Exception(
                        f"project.yaml.step_command.{step_name} 是空 list 或含空命令"
                    )
            elif not str(cmd).strip():
                raise Exception(f"project.yaml.step_command.{step_name} 是空字符串")

        timeouts = self._project_resolved.get("timeout_s", {})
        for step_name, val in timeouts.items():
            if not isinstance(val, int) or val <= 0:
                raise Exception(
                    f"project.yaml.timeout_s.{step_name} 必须是正整数秒，实际为: {val!r}"
                )

    # ── 占位符 resolve（project.yaml 内部）──

    def _resolve_project(self, project: dict, design_name: str) -> dict:
        """Resolve project.yaml 内部的所有占位符引用（多轮迭代直到稳定）。"""
        flat: dict[str, object] = {}
        flat["project_name"] = str(project.get("project_name", ""))
        flat["work_dir"] = str(project.get("work_dir", ""))
        flat["project_cshrc"] = str(project.get("project_cshrc", ""))
        flat["design_name"] = design_name
        # {fix_strategy} 运行时才知道，让它在 init 阶段保持原样（自引用不展开）
        flat["fix_strategy"] = "{fix_strategy}"

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
                flat["base"][k] = base[k]  # type: ignore[index]

            for k, v in list(logs.items()):
                new_val = self._resolve_one(str(v), flat)
                if new_val != str(v):
                    changed = True
                    logs[k] = new_val
                flat["logs"][k] = logs[k]  # type: ignore[index]

            for k, v in list(wait_flags.items()):
                new_val = self._resolve_one(str(v), flat)
                if new_val != str(v):
                    changed = True
                    wait_flags[k] = new_val
                flat["wait_flags"][k] = wait_flags[k]  # type: ignore[index]

            # step_command 的值可能是 str（单条）或 list（多条）
            for k, v in list(step_command.items()):
                if isinstance(v, list):
                    new_val = [self._resolve_one(str(item), flat) for item in v]
                    if new_val != [str(item) for item in v]:
                        changed = True
                else:
                    new_val = self._resolve_one(str(v), flat)
                    if new_val != str(v):
                        changed = True
                step_command[k] = new_val
                flat["step_command"][k] = new_val  # type: ignore[index]

            if not changed:
                break

        flat["timeout_s"] = dict(project.get("timeout_s", {}))
        return flat  # type: ignore[return-value]

    # ── 占位符 resolve（run_context.yaml，引用 project 解析后的值）──

    def _resolve_run_context(
        self, run_context: dict, resolved_project: dict, design_name: str,
    ) -> dict:
        """Resolve run_context.yaml 里的 {project_name}, {base.xxx} 等占位符。"""
        ctx_flat: dict[str, object] = {}
        ctx_flat["project_name"] = resolved_project["project_name"]
        ctx_flat["work_dir"] = resolved_project["work_dir"]
        ctx_flat["base"] = resolved_project["base"]
        ctx_flat["design_name"] = design_name

        resolved: dict[str, object] = {"design_name": design_name}

        for key in ("preco_db", "preco_db_next", "eco_scripts", "execution_dir"):
            val = run_context.get(key)
            if val:
                resolved[key] = self._resolve_one(str(val), ctx_flat)

        return resolved

    # ── 通用占位符替换 ──

    @staticmethod
    def _resolve_one(template: str, flat: dict) -> str:
        """替换单个字符串里的所有 {xxx} 和 {base.xxx} 占位符。

        未知占位符替换为空字符串；自引用（值就是 {token} 本身）保持原样，
        用于 {fix_strategy} 这类运行时才替换的 token。
        """
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
                str_val = str(val)
                if str_val == f"{{{token}}}":
                    # 自引用（如 fix_strategy="{fix_strategy}"），保持原样不展开
                    continue
                result = result.replace(f"{{{token}}}", str_val)
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
            return str(rc_exec)

        base = self._project_resolved.get("base", {})
        bin_key = f"{step_name}_bin"
        if bin_key in base:
            return base[bin_key]
        if step_name in base:
            return base[step_name]

        return ""

    # ── 超时获取（project.yaml 唯一来源，未配置走兜底默认值）──

    def _get_timeout(self, step_name: str) -> int:
        val = self._project_resolved.get("timeout_s", {}).get(step_name)
        if val is None:
            return DEFAULT_TIMEOUT_S
        return int(val)

    # ── 日志目录获取（引擎级 fallback）──

    def _resolve_log_dir(self, step_name: str) -> str:
        """获取 step 的日志目录，优先匹配 engine 级目录。

        例：run_pt_fix_setup 在 project.logs 里可能没有直接的 key，
            但有 run_pt_fix = xxx/log，所以 fallback 到引擎级目录。
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
        """运行任意 step，wrapper 统一处理 source/逐行命令/wait。

        Args:
            step_name: step 名（必须在 project.yaml.step_command 里注册）
            fix_strategy: 可选的修复策略（替换命令里的 {fix_strategy}）
            dry_run: True → 只打印 resolve 后的命令和环境变量，不真正执行
            debug: True → wrapper 里 set -x + 打印 env dump

        Returns:
            parsers.parse_output 提取到的结构化 dict（该 step 没配解析规则时为 {}）

        Raises:
            Exception: step 未注册、命令里残留未解析占位符、执行超时、wrapper 非零退出
        """
        if step_name not in STEP_NAMES:
            raise Exception(f"Step [{step_name}] 不是合法 step，合法值: {list(STEP_NAMES)}")
        if step_name not in self._project_resolved.get("step_command", {}):
            raise Exception(f"Step [{step_name}] 未在 project.yaml.step_command 里配置命令")

        # 1. 归一化成命令 list（str → 1 条；list → 多条按序执行）
        raw_cmd = self._project_resolved["step_command"][step_name]
        commands = list(raw_cmd) if isinstance(raw_cmd, list) else [raw_cmd]

        if fix_strategy:
            commands = [c.replace("{fix_strategy}", fix_strategy) for c in commands]

        # 命令里不允许残留任何未解析的 {占位符}
        for idx, cmd in enumerate(commands, start=1):
            if re.search(r"\{[^}]*\}", cmd):
                raise Exception(
                    f"[{step_name}] 第 {idx} 条命令存在未解析的占位符: {cmd!r}；"
                    f"请检查 project.yaml / run_context.yaml"
                )

        env = self._build_env(step_name, commands, fix_strategy, debug)

        # 2. dry-run：只打印，不执行，也不生成命令文件
        if dry_run:
            return self._dry_run_report(step_name, commands, env)

        # 3. 逐行写临时命令文件，wrapper 通过 CMDS_FILE 读取
        timeout = self._get_timeout(step_name)
        cmds_file = tempfile.NamedTemporaryFile(
            mode="w",
            prefix=f"eco_{step_name}_",
            suffix=".cmds",
            delete=False,
            dir=tempfile.gettempdir(),
        )
        try:
            cmds_file.write("\n".join(commands) + "\n")
            cmds_file.close()
            env["CMDS_FILE"] = cmds_file.name

            try:
                result = subprocess.run(
                    [os.environ.get("ECO_CSH_BIN", DEFAULT_CSH_BIN), "-f", self.wrapper_path],
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
                    f"  COMMANDS ({len(commands)} 条):\n"
                    + "".join(f"    {i}. {c}\n" for i, c in enumerate(commands, start=1))
                    + f"  EXECUTION_DIR: {env['EXECUTION_DIR']}\n"
                    f"  WAIT_FILE: {env['WAIT_FILE']}\n"
                    f"  STDOUT tail: {stdout[-2000:] if len(stdout) > 2000 else stdout}\n"
                    f"  STDERR tail: {stderr[-2000:] if len(stderr) > 2000 else stderr}"
                )
        finally:
            self._cleanup_cmd_files(cmds_file.name)

        # 4. 内置 parse patterns 提取结构化数据
        return parse_output(step_name, stdout + "\n" + stderr)

    def _build_env(
        self, step_name: str, commands: list[str],
        fix_strategy: str | None, debug: bool,
    ) -> dict:
        """组装传给 wrapper 的环境变量（继承当前进程环境，保留 IT 托底的 PATH 等）。"""
        env = os.environ.copy()
        env["PROJECT_CSHRC"] = self._project_resolved["project_cshrc"]
        env["STEP_NAME"] = step_name
        env["DESIGN_NAME"] = self._run_context_resolved["design_name"]
        env["EXECUTION_DIR"] = self._resolve_execution_dir(step_name)
        env["WAIT_FILE"] = self._project_resolved.get("wait_flags", {}).get(step_name, "")
        env["LOG_DIR"] = self._resolve_log_dir(step_name)

        if fix_strategy:
            env["FIX_STRATEGY"] = fix_strategy

        if debug:
            env["DEBUG"] = "1"

        return env

    @staticmethod
    def _cleanup_cmd_files(cmds_file: str) -> None:
        """删除临时命令文件及 wrapper 可能遗留的 status 文件。"""
        for path in (cmds_file, f"{cmds_file}.status"):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def _dry_run_report(self, step_name: str, commands: list[str], env: dict) -> dict:
        """dry-run 模式只报告 resolve 后的信息，不执行。"""
        from src.mcp_server.parsers import STEP_PARSE_PATTERNS

        print(f"\n[DRY-RUN] Step: {step_name}")
        print(f"  COMMANDS ({len(commands)} 条):")
        for i, cmd in enumerate(commands, start=1):
            print(f"    {i}. {cmd}")
        print(f"  EXECUTION_DIR = {env['EXECUTION_DIR']}")
        print(f"  WAIT_FILE     = {env['WAIT_FILE']}")
        print(f"  LOG_DIR       = {env['LOG_DIR']}")
        print(f"  DESIGN_NAME   = {env['DESIGN_NAME']}")
        print(f"  PROJECT_CSHRC = {env['PROJECT_CSHRC']}")
        print(f"  TIMEOUT       = {self._get_timeout(step_name)}s")
        print(f"  FIX_STRATEGY  = {env.get('FIX_STRATEGY', '(none)')}")
        print(f"  PARSE_PATTERNS = {STEP_PARSE_PATTERNS.get(step_name, {})}")
        print()

        return {
            "status": "dry_run",
            "step": step_name,
            "commands": commands,
            "execution_dir": env["EXECUTION_DIR"],
            "wait_file": env["WAIT_FILE"],
            "timeout_s": self._get_timeout(step_name),
        }

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
