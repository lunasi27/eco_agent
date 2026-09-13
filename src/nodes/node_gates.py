from __future__ import annotations

from src.state import ECOState


def _phase_gate(state: ECOState, phase_name: str, expected_steps: tuple[str, ...]) -> dict:
    step_status = state.get("step_status", {})

    any_error = any(
        step_status.get(s) == "error" for s in expected_steps
    )
    all_done = all(
        step_status.get(s) == "done" for s in expected_steps
    )

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


def node_phase2_gate(state: ECOState) -> dict:
    return _phase_gate(state, "phase2", ("run_sta", "run_pv", "run_signoff"))


def node_phase3_gate(state: ECOState) -> dict:
    step_status = state.get("step_status", {})
    all_fix_steps = ("run_fix_setup", "run_fix_hold", "run_fix_leakage")
    executed_fix_steps = tuple(
        s for s in all_fix_steps if step_status.get(s) in ("done", "error")
    )
    if not executed_fix_steps:
        executed_fix_steps = all_fix_steps
    return _phase_gate(state, "phase3", executed_fix_steps)
