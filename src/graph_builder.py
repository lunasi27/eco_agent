from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from langchain_core.messages import BaseMessage
from langgraph.graph import END, StateGraph

from src.conversation.agent import (
    make_agent_entry_node,
    node_chat_fallback,
    route_from_agent,
)
from src.nodes.node_error_handler import make_error_handler_node
from src.nodes.node_finalize import node_finalize
from src.nodes.node_gates import node_phase2_gate, node_phase3_gate
from src.nodes.node_init import make_init_node
from src.nodes.node_phase1 import make_step_node
from src.nodes.node_phase2 import node_phase2_summary
from src.nodes.node_phase3 import node_phase3_summary
from src.routers.phase_routes import (
    route_after_init,
    route_after_phase2_gate,
    route_after_phase2_summary,
    route_after_phase3_gate,
    route_after_phase3_summary,
    route_after_run_eco_route,
    route_after_run_ext,
)
from src.state import ECOState

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver

    from src.mcp_server.protocol import ECOMCPServer


def build_graph(
    mcp_server: ECOMCPServer,
    checkpointer: BaseCheckpointSaver | None = None,
    llm_callable: Callable[[list], object] | None = None,
    skip_agent_entry: bool = False,
):
    builder = StateGraph(ECOState)

    if not skip_agent_entry:
        builder.add_node("agent_entry", make_agent_entry_node(llm_callable))

    builder.add_node("init", make_init_node(mcp_server))

    builder.add_node(
        "run_eco_route",
        make_step_node(mcp_server, "run_eco_route", "phase1"),
    )
    builder.add_node(
        "run_ext",
        make_step_node(mcp_server, "run_ext", "phase1"),
    )

    builder.add_node(
        "run_sta",
        make_step_node(
            mcp_server, "run_sta", "phase2", result_keys=["setup_vio", "hold_vio"]
        ),
    )
    builder.add_node(
        "run_pv",
        make_step_node(mcp_server, "run_pv", "phase2", result_keys=["pv_pass"]),
    )
    builder.add_node(
        "run_signoff",
        make_step_node(
            mcp_server, "run_signoff", "phase2", result_keys=["signoff_pass"]
        ),
    )

    builder.add_node("phase2_gate", node_phase2_gate)
    builder.add_node("phase2_summary", node_phase2_summary)

    builder.add_node(
        "run_fix_setup",
        make_step_node(
            mcp_server, "run_fix_setup", "phase3", result_keys=["setup_vio"]
        ),
    )
    builder.add_node(
        "run_fix_hold",
        make_step_node(
            mcp_server, "run_fix_hold", "phase3", result_keys=["hold_vio"]
        ),
    )
    builder.add_node(
        "run_fix_leakage",
        make_step_node(mcp_server, "run_fix_leakage", "phase3"),
    )

    builder.add_node("phase3_gate", node_phase3_gate)
    builder.add_node("phase3_summary", node_phase3_summary)

    builder.add_node("error_handler", make_error_handler_node())
    builder.add_node("finalize", node_finalize)

    builder.add_node("chat_fallback", node_chat_fallback)

    if skip_agent_entry:
        builder.set_entry_point("init")
    else:
        builder.set_entry_point("agent_entry")
        builder.add_conditional_edges("agent_entry", route_from_agent)

    builder.add_conditional_edges("init", route_after_init)
    builder.add_conditional_edges("run_eco_route", route_after_run_eco_route)

    builder.add_conditional_edges("run_ext", route_after_run_ext)

    builder.add_edge("run_sta", "phase2_gate")
    builder.add_edge("run_pv", "phase2_gate")
    builder.add_edge("run_signoff", "phase2_gate")

    builder.add_conditional_edges("phase2_gate", route_after_phase2_gate)
    builder.add_conditional_edges("phase2_summary", route_after_phase2_summary)

    builder.add_edge("run_fix_setup", "phase3_gate")
    builder.add_edge("run_fix_hold", "phase3_gate")
    builder.add_edge("run_fix_leakage", "phase3_gate")

    builder.add_conditional_edges("phase3_gate", route_after_phase3_gate)
    builder.add_conditional_edges("phase3_summary", route_after_phase3_summary)

    builder.add_edge("finalize", END)
    builder.add_edge("chat_fallback", END)

    return builder.compile(checkpointer=checkpointer)
