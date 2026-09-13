from __future__ import annotations

from typing import TYPE_CHECKING

from src.nodes.step_runner import run_step

if TYPE_CHECKING:
    from src.mcp_server.protocol import ECOMCPServer


def make_step_node(
    mcp_server: ECOMCPServer,
    step_name: str,
    phase_name: str,
    result_keys: list | None = None,
    extra_params_builder=None,
):
    def node(state: dict) -> dict:
        extra_params = {}
        if extra_params_builder is not None:
            extra_params = extra_params_builder(state)
        elif step_name.startswith("run_fix_"):
            extra_params = {"fix_strategy": state.get("user_fix_strategy", "")}
        return run_step(
            state=state,
            step_name=step_name,
            phase_name=phase_name,
            mcp_server=mcp_server,
            result_keys=result_keys,
            extra_params=extra_params,
        )

    return node
