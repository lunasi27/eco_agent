from __future__ import annotations

from langgraph.types import Command, interrupt

from src.state import ECOState
from src.utils.constants import STEP_TO_PHASE
from src.utils.file_utils import build_log_path


VALID_STEP_TARGETS = set(STEP_TO_PHASE.keys())


def _find_error_context(state: ECOState) -> tuple[str, str]:
    step_status = state.get("step_status", {})
    phase_status = state.get("phase_status", {})
    error_step = state.get("current_step", "")
    error_phase = state.get("current_phase", "")

    for step, status in step_status.items():
        if status == "error":
            error_step = step
            break

    for p, status in phase_status.items():
        if status == "error":
            error_phase = p
            break

    if not error_step and error_phase in STEP_TO_PHASE.values():
        for step, phase in STEP_TO_PHASE.items():
            if phase == error_phase:
                error_step = step
                break

    return error_step, error_phase


def _reset_error_state(state: ECOState, error_step: str, error_phase: str) -> dict:
    step_status = dict(state.get("step_status", {}))
    phase_status = dict(state.get("phase_status", {}))

    if error_step and step_status.get(error_step) == "error":
        step_status[error_step] = "pending"
    if error_phase and phase_status.get(error_phase) == "error":
        phase_status[error_phase] = "pending"

    result = {
        "step_status": step_status,
        "phase_status": phase_status,
        "error_msg": "",
        "current_step": error_step,
        "current_phase": error_phase,
        "user_error_choice": "",
        "retry_step": "",
    }
    return result


def make_error_handler_node():
    def node_error_handler(state: ECOState) -> Command:
        error_step, error_phase = _find_error_context(state)
        error_msg = state.get("error_msg", "未知错误")
        run_dir = state.get("run_dir", "")
        log_path = build_log_path(run_dir, error_step) if run_dir else ""

        lines = [
            "── 错误发生 ──",
            f"出错Phase：{error_phase or 'unknown'}",
            f"出错Step：{error_step or 'unknown'}",
            f"错误摘要：{error_msg}",
            f"日志路径：{log_path}",
            "── 错误处理 ──",
            "请选择处理方式：retry（重试这个Step）/ abort（终止流水线）",
        ]

        interrupt_msg = "\n".join(lines)

        choice = interrupt(interrupt_msg)
        choice_lower = (choice or "abort").strip().lower()

        reset = _reset_error_state(state, error_step, error_phase)
        reset["current_phase"] = ""
        reset["current_step"] = ""
        reset["interrupt_msg"] = ""

        if choice_lower in ("retry", "r", "重试"):
            target = error_step if error_step in VALID_STEP_TARGETS else "run_eco_route"
            reset["user_error_choice"] = "retry"
            reset["retry_step"] = error_step
            return Command(goto=target, update=reset)

        reset["user_error_choice"] = "abort"
        reset["retry_step"] = ""
        return Command(goto="finalize", update=reset)

    return node_error_handler


def get_retry_step(state: ECOState) -> str:
    """向后兼容辅助函数：返回当前出错的 step 名。完全空 state 时 fallback 到 run_eco_route。"""
    error_step, _ = _find_error_context(state)
    if error_step:
        return error_step
    return "run_eco_route"
