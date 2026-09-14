from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from langgraph.graph import END, StateGraph

from src.conversation.agent import (
    make_agent_entry_node,
    make_chat_fallback_node,
    route_from_agent,
    route_from_chat,
)
from src.nodes.node_error_handler import make_error_handler_node
from src.nodes.node_finalize import node_finalize
from src.nodes.node_gates import make_phase2_gate_node, make_phase3_gate_node
from src.nodes.node_init import make_init_node
from src.nodes.node_phase1 import make_step_node
from src.nodes.node_phase2 import make_phase2_summary_node
from src.nodes.node_phase3 import node_phase3_summary
from src.routers.phase_routes import (
    make_route_after_phase2_summary,
    make_route_after_run_ext,
    route_after_init,
    route_after_phase2_gate,
    route_after_phase3_gate,
    route_after_phase3_summary,
    route_after_run_eco_route,
)
from src.state import ECOState
from src.utils.constants import PHASE_STEPS, STEP_TO_PHASE, STRATEGY_TO_STEP

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver
    from langgraph.types import Send

    from src.mcp_server.protocol import ECOMCPServer


_DEFAULT_PIPELINE: dict = {
    "name": "default",
    "phases": {
        "phase1": {
            "steps": list(PHASE_STEPS["phase1"]),
            "type": "serial",
        },
        "phase2": {
            "steps": list(PHASE_STEPS["phase2"]),
            "type": "parallel",
            "error_policy": "all_block",
        },
        "phase3": {
            "steps": list(PHASE_STEPS["phase3"]),
            "type": "branch",
            "router": "user_choice",
            "gate": True,
        },
    },
}

_PHASE2_STEP_RESULT_KEYS: dict[str, list[str]] = {
    "run_sta": ["setup_vio", "hold_vio"],
    "run_pv": ["pv_pass"],
    "run_signoff": ["signoff_pass"],
}

_PHASE3_STEP_RESULT_KEYS: dict[str, list[str]] = {
    "run_pt_fix_setup": ["setup_vio"],
    "run_pt_fix_hold": ["hold_vio"],
}


def build_graph(
    mcp_server: ECOMCPServer,
    checkpointer: BaseCheckpointSaver | None = None,
    llm_callable: Callable[[list], object] | None = None,
    skip_agent_entry: bool = False,
    pipeline: dict | None = None,
):
    """
    构图的单一入口。

    pipeline 配置决定了：
    - Phase2 并行哪些 step → 决定注册哪些节点 + Send 列表 + gate 检查清单
    - Phase3 有哪些 fix step → 决定注册哪些节点 + router 策略 + gate 全集
    - Phase3 router 策略 → 决定 phase2_summary 是否 interrupt + 路由方式
    - 是否跳过 Phase3 → phase2_summary 直接 → finalize

    运行时图是固定的——不会有"注册了但不走"的边。
    """
    pipeline = pipeline or _DEFAULT_PIPELINE
    phases_cfg = pipeline.get("phases", {})

    p1_steps = tuple(phases_cfg.get("phase1", {}).get("steps", PHASE_STEPS["phase1"]))
    p2_steps = tuple(phases_cfg.get("phase2", {}).get("steps", PHASE_STEPS["phase2"]))
    phase3_cfg = phases_cfg.get("phase3", {})
    p3_steps = tuple(phase3_cfg.get("steps", []))
    p3_router = phase3_cfg.get("router", "user_choice")
    has_phase3 = len(p3_steps) > 0

    builder = StateGraph(ECOState)

    # ========== 固定节点 ==========
    if not skip_agent_entry:
        builder.add_node("agent_entry", make_agent_entry_node(llm_callable))

    builder.add_node("init", make_init_node(mcp_server))
    builder.add_node("error_handler", make_error_handler_node())
    builder.add_node("finalize", node_finalize)
    builder.add_node("chat_fallback", make_chat_fallback_node(llm_callable))

    # ========== Phase1：固定串行 ==========
    for step in p1_steps:
        builder.add_node(step, make_step_node(mcp_server, step, "phase1"))

    # ========== Phase2：动态并行 ==========
    for step in p2_steps:
        result_keys = _PHASE2_STEP_RESULT_KEYS.get(step, [])
        builder.add_node(
            step,
            make_step_node(mcp_server, step, "phase2", result_keys=result_keys),
        )
        builder.add_edge(step, "phase2_gate")

    builder.add_node("phase2_gate", make_phase2_gate_node(p2_steps))
    builder.add_node(
        "phase2_summary",
        make_phase2_summary_node(p2_steps, has_phase3=has_phase3, p3_router=p3_router),
    )

    builder.add_conditional_edges(
        "run_ext", make_route_after_run_ext(p2_steps)
    )
    builder.add_conditional_edges("phase2_gate", route_after_phase2_gate)

    # ========== Phase3：动态分支（可能跳过）==========
    if has_phase3:
        for step in p3_steps:
            result_keys = _PHASE3_STEP_RESULT_KEYS.get(step, [])
            builder.add_node(
                step,
                make_step_node(mcp_server, step, "phase3", result_keys=result_keys),
            )
            builder.add_edge(step, "phase3_gate")

        builder.add_node("phase3_gate", make_phase3_gate_node(p3_steps))
        builder.add_node("phase3_summary", node_phase3_summary)

        # Phase2 → Phase3 路由策略
        if p3_router == "user_choice":
            builder.add_conditional_edges(
                "phase2_summary", make_route_after_phase2_summary(p2_steps, p3_steps)
            )
        elif p3_router.startswith("auto_"):
            strategy = p3_router.replace("auto_", "")
            target_step = STRATEGY_TO_STEP.get(strategy, "")
            if target_step and target_step in p3_steps:
                builder.add_edge("phase2_summary", target_step)

        builder.add_conditional_edges("phase3_gate", route_after_phase3_gate)
        builder.add_conditional_edges("phase3_summary", route_after_phase3_summary)
    else:
        builder.add_edge("phase2_summary", "finalize")

    # ========== 入口和终节点 ==========
    if skip_agent_entry:
        builder.set_entry_point("init")
    else:
        builder.set_entry_point("agent_entry")
        builder.add_conditional_edges("agent_entry", route_from_agent)

    builder.add_conditional_edges("init", route_after_init)
    builder.add_conditional_edges("run_eco_route", route_after_run_eco_route)

    builder.add_edge("finalize", END)
    # 对话自环：chat_fallback 解析出 design → init；否则回到本节点继续对话。
    # 每轮对话都是一个全新任务，避免同一任务内重复 interrupt 的恢复陷阱
    builder.add_conditional_edges("chat_fallback", route_from_chat)

    return builder.compile(checkpointer=checkpointer)
