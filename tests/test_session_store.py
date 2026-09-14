"""Session 生命周期管理测试：列举 / 分类 / 删除 checkpoint 会话。"""
from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from src.graph_builder import build_graph
from src.mcp_server.mock import MockECOMCPServer
from src.utils.session_store import (
    STATUS_COMPLETED,
    STATUS_INTERRUPTED,
    STATUS_MISSING,
    classify_snapshot,
    delete_thread,
    describe_sessions,
    list_thread_ids,
)


def _make_graph(checkpointer, scenario: str = "happy_path"):
    mcp = MockECOMCPServer(scenario=scenario, simulate_delay=0)
    return build_graph(mcp_server=mcp, checkpointer=checkpointer, skip_agent_entry=True)


def _pause_at_interrupt(graph, cfg, design: str = "designA"):
    """跑到第一个 interrupt（phase2_summary）停下。"""
    list(graph.stream({"design_name": design}, cfg))
    state = graph.get_state(cfg)
    assert state.next, "前置条件：图应停在 interrupt"
    return state


def _drive_to_end(graph, cfg):
    """把停在 phase2_summary 的图继续跑到 END。"""
    current = Command(resume="setup")
    while True:
        list(graph.stream(current, cfg))
        state = graph.get_state(cfg)
        if not state.next:
            return state
        current = Command(resume="stop")


@pytest.fixture
def sqlite_graph():
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "sessions.db")
        conn = sqlite3.connect(db_path, check_same_thread=False)
        saver = SqliteSaver(conn)
        g = _make_graph(saver)
        try:
            yield g, saver
        finally:
            conn.close()


class TestClassifySnapshot:
    def test_missing_thread(self, sqlite_graph):
        g, _ = sqlite_graph
        snap = g.get_state({"configurable": {"thread_id": "never_existed"}})
        assert classify_snapshot(snap) == STATUS_MISSING

    def test_interrupted_thread(self, sqlite_graph):
        g, _ = sqlite_graph
        cfg = {"configurable": {"thread_id": "paused_job"}}
        _pause_at_interrupt(g, cfg)
        assert classify_snapshot(g.get_state(cfg)) == STATUS_INTERRUPTED

    def test_completed_thread(self, sqlite_graph):
        g, _ = sqlite_graph
        cfg = {"configurable": {"thread_id": "done_job"}}
        _pause_at_interrupt(g, cfg)
        _drive_to_end(g, cfg)
        assert classify_snapshot(g.get_state(cfg)) == STATUS_COMPLETED


class TestSessionListing:
    def test_lists_and_describes_mixed_sessions(self, sqlite_graph):
        g, saver = sqlite_graph
        cfg_paused = {"configurable": {"thread_id": "job_paused"}}
        cfg_done = {"configurable": {"thread_id": "job_done"}}
        _pause_at_interrupt(g, cfg_paused, design="designP")
        _pause_at_interrupt(g, cfg_done, design="designD")
        _drive_to_end(g, cfg_done)

        tids = list_thread_ids(saver)
        assert set(tids) == {"job_paused", "job_done"}

        infos = describe_sessions(g, saver)
        by_id = {i.thread_id: i for i in infos}
        assert by_id["job_paused"].status == STATUS_INTERRUPTED
        assert by_id["job_paused"].design_name == "designP"
        assert by_id["job_done"].status == STATUS_COMPLETED
        assert by_id["job_done"].design_name == "designD"

        # 中断中的会话排前面
        assert infos[0].thread_id == "job_paused"

    def test_memory_backend_enumeration(self):
        saver = MemorySaver()
        g = _make_graph(saver)
        cfg = {"configurable": {"thread_id": "mem_job"}}
        _pause_at_interrupt(g, cfg)

        infos = describe_sessions(g, saver)
        assert len(infos) == 1
        assert infos[0].thread_id == "mem_job"
        assert infos[0].status == STATUS_INTERRUPTED

    def test_empty_backend(self):
        saver = MemorySaver()
        g = _make_graph(saver)
        assert describe_sessions(g, saver) == []


class TestDeleteThread:
    def test_delete_removes_checkpoint(self, sqlite_graph):
        g, saver = sqlite_graph
        cfg = {"configurable": {"thread_id": "job_to_delete"}}
        _pause_at_interrupt(g, cfg)
        assert "job_to_delete" in list_thread_ids(saver)

        delete_thread(saver, "job_to_delete")

        assert "job_to_delete" not in list_thread_ids(saver)
        assert classify_snapshot(g.get_state(cfg)) == STATUS_MISSING

    def test_delete_completed_session_allows_clean_rerun(self, sqlite_graph):
        """完成态 checkpoint 删除后，同 thread_id 重跑应是全新会话。"""
        g, saver = sqlite_graph
        cfg = {"configurable": {"thread_id": "rerun_job"}}
        _pause_at_interrupt(g, cfg, design="firstDesign")
        _drive_to_end(g, cfg)
        delete_thread(saver, "rerun_job")

        # 全新图实例模拟进程重启
        g2 = _make_graph(saver)
        state2 = _pause_at_interrupt(g2, cfg, design="secondDesign")
        assert state2.values["design_name"] == "secondDesign"
        assert classify_snapshot(g2.get_state(cfg)) == STATUS_INTERRUPTED

    def test_delete_from_memory_backend(self):
        saver = MemorySaver()
        g = _make_graph(saver)
        cfg = {"configurable": {"thread_id": "mem_del"}}
        _pause_at_interrupt(g, cfg)

        delete_thread(saver, "mem_del")
        assert classify_snapshot(g.get_state(cfg)) == STATUS_MISSING
