from __future__ import annotations

import os
from typing import TYPE_CHECKING

from langgraph.types import interrupt

from src.state import ECOState
from src.utils.constants import BASE_RUN_DIR, INITIAL_PHASE_STATUS
from src.utils.file_utils import (
    build_run_dir,
    check_env_vars,
    check_tool_available,
    ensure_dir,
    validate_design_dir,
)

if TYPE_CHECKING:
    from src.mcp_server.protocol import ECOMCPServer


DEFAULT_REQUIRED_ENV = [
    "EDA_LICENSE_FILE",
]

DEFAULT_REQUIRED_TOOLS = []


def make_init_node(
    mcp_server: ECOMCPServer | None = None,
    require_design_dir: bool = False,
    require_netlist: bool = False,
    require_sdc: bool = False,
    required_env_vars: list[str] | None = None,
    required_tools: list[str] | None = None,
):
    required_env_vars = required_env_vars or []
    required_tools = required_tools or []

    def _error(phase_status: dict, step_status: dict, error_msg: str) -> dict:
        return {
            "current_phase": "init",
            "current_step": "init",
            "phase_status": {**phase_status, "init": "error"},
            "step_status": {**step_status, "init": "error"},
            "error_msg": f"[init] {error_msg}",
        }

    def node_init(state: ECOState) -> dict:
        design_name = state.get("design_name", "")

        if not design_name:
            interrupt_msg = "请提供 design_name（必填），可输入如：'帮我跑 designA 的 ECO'"
            resume_val = interrupt(interrupt_msg)
            design_name = resume_val.strip()

        iteration_cnt = state.get("iteration_cnt", 1)
        if iteration_cnt == 0:
            iteration_cnt = 1

        design_dir = state.get("design_dir", "")

        phase_status = {**INITIAL_PHASE_STATUS}
        step_status = {}

        init_errors = []

        if require_design_dir:
            if not design_dir:
                init_errors.append("design_dir 未提供（require_design_dir=True）")
            else:
                result = validate_design_dir(
                    design_dir,
                    require_netlist=require_netlist,
                )
                if not result["ok"]:
                    init_errors.extend(result["errors"])
                if require_sdc and result["sdc_count"] == 0:
                    init_errors.append(
                        f"design_dir {design_dir} 下未找到 SDC 约束文件"
                    )

        env_info = check_env_vars(required_env_vars)
        if env_info["missing"]:
            init_errors.append(
                f"缺失 {len(env_info['missing'])} 个必要环境变量: {env_info['missing']}"
            )

        if required_tools:
            missing_tools = [
                t for t in required_tools if not check_tool_available(t)
            ]
            if missing_tools:
                init_errors.append(
                    f"缺失 {len(missing_tools)} 个 EDA 工具: {missing_tools}"
                )

        run_dir = build_run_dir(BASE_RUN_DIR, design_name, iteration_cnt)

        if not ensure_dir(run_dir):
            init_errors.append(f"无法创建运行目录 {run_dir}")

        if init_errors:
            summary = "; ".join(init_errors)
            return _error(phase_status, step_status, summary)

        prev_setup_vio = state.get("prev_setup_vio", 0)
        prev_hold_vio = state.get("prev_hold_vio", 0)

        if iteration_cnt == 1:
            prev_setup_vio = 0
            prev_hold_vio = 0

        result = {
            "design_name": design_name,
            "run_dir": run_dir,
            "iteration_cnt": iteration_cnt,
            "current_phase": "init",
            "current_step": "init",
            "phase_status": {**phase_status, "init": "done"},
            "step_status": {"init": "done"},
            "prev_setup_vio": prev_setup_vio,
            "prev_hold_vio": prev_hold_vio,
            "interrupt_msg": "",
            "error_msg": "",
        }

        # 首轮迭代（新 ECO 任务）时清空上一轮的收敛历史
        if iteration_cnt == 1:
            result["iteration_history"] = []
            result["invalid_input"] = ""

        if design_dir:
            result["design_dir"] = design_dir

        return result

    return node_init
