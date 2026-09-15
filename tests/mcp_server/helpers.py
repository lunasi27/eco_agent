"""mcp_server 测试公共工具：在 tmp_path 下构造 project.yaml + run_context.yaml。"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER_PATH = REPO_ROOT / "scripts" / "eda" / "run_step_wrapper.csh"
FIXTURE_SCRIPTS = REPO_ROOT / "tests" / "fixtures" / "scripts"


def resolve_csh_bin() -> str | None:
    """返回可用的 csh 路径（ECO_CSH_BIN 覆盖，默认 /bin/csh），不存在则 None。"""
    candidate = os.environ.get("ECO_CSH_BIN", "/bin/csh")
    if Path(candidate).is_file() and os.access(candidate, os.X_OK):
        return candidate
    return None


def write_real_configs(
    base_dir: Path,
    *,
    step_command: dict,
    timeout_s: dict | None = None,
    logs: dict | None = None,
    wait_flags: dict | None = None,
    design_name: str | None = None,
    project_name: str = "TestDesign",
) -> tuple[Path, Path]:
    """在 base_dir 下写一套最小可用的 project.yaml + run_context.yaml。"""
    project: dict = {
        "project_name": project_name,
        "work_dir": str(base_dir),
        "project_cshrc": "",
        "base": {
            "run_eco_route": "{work_dir}/apr",
            "run_ext": "{work_dir}/ext",
            "run_sta_bin": "{work_dir}/sta_bin",
            "run_sta_rpt": "{work_dir}/sta_rpt",
            "run_pv": "{work_dir}/pv",
            "run_signoff": "{base.run_eco_route}",
            "run_pt_fix": "{work_dir}/pteco",
            "run_xtop_fix": "{work_dir}/xtop",
            "dataout": "{work_dir}/out",
        },
        "logs": logs or {},
        "wait_flags": wait_flags or {},
        "step_command": step_command,
    }
    if timeout_s is not None:
        project["timeout_s"] = timeout_s

    run_context = {
        "design_name": design_name or "{project_name}",
        "execution_dir": "{work_dir}",
        "preco_db": "{base.run_eco_route}/DB/{design_name}.enc",
        "preco_db_next": "{base.run_eco_route}/DB/{design_name}_next.enc",
        "eco_scripts": "{base.run_pt_fix}/output/setup.tcl",
    }

    project_path = base_dir / "project.yaml"
    run_context_path = base_dir / "run_context.yaml"
    project_path.write_text(yaml.safe_dump(project, sort_keys=False))
    run_context_path.write_text(yaml.safe_dump(run_context, sort_keys=False))
    return project_path, run_context_path
