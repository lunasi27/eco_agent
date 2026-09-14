from __future__ import annotations

import uuid

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from src.graph_builder import build_graph
from src.mcp_server.mock import MockECOMCPServer
from src.nodes.node_error_handler import get_retry_step
from src.nodes.step_runner import run_step
from src.utils.constants import PHASE_STEPS, STEP_TO_PHASE


@pytest.fixture
def config():
    def _cfg(name: str) -> dict:
        return {"configurable": {"thread_id": f"L5_{name}_{uuid.uuid4().hex[:8]}"}}
    return _cfg


def _initial_input():
    return {
        "messages": [HumanMessage(content="帮我跑 designL5 的 ECO")],
        "design_name": "designL5",
    }


def _consume(graph, input_val, cfg):
    list(graph.stream(input_val, cfg))


# ============================================================
# L5-1: error_msg 格式正确性
# ============================================================


class TestErrorMessageFormat:
    def test_run_step_exception_wraps_step_name(self):
        server = MockECOMCPServer(scenario="phase2_sta_error", simulate_delay=0)
        result = run_step(
            state={"design_name": "d", "run_dir": "/tmp", "step_status": {}, "phase_status": {}},
            step_name="run_sta",
            phase_name="phase2",
            mcp_server=server,
        )
        assert result["step_status"]["run_sta"] == "error"
        assert result["phase_status"]["phase2"] == "error"
        assert result["error_msg"].startswith("[run_sta] "), (
            f"error_msg 应以 '[run_sta] ' 开头, 实际: {result['error_msg']!r}"
        )
        assert "STA 执行超时" in result["error_msg"]

    def test_phase3_fix_error_msg_format(self):
        server = MockECOMCPServer(scenario="phase3_fix_error", simulate_delay=0)
        result = run_step(
            state={"design_name": "d", "run_dir": "/tmp", "step_status": {}, "phase_status": {}},
            step_name="run_pt_fix_setup",
            phase_name="phase3",
            mcp_server=server,
            extra_params={"fix_strategy": "setup"},
        )
        assert result["error_msg"].startswith("[run_pt_fix_setup] ")
        assert "ECO Fix" in result["error_msg"]

    def test_error_is_propagated_to_error_handler_state(self, config):
        mcp = MockECOMCPServer(scenario="phase2_pv_error", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
        cfg = config("pv_error")

        _consume(g, _initial_input(), cfg)
        s = g.get_state(cfg).values
        assert s.get("error_msg", "").startswith("[run_pv] ")
        assert "LVS" in s["error_msg"]


# ============================================================
# L5-2: error_handler interrupt 内容完整性
# ============================================================


class TestErrorHandlerInterrupt:
    def test_error_handler_interrupt_contains_required_fields(self, config):
        mcp = MockECOMCPServer(scenario="phase1_ext_error", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
        cfg = config("interrupt_fields")

        _consume(g, _initial_input(), cfg)
        s = g.get_state(cfg)
        assert s.interrupts

        interrupt_text = str(s.interrupts[0].value)

        assert "错误发生" in interrupt_text
        assert "出错Phase" in interrupt_text
        assert "出错Step" in interrupt_text
        assert "错误摘要" in interrupt_text
        assert "日志路径" in interrupt_text
        assert "处理方式" in interrupt_text

    def test_error_handler_reports_correct_phase1(self, config):
        mcp = MockECOMCPServer(scenario="phase1_ext_error", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
        cfg = config("phase1_err")

        _consume(g, _initial_input(), cfg)
        s = g.get_state(cfg).interrupts[0].value
        assert "phase1" in str(s)
        assert "run_ext" in str(s)

    def test_error_handler_reports_correct_phase2(self, config):
        mcp = MockECOMCPServer(scenario="phase2_sta_error", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
        cfg = config("phase2_err")

        _consume(g, _initial_input(), cfg)
        s = g.get_state(cfg).interrupts[0].value
        assert "phase2" in str(s)
        assert "run_sta" in str(s)

    def test_error_handler_reports_correct_phase3(self, config):
        mcp = MockECOMCPServer(scenario="phase3_fix_error", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
        cfg = config("phase3_err")

        _consume(g, _initial_input(), cfg)
        _consume(g, Command(resume="setup"), cfg)
        s = g.get_state(cfg).interrupts[0].value
        assert "phase3" in str(s)
        assert "run_pt_fix_setup" in str(s)


# ============================================================
# L5-3: update_state 状态重置正确性（调用层重试语义）
# ============================================================


class TestUpdateStateReset:
    def test_reset_step_and_phase_status_before_retry(self, config):
        mcp = MockECOMCPServer(scenario="phase2_sta_error", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
        cfg = config("reset_status")

        _consume(g, _initial_input(), cfg)
        s = g.get_state(cfg).values

        assert s["step_status"]["run_sta"] == "error"
        assert s["phase_status"]["phase2"] == "error"

        new_step = dict(s["step_status"])
        new_step["run_sta"] = "pending"
        new_phase = dict(s["phase_status"])
        new_phase["phase2"] = "pending"

        g.update_state(cfg, {
            "step_status": new_step,
            "phase_status": new_phase,
            "error_msg": "",
        })

        after_reset = g.get_state(cfg).values
        assert after_reset["step_status"]["run_sta"] == "pending"
        assert after_reset["phase_status"]["phase2"] == "pending"
        assert after_reset["error_msg"] == ""

    def test_reset_preserves_other_step_statuses(self, config):
        mcp = MockECOMCPServer(scenario="phase2_sta_error", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
        cfg = config("preserve_others")

        _consume(g, _initial_input(), cfg)
        s = g.get_state(cfg).values

        new_step = dict(s["step_status"])
        new_step["run_sta"] = "pending"
        g.update_state(cfg, {"step_status": new_step})

        after = g.get_state(cfg).values
        assert after["step_status"]["run_eco_route"] == "done"
        assert after["step_status"]["run_ext"] == "done"
        assert after["step_status"]["run_pv"] == "done"
        assert after["step_status"]["run_signoff"] == "done"


# ============================================================
# L5-4: get_retry_step 覆盖所有出错场景
# ============================================================


class TestGetRetryStep:
    def test_phase1_step_error(self):
        state = {
            "step_status": {"run_ext": "error"},
            "phase_status": {"phase1": "error"},
            "current_step": "run_ext",
            "current_phase": "phase1",
        }
        assert get_retry_step(state) == "run_ext"

    def test_phase2_sta_error(self):
        state = {
            "step_status": {"run_sta": "error", "run_pv": "done"},
            "phase_status": {"phase2": "error"},
            "current_step": "run_sta",
            "current_phase": "phase2",
        }
        assert get_retry_step(state) == "run_sta"

    def test_phase3_fix_error(self):
        state = {
            "step_status": {"run_pt_fix_hold": "error"},
            "phase_status": {"phase3": "error"},
            "current_step": "run_pt_fix_hold",
            "current_phase": "phase3",
        }
        assert get_retry_step(state) == "run_pt_fix_hold"

    def test_empty_falls_back_to_run_eco_route(self):
        state = {"step_status": {}, "phase_status": {}, "current_step": "", "current_phase": ""}
        assert get_retry_step(state) == "run_eco_route"


# ============================================================
# L5-5: error_handler Command(goto) 边界分支
# ============================================================


class TestErrorHandlerCommandGoto:
    def test_abort_returns_command_goto_finalize(self, config):
        mcp = MockECOMCPServer(scenario="phase2_sta_error", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
        cfg = config("abort_goto")

        _consume(g, _initial_input(), cfg)
        events = []
        for ev in g.stream(Command(resume="abort"), cfg):
            node = list(ev.keys())[0]
            events.append(node)

        assert "finalize" in events, f"abort 后应直接 goto finalize, 事件序列: {events}"
        assert not g.get_state(cfg).next

    def test_retry_returns_command_goto_error_step(self, config):
        mcp = MockECOMCPServer(scenario="phase2_sta_error", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
        cfg = config("retry_goto")

        _consume(g, _initial_input(), cfg)
        events = []
        for ev in g.stream(Command(resume="retry"), cfg):
            node = list(ev.keys())[0]
            events.append(node)

        assert "run_sta" in events, f"retry 应 Command(goto=run_sta), 事件序列: {events}"

    def test_retry_shortcut_r_resumes(self, config):
        mcp = MockECOMCPServer(scenario="phase2_sta_error", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
        cfg = config("shortcut_r")

        _consume(g, _initial_input(), cfg)
        events = []
        for ev in g.stream(Command(resume="r"), cfg):
            node = list(ev.keys())[0]
            events.append(node)

        assert "run_sta" in events

    def test_retry_chinese_重试_resumes(self, config):
        mcp = MockECOMCPServer(scenario="phase2_sta_error", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
        cfg = config("chinese_retry")

        _consume(g, _initial_input(), cfg)
        events = []
        for ev in g.stream(Command(resume="重试"), cfg):
            node = list(ev.keys())[0]
            events.append(node)

        assert "run_sta" in events

    def test_default_fallback_is_abort(self, config):
        mcp = MockECOMCPServer(scenario="phase2_sta_error", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
        cfg = config("default_fallback")

        _consume(g, _initial_input(), cfg)
        events = []
        for ev in g.stream(Command(resume=""), cfg):
            node = list(ev.keys())[0]
            events.append(node)

        assert "finalize" in events, f"空 choice 应默认 abort→finalize, 事件: {events}"


# ============================================================
# L5-6: _find_error_context fallback 分支
# ============================================================


class TestFindErrorContextFallback:
    def test_phase2_error_without_step_status_still_finds_step(self):
        from src.nodes.node_error_handler import _find_error_context

        state = {
            "step_status": {},
            "phase_status": {"phase2": "error"},
            "current_step": "",
            "current_phase": "phase2",
        }
        error_step, error_phase = _find_error_context(state)
        assert error_step == "run_sta", (
            f"phase2 error fallback 应找到 run_sta, 实际: {error_step}"
        )
        assert error_phase == "phase2"

    def test_phase3_error_finds_corresponding_step(self):
        from src.nodes.node_error_handler import _find_error_context

        state = {
            "step_status": {},
            "phase_status": {"phase3": "error"},
            "current_step": "",
            "current_phase": "phase3",
        }
        error_step, error_phase = _find_error_context(state)
        assert error_step == "run_pt_fix_setup", (
            f"phase3 error fallback 应找到 run_pt_fix_setup, 实际: {error_step}"
        )
