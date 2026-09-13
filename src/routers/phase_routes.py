from __future__ import annotations

from typing import Literal

from langgraph.types import Send

from src.state import ECOState


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


def route_after_run_ext(state: ECOState):
    if state.get("step_status", {}).get("run_ext") == "error":
        return "error_handler"
    shared = {k: state[k] for k in _PHASE2_SHARED_KEYS if k in state}
    return [
        Send("run_sta", shared),
        Send("run_pv", shared),
        Send("run_signoff", shared),
    ]


def route_after_phase2_gate(
    state: ECOState,
) -> Literal["error_handler", "phase2_summary"]:
    if state.get("phase_status", {}).get("phase2") == "error":
        return "error_handler"
    return "phase2_summary"


def route_after_phase2_summary(
    state: ECOState,
) -> Literal["error_handler", "run_fix_setup", "run_fix_hold", "run_fix_leakage"]:
    step_status = state.get("step_status", {})
    for step in ("run_sta", "run_pv", "run_signoff"):
        if step_status.get(step) == "error":
            return "error_handler"

    strategy = state.get("user_fix_strategy", "")
    if strategy == "setup":
        return "run_fix_setup"
    if strategy == "hold":
        return "run_fix_hold"
    if strategy == "leakage":
        return "run_fix_leakage"
    return "error_handler"


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



