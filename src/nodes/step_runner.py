from __future__ import annotations

import time
from typing import TYPE_CHECKING

from src.state import ECOState

if TYPE_CHECKING:
    from src.mcp_server.protocol import ECOMCPServer


def run_step(
    state: ECOState,
    step_name: str,
    phase_name: str,
    mcp_server: ECOMCPServer,
    result_keys: list | None = None,
    extra_params: dict | None = None,
) -> dict:
    result_keys = result_keys or []
    extra_params = extra_params or {}

    updated = {
        "current_step": step_name,
        "current_phase": phase_name,
        "step_status": {**state.get("step_status", {}), step_name: "running"},
        "phase_status": {**state.get("phase_status", {}), phase_name: "running"},
    }

    started_at = time.monotonic()

    try:
        params = {
            "design_name": state["design_name"],
            "run_dir": state["run_dir"],
            **extra_params,
        }
        res = getattr(mcp_server, step_name)(**params)

        updated["step_status"] = {
            **updated["step_status"],
            step_name: "done",
        }
        updated["phase_status"] = {
            **updated["phase_status"],
            phase_name: "done",
        }

        for key in result_keys:
            if key in res:
                updated[key] = res[key]

    except Exception as e:
        updated["step_status"] = {
            **updated["step_status"],
            step_name: "error",
        }
        updated["phase_status"] = {
            **updated["phase_status"],
            phase_name: "error",
        }
        updated["error_msg"] = f"[{step_name}] {str(e)}"

    elapsed = time.monotonic() - started_at
    updated["step_elapsed"] = {
        **state.get("step_elapsed", {}),
        step_name: round(elapsed, 4),
    }

    return updated
