from __future__ import annotations

from typing import Literal

from langgraph.types import Send

from src.state import ECOState
from src.utils.constants import PHASE_STEPS


def route_after_init(state: ECOState) -> Literal["error_handler", "run_eco_route"]:
    if state.get("phase_status", {}).get("init") == "error":
        return "error_handler"
    if state.get("step_status", {}).get("init") == "error":
        return "error_handler"
    return "run_eco_route"


def route_after_run_eco_route(state: ECOState) -> Literal["error_handler", "run_ext"]:
    if state.get("step_status", {}).get("run_eco_route") == "error":
        return "error_handler"
    return "run_ext"


_PHASE2_SHARED_KEYS = (
    "design_name", "design_dir", "run_dir",
    "iteration_cnt", "prev_setup_vio", "prev_hold_vio",
    "iteration_history", "step_elapsed",
)


def make_route_after_run_ext(p2_steps: tuple[str, ...]):
    """
    构图时由 build_graph 调用，闭包捕获 p2_steps。
    运行时根据闭包里固定的 step 列表生成 Send 并行派发。
    """

    def route(state: ECOState):
        if state.get("step_status", {}).get("run_ext") == "error":
            return "error_handler"
        shared = {k: state[k] for k in _PHASE2_SHARED_KEYS if k in state}
        return [Send(name, shared) for name in p2_steps]

    return route


def route_after_run_ext(state: ECOState):
    """向后兼容：硬编码默认 Phase2 step 列表。"""
    return make_route_after_run_ext(PHASE_STEPS["phase2"])(state)


def route_after_phase2_gate(
    state: ECOState,
) -> Literal["error_handler", "phase2_summary"]:
    if state.get("phase_status", {}).get("phase2") == "error":
        return "error_handler"
    return "phase2_summary"


def make_route_after_phase2_summary(
    p2_steps: tuple[str, ...],
    p3_steps: tuple[str, ...],
):
    """
    构图时由 build_graph 调用，闭包捕获 p2_steps 和 p3_steps。
    检查 Phase2 哪些 step 有 error → 路由到 error_handler。
    否则根据 user_fix_strategy 路由到对应的 Phase3 fix step。
    只在 p3_router == "user_choice" 时注册这个条件边。
    """
    valid_fix_steps = {
        f"run_fix_{s.replace('run_fix_', '')}" for s in p3_steps
    }

    def route(state: ECOState):
        step_status = state.get("step_status", {})
        for step in p2_steps:
            if step_status.get(step) == "error":
                return "error_handler"

        strategy = state.get("user_fix_strategy", "")
        target = f"run_fix_{strategy}" if strategy else ""
        if target in valid_fix_steps:
            return target
        return "error_handler"

    return route


def route_after_phase2_summary(
    state: ECOState,
) -> Literal["error_handler", "run_fix_setup", "run_fix_hold", "run_fix_leakage"]:
    """向后兼容：硬编码默认 Phase2 + Phase3 step 列表。"""
    return make_route_after_phase2_summary(
        PHASE_STEPS["phase2"], PHASE_STEPS["phase3"]
    )(state)


def route_after_phase3_gate(
    state: ECOState,
) -> Literal["error_handler", "phase3_summary"]:
    if state.get("phase_status", {}).get("phase3") == "error":
        return "error_handler"
    return "phase3_summary"


def route_after_phase3_summary(
    state: ECOState,
) -> Literal["init", "finalize"]:
    choice = state.get("user_iter_choice", "")
    if choice == "continue":
        return "init"
    return "finalize"
