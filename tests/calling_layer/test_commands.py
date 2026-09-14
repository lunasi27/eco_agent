"""slash 命令分发层测试（dispatch 级，不经过事件循环）。"""
from __future__ import annotations

import time

from langgraph.checkpoint.memory import MemorySaver

from src.calling_layer.commands import (
    CommandResult,
    dispatch,
    new_thread_id,
)
from src.graph_builder import build_graph
from src.mcp_server.mock import MockECOMCPServer


def _fresh_thread_id(name: str) -> str:
    return f"{name}_{int(time.time() * 1000)}"


def _make_graph():
    mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
    return build_graph(mcp_server=mcp, checkpointer=MemorySaver())


def _conversation_state(g, cfg):
    """启动新会话并跑到 agent_entry 的打招呼 interrupt。"""
    list(g.stream({"messages": []}, cfg))
    return g.get_state(cfg)


def _pipeline_state(g, cfg):
    """跳过对话，跑到 phase2_summary 的策略选择 interrupt。"""
    list(g.stream({"design_name": "d"}, cfg))
    return g.get_state(cfg)


class TestBasicCommands:
    def test_help_and_unknown(self, capsys):
        g = _make_graph()
        cfg = {"configurable": {"thread_id": _fresh_thread_id("cmd_help")}}
        state = _conversation_state(g, cfg)

        assert dispatch("/help", g, cfg, state).action == "handled"
        assert "/run_eco" in capsys.readouterr().out

        assert dispatch("/nope", g, cfg, state).action == "handled"
        assert "未知命令" in capsys.readouterr().out

    def test_status_shows_thread_and_phase(self, capsys):
        g = _make_graph()
        cfg = {"configurable": {"thread_id": _fresh_thread_id("cmd_st")}}
        state = _conversation_state(g, cfg)

        assert dispatch("/status", g, cfg, state).action == "handled"
        out = capsys.readouterr().out
        assert cfg["configurable"]["thread_id"] in out
        assert "对话中" in out  # 打招呼中断点尚未进入流水线

    def test_sessions_lists_saved_threads(self, capsys):
        g = _make_graph()
        cfg = {"configurable": {"thread_id": _fresh_thread_id("cmd_ls")}}
        state = _conversation_state(g, cfg)

        assert dispatch("/sessions", g, cfg, state).action == "handled"
        out = capsys.readouterr().out
        assert cfg["configurable"]["thread_id"] in out
        assert "中断中" in out


class TestRunEco:
    def test_run_eco_synthesizes_intent_text(self):
        g = _make_graph()
        cfg = {"configurable": {"thread_id": _fresh_thread_id("cmd_eco")}}
        state = _conversation_state(g, cfg)

        result = dispatch("/run_eco designA", g, cfg, state)
        assert result.action == "resume_text"
        assert result.resume_text == "帮我跑 designA 的 ECO"

    def test_run_eco_requires_design_name(self, capsys):
        g = _make_graph()
        cfg = {"configurable": {"thread_id": _fresh_thread_id("cmd_eco2")}}
        state = _conversation_state(g, cfg)

        assert dispatch("/run_eco", g, cfg, state).action == "handled"
        assert "用法" in capsys.readouterr().out

    def test_run_eco_rejected_mid_pipeline(self, capsys):
        g = _make_graph()
        cfg = {"configurable": {"thread_id": _fresh_thread_id("cmd_eco3")}}
        state = _pipeline_state(g, cfg)

        assert dispatch("/run_eco designA", g, cfg, state).action == "handled"
        assert "流水线进行中" in capsys.readouterr().out


