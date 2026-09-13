from __future__ import annotations

import os
import shutil


def ensure_dir(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except OSError:
        return False


def check_write_permission(path: str) -> bool:
    if not os.path.exists(path):
        parent = os.path.dirname(path) or "."
        return os.access(parent, os.W_OK)
    return os.access(path, os.W_OK)


def check_read_permission(path: str) -> bool:
    return os.access(path, os.R_OK)


def build_run_dir(base_dir: str, design_name: str, iteration_cnt: int) -> str:
    return os.path.join(base_dir, design_name, f"iter_{iteration_cnt:03d}")


def build_log_path(run_dir: str, step_name: str) -> str:
    return os.path.join(run_dir, f"{step_name}.log")


def build_report_path(run_dir: str, step_name: str) -> str:
    return os.path.join(run_dir, f"{step_name}.rpt")


NETLIST_EXTS = (".v", ".sv", ".vhd", ".vhdl", ".gds")
SDC_EXTS = (".sdc", ".tcl")
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules"}


def find_design_files(design_dir: str) -> dict:
    found = {"netlist": [], "sdc": []}
    if not design_dir or not os.path.isdir(design_dir):
        return found

    for dirpath, dirnames, filenames in os.walk(design_dir):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fname in filenames:
            lower = fname.lower()
            full = os.path.join(dirpath, fname)
            if lower.endswith(NETLIST_EXTS):
                found["netlist"].append(full)
            elif lower.endswith(SDC_EXTS) and "sdc" in lower:
                found["sdc"].append(full)

    found["netlist"].sort()
    found["sdc"].sort()
    return found


def check_tool_available(tool_name: str) -> bool:
    return shutil.which(tool_name) is not None


def check_env_vars(required_env: list[str]) -> dict:
    missing = []
    for var in required_env:
        if not os.environ.get(var):
            missing.append(var)
    return {"missing": missing, "present": len(required_env) - len(missing), "total": len(required_env)}


def validate_design_dir(design_dir: str, require_netlist: bool = True) -> dict:
    result = {
        "exists": False,
        "netlist_count": 0,
        "sdc_count": 0,
        "netlist_paths": [],
        "sdc_paths": [],
        "errors": [],
        "ok": False,
    }

    if not design_dir:
        result["errors"].append("design_dir 未指定")
        return result

    if not os.path.isdir(design_dir):
        result["errors"].append(f"design_dir 不存在: {design_dir}")
        return result

    result["exists"] = True

    files = find_design_files(design_dir)
    result["netlist_count"] = len(files["netlist"])
    result["sdc_count"] = len(files["sdc"])
    result["netlist_paths"] = files["netlist"]
    result["sdc_paths"] = files["sdc"]

    if require_netlist and result["netlist_count"] == 0:
        result["errors"].append(
            f"在 {design_dir} 下未找到网表文件 (.v/.sv/.vhd/.gds)"
        )

    result["ok"] = len(result["errors"]) == 0
    return result
