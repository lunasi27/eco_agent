from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

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


def test_event_loop_default_initial_input():
    """initial_input=None → default empty messages"""
    mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
    g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": _fresh_thread_id("el_def")}}

    with patch("builtins.input", side_effect=["setup", "stop"]), \
         patch("builtins.print"):
        result = run_event_loop(g, cfg)

    assert result is not None


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