class TestResumeCommand:
    def test_resume_rejects_missing_session(self, capsys):
        g = _make_graph()
        cfg = {"configurable": {"thread_id": _fresh_thread_id("cmd_rs1")}}
        state = _conversation_state(g, cfg)

        assert dispatch("/resume ghost_session", g, cfg, state).action == "handled"
        assert "找不到会话" in capsys.readouterr().out

    def test_resume_rejects_completed_session(self, capsys):
        g = _make_graph()
        cfg = {"configurable": {"thread_id": _fresh_thread_id("cmd_rs2")}}
        state = _conversation_state(g, cfg)

        # 造一个已完成的会话
        done_cfg = {"configurable": {"thread_id": _fresh_thread_id("cmd_done")}}
        list(g.stream({"design_name": "d"}, done_cfg))
        # happy_path 停在中断，跑完它
        from langgraph.types import Command
        list(g.stream(Command(resume="setup"), done_cfg))
        list(g.stream(Command(resume="stop"), done_cfg))

        result = dispatch(f"/resume {done_cfg['configurable']['thread_id']}", g, cfg, state)
        assert result.action == "handled"
        assert "已正常结束" in capsys.readouterr().out

    def test_resume_switches_to_interrupted_session(self):
        g = _make_graph()
        cfg = {"configurable": {"thread_id": _fresh_thread_id("cmd_rs3")}}
        state = _conversation_state(g, cfg)

        # 造一个中断中的目标会话
        target = _fresh_thread_id("cmd_target")
        list(g.stream({"design_name": "d"}, {"configurable": {"thread_id": target}}))

        result = dispatch(f"/resume {target}", g, cfg, state)
        assert result.action == "switch"
        assert result.new_thread_id == target
        assert result.next_input is None

    def test_resume_to_same_session_is_noop(self, capsys):
        g = _make_graph()
        cfg = {"configurable": {"thread_id": _fresh_thread_id("cmd_rs4")}}
        state = _conversation_state(g, cfg)

        assert dispatch(f"/resume {cfg['configurable']['thread_id']}", g, cfg, state).action == "handled"
        assert "已经在" in capsys.readouterr().out


class TestNewAndExit:
    def test_new_switches_to_fresh_thread(self):
        g = _make_graph()
        cfg = {"configurable": {"thread_id": _fresh_thread_id("cmd_new")}}
        state = _conversation_state(g, cfg)

        result = dispatch("/new", g, cfg, state)
        assert result.action == "switch"
        assert result.new_thread_id != cfg["configurable"]["thread_id"]
        assert result.next_input == {"messages": []}

    def test_new_deletes_conversation_stage_checkpoint(self):
        g = _make_graph()
        old_tid = _fresh_thread_id("cmd_new2")
        cfg = {"configurable": {"thread_id": old_tid}}
        state = _conversation_state(g, cfg)

        dispatch("/new", g, cfg, state)
        # 对话阶段断点无保留价值，应被清理
        from src.utils.session_store import STATUS_MISSING, classify_snapshot
        assert classify_snapshot(g.get_state(cfg)) == STATUS_MISSING

    def test_new_mid_pipeline_keeps_checkpoint(self, capsys):
        g = _make_graph()
        old_tid = _fresh_thread_id("cmd_new3")
        cfg = {"configurable": {"thread_id": old_tid}}
        state = _pipeline_state(g, cfg)

        result = dispatch("/new", g, cfg, state)
        assert result.action == "switch"
        assert "断点已保留" in capsys.readouterr().out
        # 流水线断点仍在
        s = g.get_state({"configurable": {"thread_id": old_tid}})
        assert s.next

    def test_exit_conversation_stage(self):
        g = _make_graph()
        cfg = {"configurable": {"thread_id": _fresh_thread_id("cmd_exit")}}
        state = _conversation_state(g, cfg)

        assert dispatch("/exit", g, cfg, state).action == "quit"

    def test_exit_mid_pipeline_requests_keep(self):
        g = _make_graph()
        cfg = {"configurable": {"thread_id": _fresh_thread_id("cmd_exit2")}}
        state = _pipeline_state(g, cfg)

        assert dispatch("/exit", g, cfg, state).action == "quit_keep"


def test_new_thread_id_unique_and_prefixed():
    a = new_thread_id("chat")
    b = new_thread_id("chat")
    assert a != b
    assert a.startswith("chat_")
