from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from src.graph_builder import build_graph
from src.mcp_server.mock import MockECOMCPServer


@pytest.fixture
def sqlite_ctx():
    """在一个 tmpdir 内创建 sqlite db，返回 db_path + 两个函数（用/不用 SqliteSaver 建 graph）。"""
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "test_checkpoint.db")
        _conns: list[sqlite3.Connection] = []

        def _make_graph(scenario: str = "happy_path"):
            conn = sqlite3.connect(db_path, check_same_thread=False)
            _conns.append(conn)
            saver = SqliteSaver(conn)
            mcp = MockECOMCPServer(scenario=scenario, simulate_delay=0)
            return build_graph(mcp_server=mcp, checkpointer=saver)

        try:
            yield db_path, _make_graph
        finally:
            for c in _conns:
                try:
                    c.close()
                except Exception:
                    pass


def _input(design_name: str = "designSqlite"):
    return {
        "messages": [HumanMessage(content=f"帮我跑 {design_name} 的 ECO")],
        "design_name": design_name,
    }


def _consume(graph, input_val, cfg):
    list(graph.stream(input_val, cfg))


class TestSqliteSaverPersistence:
    def test_separate_graph_instance_resumes_from_checkpoint(self, sqlite_ctx):
        """同一个 db_path 上创建的两个独立 graph 实例，应能互相续跑 checkpoint。"""
        db_path, make_graph = sqlite_ctx
        cfg = {"configurable": {"thread_id": "persistence_test"}}

        g1 = make_graph("happy_path")
        _consume(g1, _input(), cfg)

        s1 = g1.get_state(cfg)
        assert s1.next, "应该停在 phase2_summary interrupt"
        assert s1.values.get("setup_vio") == 125
        phase1_done_before = s1.values.get("step_status", {}).get("run_ext") == "done"
        assert phase1_done_before

        del g1

        g2 = make_graph("happy_path")
        _consume(g2, Command(resume="setup"), cfg)

        s2 = g2.get_state(cfg)
        assert s2.next, "phase3_summary interrupt"
        assert s2.values.get("setup_vio") == 30, (
            "续跑后应有 Mock fix 的 30 violations"
        )
        assert s2.values.get("user_fix_strategy") == "setup"

    def test_sqlite_db_file_created_and_has_content(self, sqlite_ctx):
        db_path, make_graph = sqlite_ctx
        cfg = {"configurable": {"thread_id": "db_content_test"}}

        g = make_graph("happy_path")
        _consume(g, _input(), cfg)

        assert os.path.exists(db_path)
        assert os.path.getsize(db_path) > 0

    def test_separate_thread_ids_independent_checkpoints(self, sqlite_ctx):
        db_path, make_graph = sqlite_ctx
        g = make_graph("happy_path")

        cfg_a = {"configurable": {"thread_id": "thread_A"}}
        cfg_b = {"configurable": {"thread_id": "thread_B"}}

        _consume(g, _input("designA"), cfg_a)
        _consume(g, _input("designB"), cfg_b)

        state_a = g.get_state(cfg_a).values
        state_b = g.get_state(cfg_b).values

        assert state_a.get("design_name") == "designA"
        assert state_b.get("design_name") == "designB"
        assert state_a.get("run_dir") != state_b.get("run_dir")


class TestSqliteSaverErrorRecovery:
    def test_error_handler_interrupt_saved_in_sqlite(self, sqlite_ctx):
        db_path, make_graph = sqlite_ctx
        g = make_graph("phase2_sta_error")
        cfg = {"configurable": {"thread_id": "err_sqlite"}}

        _consume(g, _input(), cfg)

        state = g.get_state(cfg)
        assert state.next, "Error Handler 应在 checkpoint 中被保留"
        assert "STA 执行超时" in state.values.get("error_msg", "")

    def test_error_handler_resume_abort_via_sqlite(self, sqlite_ctx):
        db_path, make_graph = sqlite_ctx
        cfg = {"configurable": {"thread_id": "err_abort_sqlite"}}

        g1 = make_graph("phase2_sta_error")
        _consume(g1, _input(), cfg)

        s = g1.get_state(cfg)
        assert s.next == ("error_handler",), f"应停在 error_handler, 实际: {s.next}"
        assert len(s.interrupts) == 1

        del g1

        g2 = make_graph("happy_path")
        abort_events = [
            list(ev.keys())[0]
            for ev in g2.stream(Command(resume="abort"), cfg)
        ]

        assert "finalize" in abort_events, f"abort 后应走 finalize, 实际: {abort_events}"
        s2 = g2.get_state(cfg)
        assert not s2.next  # abort + finalize 后应该结束
