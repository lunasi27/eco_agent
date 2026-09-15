from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from src.calling_layer.commands import SessionExit
from src.calling_layer.event_loop import run_event_loop
from src.graph_builder import build_graph
from src.mcp_server.mock import MockECOMCPServer


def _fresh_thread_id(name: str) -> str:
    return f"{name}_{int(time.time()*1000)}"


def test_event_loop_happy_path_two_interrupts():
    """phase2_summary(setup) → phase3_summary(stop) → finalize"""
    mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
    g = build_graph(mcp_server=mcp, checkpointer=MemorySaver(), skip_agent_entry=True)
    cfg = {"configurable": {"thread_id": _fresh_thread_id("el_hp")}}

    with patch("builtins.input", side_effect=["setup", "stop"]), \
         patch("builtins.print"):
        result = run_event_loop(g, cfg, initial_input={"design_name": "d"})

    assert result is not None
    assert "setup_vio" in result
    assert "hold_vio" in result


def test_event_loop_leakage_strategy():
    """phase2_summary(leakage) → phase3_summary(stop) → finalize"""
    mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
    g = build_graph(mcp_server=mcp, checkpointer=MemorySaver(), skip_agent_entry=True)
    cfg = {"configurable": {"thread_id": _fresh_thread_id("el_lk")}}

    with patch("builtins.input", side_effect=["leakage", "stop"]), \
         patch("builtins.print"):
        result = run_event_loop(g, cfg, initial_input={"design_name": "d"})

    assert result is not None
    history = result.get("iteration_history", [])
    assert len(history) == 1
    assert history[0]["fix_strategy"] == "leakage"


def test_event_loop_fresh_conversation_explicit_messages():
    """新会话需显式传 {"messages": []}：agent_entry 先 interrupt 打招呼，
    用户下达 ECO 指令后流水线才开始。"""
    mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
    g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": _fresh_thread_id("el_def")}}

    with patch("builtins.input", side_effect=["帮我跑 designA 的 ECO", "setup", "stop", "/exit"]), \
         patch("builtins.print"):
        result = run_event_loop(g, cfg, initial_input={"messages": []})

    assert result is not None
    # finalize 清空 design_name 回到对话模式，此处验证流水线确实跑过
    assert "setup_vio" in result
    assert result["design_name"] == ""


def test_event_loop_resume_none_input_continues_checkpoint():
    """initial_input=None 是 resume 语义：从 checkpoint 断点继续，不能触发 __start__。"""
    mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
    g = build_graph(mcp_server=mcp, checkpointer=MemorySaver(), skip_agent_entry=True)
    cfg = {"configurable": {"thread_id": _fresh_thread_id("el_resume")}}

    # 先跑到 phase2_summary 的 interrupt 停下
    list(g.stream({"design_name": "d"}, cfg))
    state = g.get_state(cfg)
    assert state.next  # 确认确实停在中断点

    # resume：继续跑完
    with patch("builtins.input", side_effect=["setup", "stop"]), \
         patch("builtins.print"):
        result = run_event_loop(g, cfg, initial_input=None)

    assert result is not None
    assert "setup_vio" in result


def test_event_loop_graph_finalize_end_state():
    """图执行完毕后 state.next 为空"""
    mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
    g = build_graph(mcp_server=mcp, checkpointer=MemorySaver(), skip_agent_entry=True)
    cfg = {"configurable": {"thread_id": _fresh_thread_id("el_final")}}

    with patch("builtins.input", side_effect=["setup", "stop"]), \
         patch("builtins.print"):
        result = run_event_loop(g, cfg, initial_input={"design_name": "d"})

    s = g.get_state(cfg)
    assert not s.next
    assert result["setup_vio"] is not None


def test_event_loop_exit_command_during_conversation():
    """对话阶段输入 /exit → 事件循环立即返回，不继续 resume 图"""
    mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
    g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": _fresh_thread_id("el_quit")}}

    with patch("builtins.input", side_effect=["/exit"]), \
         patch("builtins.print"):
        result = run_event_loop(g, cfg, initial_input={"messages": []})

    # 停在 agent_entry 的 interrupt 上，没有进入流水线
    assert result.get("design_name", "") == ""
    s = g.get_state(cfg)
    assert s.next == ("agent_entry",)


def test_event_loop_exit_mid_pipeline_keeps_checkpoint():
    """流水线中断点 /exit → 抛 SessionExit（断点保留），不能当成正常结束"""
    mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
    g = build_graph(mcp_server=mcp, checkpointer=MemorySaver(), skip_agent_entry=True)
    cfg = {"configurable": {"thread_id": _fresh_thread_id("el_mid")}}

    list(g.stream({"design_name": "d"}, cfg))  # 停在 phase2_summary
    with patch("builtins.input", side_effect=["/exit"]), \
         patch("builtins.print"):
        with pytest.raises(SessionExit):
            run_event_loop(g, cfg, initial_input=None)

    # 断点仍在，可续跑
    state = g.get_state(cfg)
    assert state.next, "流水线断点应保留"


def test_event_loop_status_command_mid_pipeline():
    """流水线提示等待答案时输 /status：本地处理后图仍挂起，答案不被吞"""
    mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
    g = build_graph(mcp_server=mcp, checkpointer=MemorySaver(), skip_agent_entry=True)
    cfg = {"configurable": {"thread_id": _fresh_thread_id("el_st")}}

    list(g.stream({"design_name": "d"}, cfg))  # 停在 phase2_summary
    with patch("builtins.input", side_effect=["/status", "setup", "stop"]), \
         patch("builtins.print"):
        result = run_event_loop(g, cfg, initial_input=None)

    assert "setup_vio" in result, "/status 后流水线应正常继续跑完"


def test_event_loop_invalid_inputs_at_both_gates_reprompt():
    """两个断点处乱输入都必须原地重问，直到拿到合法值，流程正常跑完。"""
    mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
    g = build_graph(mcp_server=mcp, checkpointer=MemorySaver(), skip_agent_entry=True)
    cfg = {"configurable": {"thread_id": _fresh_thread_id("el_invalid")}}

    # phase2: 乱码、旧别名(设置) → 重问；setup 放行
    # phase3: 乱码 → 重问；stop 放行
    with patch("builtins.input", side_effect=["asdf", "设置", "setup", "嗯嗯", "stop"]), \
         patch("builtins.print") as mock_print:
        result = run_event_loop(g, cfg, initial_input={"design_name": "d"})

    printed = "\n".join(
        str(call.args[0]) for call in mock_print.call_args_list if call.args
    )
    assert printed.count("无效输入") >= 2, "phase2 与 phase3 各至少提示一次无效输入"
    assert "setup_vio" in result
    assert len(result.get("iteration_history", [])) == 1, (
        "乱输入重问不得产生迭代历史记录"
    )


def test_event_loop_run_eco_command_in_conversation():
    """/run_eco designA → 合成自然语言走意图解析 → 进流水线"""
    mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
    g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": _fresh_thread_id("el_runeco")}}

    with patch("builtins.input", side_effect=["/run_eco designA", "setup", "stop", "/exit"]), \
         patch("builtins.print"):
        result = run_event_loop(g, cfg, initial_input={"messages": []})

    # finalize 清空 design_name 回到对话模式，验证流水线确实跑过
    assert result["design_name"] == ""
    assert "setup_vio" in result
