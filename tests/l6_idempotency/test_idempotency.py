from __future__ import annotations

import os
import uuid

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from src.graph_builder import build_graph
from src.mcp_server.mock import MockECOMCPServer
from src.utils.file_utils import build_run_dir


@pytest.fixture
def config():
    def _cfg(name: str) -> dict:
        return {"configurable": {"thread_id": f"L6_{name}_{uuid.uuid4().hex[:8]}"}}
    return _cfg


def _initial_input():
    return {
        "messages": [HumanMessage(content="帮我跑 designL6 的 ECO")],
        "design_name": "designL6",
    }


def _consume(graph, input_val, cfg):
    list(graph.stream(input_val, cfg))


# ============================================================
# L6-1: 不同 iteration 的 run_dir 覆盖安全
# ============================================================


class TestRunDirIsolation:
    def test_iteration1_and_iteration2_different_dirs(self, config):
        mcp = MockECOMCPServer(scenario="convergence", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
        cfg = config("run_dir_iso")

        _consume(g, _initial_input(), cfg)
        run_dir_iter1 = g.get_state(cfg).values.get("run_dir")

        _consume(g, Command(resume="setup"), cfg)
        _consume(g, Command(resume="continue"), cfg)
        run_dir_iter2 = g.get_state(cfg).values.get("run_dir")

        assert run_dir_iter1 != run_dir_iter2, (
            f"两次迭代 run_dir 应不同, 实际: {run_dir_iter1} vs {run_dir_iter2}"
        )
        assert run_dir_iter1.endswith("iter_001")
        assert run_dir_iter2.endswith("iter_002")

    def test_run_dir_format(self):
        d = build_run_dir("runs", "designX", 1)
        assert d == "runs/designX/iter_001"
        d2 = build_run_dir("runs", "designX", 10)
        assert d2 == "runs/designX/iter_010"


# ============================================================
# L6-2: Mock Server 幂等重复调用
# ============================================================


class TestMockServerIdempotency:
    def test_happy_path_scenario_reproducible(self):
        s1 = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
        s2 = MockECOMCPServer(scenario="happy_path", simulate_delay=0)

        assert s1.run_sta("d", "/tmp") == s2.run_sta("d", "/tmp")
        assert s1.run_pt_fix_setup("d", "/tmp", "setup") == s2.run_pt_fix_setup("d", "/tmp", "setup")

    def test_multiple_runs_same_results(self):
        server = MockECOMCPServer(scenario="convergence", simulate_delay=0)
        runs = [server.run_sta("d", "/tmp") for _ in range(10)]
        assert len(set(str(r) for r in runs)) == 1


# ============================================================
# L6-3: MemorySaver checkpoint 恢复一致性
# ============================================================


class TestCheckpointIdempotency:
    def test_same_thread_id_resume_is_consistent(self, config):
        """多次 resume + continue，中间不改变外部状态，checkpoint 应可重现。"""
        mcp = MockECOMCPServer(scenario="convergence", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
        cfg = config("ckpt_consistent")

        _consume(g, _initial_input(), cfg)

        state_at_phase2 = g.get_state(cfg).values
        setup_before = state_at_phase2.get("setup_vio")
        hold_before = state_at_phase2.get("hold_vio")
        assert setup_before == 80
        assert hold_before == 25

        _consume(g, Command(resume="setup"), cfg)

        state_at_phase3 = g.get_state(cfg).values
        assert state_at_phase3.get("setup_vio") == 15
        assert state_at_phase3.get("user_fix_strategy") == "setup"

    def test_separate_threads_independent_phase2_values(self):
        """MemorySaver 不同 thread_id 的 phase2 setup_vio/hold_vio 应独立保存。"""
        mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())

        threads = []
        for i in range(3):
            cfg = {"configurable": {"thread_id": f"L6_indep_{i}_{uuid.uuid4().hex[:6]}"}}
            _consume(g, {
                "messages": [HumanMessage(content=f"帮我跑 design{i} 的 ECO")],
                "design_name": f"design{i}",
            }, cfg)
            state_vals = g.get_state(cfg).values
            threads.append(state_vals)

        assert all(t["setup_vio"] == 125 for t in threads)
        assert all(t["hold_vio"] == 47 for t in threads)
        assert len(set(t["design_name"] for t in threads)) == 3
