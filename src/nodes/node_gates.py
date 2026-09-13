from __future__ import annotations

from src.state import ECOState
from src.utils.constants import PHASE_STEPS


def _phase_gate(
    state: ECOState, phase_name: str, expected_steps: tuple[str, ...]
) -> dict:
    step_status = state.get("step_status", {})

    any_error = any(step_status.get(s) == "error" for s in expected_steps)
    all_done = all(step_status.get(s) == "done" for s in expected_steps)

    result = {
        "current_phase": phase_name,
        "phase_status": dict(state.get("phase_status", {})),
    }

    if any_error:
        result["phase_status"][phase_name] = "error"
    elif all_done:
        result["phase_status"][phase_name] = "done"
    else:
        result["phase_status"][phase_name] = "running"

    return result


def make_phase2_gate_node(expected_steps: tuple[str, ...]):
    """
    Phase2 gate 工厂函数。Phase2 是 Send 并行——所有注册的 step 都会执行，
    所以闭包捕获固定的 expected_steps 列表，运行时直接用。
    """

    def gate(state: ECOState) -> dict:
        return _phase_gate(state, "phase2", expected_steps)

    return gate


def make_phase3_gate_node(all_possible_steps: tuple[str, ...]):
    """
    Phase3 gate 工厂函数。Phase3 是互斥分支——运行时只有一个 step 会执行，
    所以闭包捕获所有可能的 step，但内部动态从 step_status 推断哪些实际执行了。
    """

    def gate(state: ECOState) -> dict:
        step_status = state.get("step_status", {})
        executed = tuple(
            s for s in all_possible_steps
            if step_status.get(s) in ("done", "error")
        )
        if not executed:
            executed = all_possible_steps
        return _phase_gate(state, "phase3", executed)

    return gate


def node_phase2_gate(state: ECOState) -> dict:
    """向后兼容：硬编码默认 Phase2 step 列表。"""
    return make_phase2_gate_node(PHASE_STEPS["phase2"])(state)


def node_phase3_gate(state: ECOState) -> dict:
    """向后兼容：硬编码默认 Phase3 step 全集。"""
    return make_phase3_gate_node(PHASE_STEPS["phase3"])(state)
