from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import yaml

from src.mcp_server.protocol import ECOMCPServer


class RealECOMCPServer(ECOMCPServer):
    """真实 EDA 工具调用实现。

    通过 subprocess 调用 cshell 脚本，所有 EDA 参数通过环境变量注入。
    从 config/eda_tools.yaml 读取 step 配置和日志解析规则。
    """

    def __init__(self, config_path: str = "config/eda_tools.yaml"):
        self._config = self._load_config(config_path)
        self._common_env = os.environ.copy()
        common = self._config.get("common_env", {})
        self._common_env.update(self._resolve_env_dict(common, None, None, None))

    @staticmethod
    def _load_config(path: str) -> dict:
        with open(path) as f:
            return yaml.safe_load(f)

    def _resolve_env_dict(
        self, env: dict, design_name: str | None, run_dir: str | None,
        fix_strategy: str | None = None,
    ) -> dict[str, str]:
        result = {}
        for key, val in env.items():
            if isinstance(val, str):
                val = val.replace("{design_name}", design_name or "")
                val = val.replace("{run_dir}", run_dir or "")
                val = val.replace("{fix_strategy}", fix_strategy or "")
                val = val.replace("{pdk_dir}", self._common_env.get("PDK_DIR", ""))
            result[key] = val
        return result

    def _run_script(
        self,
        step_name: str,
        design_name: str,
        run_dir: str,
        fix_strategy: str | None = None,
    ) -> dict:
        step_cfg = self._config["steps"][step_name]
        script = step_cfg["script"]
        timeout = step_cfg.get("timeout_s", 1800)

        os.makedirs(run_dir, exist_ok=True)

        env = self._common_env.copy()
        env.update({"DESIGN_NAME": design_name, "RUN_DIR": run_dir})
        env.update(self._resolve_env_dict(
            step_cfg.get("env", {}),
            design_name, run_dir, fix_strategy,
        ))
        if fix_strategy:
            env["FIX_STRATEGY"] = fix_strategy

        log_path = os.path.join(run_dir, f"{step_name}.log")

        try:
            proc = subprocess.run(
                ["csh", "-f", script],
                env=env,
                timeout=timeout,
                capture_output=True,
                text=True,
                cwd=run_dir,
            )
        except subprocess.TimeoutExpired:
            raise Exception(f"{step_name} 执行超时（{timeout}s）")

        with open(log_path, "w") as f:
            f.write(proc.stdout)
            if proc.stderr:
                f.write("\n--- STDERR ---\n")
                f.write(proc.stderr)

        if proc.returncode != 0:
            raise Exception(
                f"{step_name} cshell 脚本退出码 {proc.returncode}，日志: {log_path}"
            )

        return self._parse_log(log_path, step_cfg.get("parse_patterns", {}))

    def _parse_log(self, log_path: str, patterns: dict) -> dict:
        if not patterns:
            return {}
        text = Path(log_path).read_text()
        result = {}
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
        if lower in ("true", "yes", "pass", "passed"):
            return True
        if lower in ("false", "no", "fail", "failed"):
            return False
        return value

    # ── Protocol 方法实现 ──

    def run_eco_route(self, design_name: str, run_dir: str) -> dict:
        self._run_script("run_eco_route", design_name, run_dir)
        return {"route_done": True}

    def run_ext(self, design_name: str, run_dir: str) -> dict:
        self._run_script("run_ext", design_name, run_dir)
        return {"ext_done": True}

    def run_sta(self, design_name: str, run_dir: str) -> dict:
        res = self._run_script("run_sta", design_name, run_dir)
        return {
            "setup_vio": res.get("setup_vio", 0),
            "hold_vio": res.get("hold_vio", 0),
        }

    def run_pv(self, design_name: str, run_dir: str) -> dict:
        res = self._run_script("run_pv", design_name, run_dir)
        return {"pv_pass": res.get("pv_pass", False)}

    def run_signoff(self, design_name: str, run_dir: str) -> dict:
        res = self._run_script("run_signoff", design_name, run_dir)
        return {
            "signoff_pass": res.get("signoff_pass", False),
            "violations": [],
        }

    def run_fix_setup(self, design_name: str, run_dir: str, fix_strategy: str) -> dict:
        res = self._run_script("run_fix_setup", design_name, run_dir, fix_strategy)
        return {"fix_done": True, "setup_vio": res.get("setup_vio", 0)}

    def run_fix_hold(self, design_name: str, run_dir: str, fix_strategy: str) -> dict:
        res = self._run_script("run_fix_hold", design_name, run_dir, fix_strategy)
        return {"fix_done": True, "hold_vio": res.get("hold_vio", 0)}

    def run_fix_leakage(self, design_name: str, run_dir: str, fix_strategy: str) -> dict:
        self._run_script("run_fix_leakage", design_name, run_dir, fix_strategy)
        return {"fix_done": True}
