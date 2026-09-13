from __future__ import annotations

import uuid

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from src.graph_builder import build_graph
from src.mcp_server.mock import MockECOMCPServer
from src.nodes.node_error_handler import get_retry_step


@pytest.fixture
def config():
    def _cfg(name: str) -> dict:
        return {"configurable": {"thread_id": f"L4_{name}_{uuid.uuid4().hex[:8]}"}}
    return _cfg


def _initial_input():
    return {
        "messages": [HumanMessage(content="帮我跑 designL4 的 ECO")],
        "design_name": "designL4",
    }


def _consume(graph, input_val, cfg):
    list(graph.stream(input_val, cfg))


def _trigger_error(graph, cfg):
    _consume(graph, _initial_input(), cfg)


# ============================================================
# L4-1: Command(resume=...) 正常中断恢复
# ============================================================


class TestCommandResumeRecovery:
    def test_phase2_summary_resume_setup_strategy(self, config):
        mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
        cfg = config("resume_setup")

        _consume(g, _initial_input(), cfg)
        _consume(g, Command(resume="setup"), cfg)

        s = g.get_state(cfg)
        assert s.next, "phase3_summary 应再触发 interrupt"
        assert s.values.get("user_fix_strategy") == "setup"
        assert s.values.get("setup_vio") == 30

    def test_phase2_summary_resume_hold_strategy(self, config):
        mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
        cfg = config("resume_hold")

        _consume(g, _initial_input(), cfg)
        _consume(g, Command(resume="hold"), cfg)

        s = g.get_state(cfg)
        assert s.next
        assert s.values.get("user_fix_strategy") == "hold"
        assert s.values.get("hold_vio") == 10

    def test_phase3_summary_resume_stop(self, config):
        mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
        cfg = config("resume_stop")

        _consume(g, _initial_input(), cfg)
        _consume(g, Command(resume="setup"), cfg)
        _consume(g, Command(resume="stop"), cfg)

        s = g.get_state(cfg)
        assert not s.next, "stop 后应无后续中断"

    def test_phase3_summary_resume_continue_advances_iteration(self, config):
        mcp = MockECOMCPServer(scenario="convergence", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
        cfg = config("resume_continue")

        _consume(g, _initial_input(), cfg)
        _consume(g, Command(resume="setup"), cfg)
        _consume(g, Command(resume="continue"), cfg)

        s = g.get_state(cfg).values
        assert s.get("iteration_cnt") == 2


# ============================================================
# L4-2: 断点续跑 —— 同一 thread_id + checkpoint 继续执行
# ============================================================


class TestCheckpointResume:
    def test_memory_saver_separate_threads_isolated(self):
        """同一个 graph fixture，不同 thread_id 应完全独立。"""
        mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())

        cfg_a = {"configurable": {"thread_id": f"iso_A_{uuid.uuid4().hex[:8]}"}}
        cfg_b = {"configurable": {"thread_id": f"iso_B_{uuid.uuid4().hex[:8]}"}}

        _consume(g, _initial_input(), cfg_a)
        state_a_before_resume = g.get_state(cfg_a).values

        _consume(g, _initial_input(), cfg_b)
        state_b_before_resume = g.get_state(cfg_b).values

        _consume(g, Command(resume="setup"), cfg_a)

        state_a_after = g.get_state(cfg_a).values
        state_b_after = g.get_state(cfg_b).values

        assert state_a_after.get("user_fix_strategy") == "setup"
        assert state_b_after.get("user_fix_strategy") != "setup", (
            f"thread B 不应被 thread A 的 resume 影响: {state_b_after.get('user_fix_strategy')}"
        )
        assert state_a_before_resume.get("design_name") == state_b_before_resume.get("design_name")

    def test_resume_after_checkpoint_continues_from_correct_phase(self, config):
        mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
        cfg = config("checkpoint_phase3")

        _consume(g, _initial_input(), cfg)
        _consume(g, Command(resume="setup"), cfg)

        checkpoint_phase3 = g.get_state(cfg).values.get("current_phase")
        assert checkpoint_phase3 == "phase3"

        _consume(g, Command(resume="stop"), cfg)
        final = g.get_state(cfg)
        assert not final.next


# ============================================================
# L4-3: Command(goto=...) retry 路径
# ============================================================


class TestRetryGotoRecovery:
    def test_retry_goto_syntax_executes_target_node(self, config):
        """Command(goto=...) 应跳过 error_handler 的正常路由，直接跳到指定节点执行。"""
        mcp = MockECOMCPServer(scenario="phase1_ext_error", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
        cfg = config("goto_ext")

        _consume(g, _initial_input(), cfg)
        state = g.get_state(cfg)
        assert state.next, "Error Handler 应触发 interrupt"
        assert "StarRCX license" in state.values.get("error_msg", "")

        new_step_status = dict(state.values.get("step_status", {}))
        new_step_status["run_ext"] = "pending"
        new_phase_status = dict(state.values.get("phase_status", {}))
        for p, status in new_phase_status.items():
            if status == "error":
                new_phase_status[p] = "pending"

        g.update_state(cfg, {
            "step_status": new_step_status,
            "phase_status": new_phase_status,
            "error_msg": "",
            "current_phase": "",
            "current_step": "",
        })

        retry_events = [
            list(ev.keys())[0]
            for ev in g.stream(Command(goto="run_ext"), cfg)
        ]
        assert "run_ext" in retry_events, f"goto='run_ext' 应执行 run_ext，实际事件: {retry_events}"

        s = g.get_state(cfg)
        assert s.next, "retry 后 Mock Server 仍返回错误 → error_handler 再次触发 interrupt（正确行为）"

    def test_get_retry_step_returns_actual_failed_step(self, config):
        """get_retry_step 辅助函数应返回当前出错 step。"""
        mcp = MockECOMCPServer(scenario="phase2_pv_error", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
        cfg = config("goto_pv")

        _consume(g, _initial_input(), cfg)
        state = g.get_state(cfg)

        retry_step = get_retry_step(state.values)
        assert retry_step == "run_pv", f"应返回 run_pv，实际 {retry_step}"

    def test_abort_via_goto_finalize_executes_finalize_node(self, config):
        """Command(goto='finalize') 应直接执行 finalize 节点。"""
        mcp = MockECOMCPServer(scenario="phase2_sta_error", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
        cfg = config("abort_finalize")

        _consume(g, _initial_input(), cfg)
        abort_events = [
            list(ev.keys())[0]
            for ev in g.stream(Command(goto="finalize"), cfg)
        ]
        assert "finalize" in abort_events, (
            f"goto='finalize' 应执行 finalize 节点，实际事件: {abort_events}"
        )


# ============================================================
# L4-4: stream(None) 断点续跑 —— LangGraph resume 语义
# ============================================================


class TestStreamNoneResume:
    def test_stream_none_resumes_after_command_resume(self, config):
        """stream(None) 等同于 Command() 的快捷方式，能继续执行下一个 step。"""
        mcp = MockECOMCPServer(scenario="convergence", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
        cfg = config("stream_none")

        _consume(g, _initial_input(), cfg)
        _consume(g, Command(resume="setup"), cfg)
        _consume(g, Command(resume="continue"), cfg)

        s = g.get_state(cfg)
        assert s.values.get("iteration_cnt") == 2
        assert s.next, "phase2_summary 应再 interrupt"
