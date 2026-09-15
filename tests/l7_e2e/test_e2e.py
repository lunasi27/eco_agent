from __future__ import annotations

import os
import uuid

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from src.graph_builder import build_graph
from src.mcp_server.mock import MockECOMCPServer
from src.utils.constants import PHASE_STEPS, PHASES


@pytest.fixture
def config():
    def _cfg(name: str) -> dict:
        return {"configurable": {"thread_id": f"L7_{name}_{uuid.uuid4().hex[:8]}"}}
    return _cfg


def _initial_input(design_name: str = "designL7"):
    return {
        "messages": [HumanMessage(content=f"帮我跑 {design_name} 的 ECO")],
        "design_name": design_name,
    }


def _consume(graph, input_val, cfg):
    list(graph.stream(input_val, cfg))


# ============================================================
# L7-1: happy_path 完整端到端（单轮迭代 stop）
# ============================================================


class TestHappyPathE2E:
    def test_full_pipeline_single_iteration(self, config):
        mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver(), skip_agent_entry=True)
        cfg = config("happy_full")

        _consume(g, _initial_input(), cfg)
        _consume(g, Command(resume="setup"), cfg)
        _consume(g, Command(resume="stop"), cfg)

        s = g.get_state(cfg)
        assert not s.next, "完整跑通后应无后续中断"

        v = s.values
        # finalize 清空 design_name / iteration_cnt 回到对话模式
        assert v.get("design_name") == ""
        assert v.get("iteration_cnt") == 0
        assert v.get("setup_vio") == 30
        assert v.get("hold_vio") == 47
        assert v.get("pv_pass") is True
        assert v.get("signoff_pass") is True

    def test_finalize_writes_finished_file(self, config):
        mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver(), skip_agent_entry=True)
        cfg = config("finished_file")

        _consume(g, _initial_input("designFin"), cfg)
        _consume(g, Command(resume="hold"), cfg)
        _consume(g, Command(resume="stop"), cfg)

        v = g.get_state(cfg).values
        finished_path = os.path.join(v.get("run_dir", ""), "FINISHED")
        assert os.path.exists(finished_path), f"Finalize 应写 FINISHED 文件: {finished_path}"

        with open(finished_path) as f:
            content = f.read()
        assert "designFin" in content
        assert "Final Setup" in content or "Setup 收敛率" in content

    def test_run_dir_created_for_each_iteration(self, config):
        mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver(), skip_agent_entry=True)
        cfg = config("run_dir_created")

        _consume(g, _initial_input("designDir"), cfg)
        v = g.get_state(cfg).values

        assert os.path.exists(v["run_dir"]), f"run_dir 应被创建: {v['run_dir']}"


# ============================================================
# L7-2: convergence 多轮迭代端到端
# ============================================================


class TestConvergenceE2E:
    def test_two_iterations_then_stop(self, config):
        mcp = MockECOMCPServer(scenario="convergence", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver(), skip_agent_entry=True)
        cfg = config("conv_2iter")

        _consume(g, _initial_input("designConv"), cfg)

        _consume(g, Command(resume="setup"), cfg)
        _consume(g, Command(resume="continue"), cfg)

        v_iter2_after_phase1 = g.get_state(cfg).values
        assert v_iter2_after_phase1.get("iteration_cnt") == 2

        _consume(g, Command(resume="hold"), cfg)
        _consume(g, Command(resume="stop"), cfg)

        s = g.get_state(cfg)
        assert not s.next
        # finalize 清空 iteration_cnt 回到对话模式
        assert s.values.get("iteration_cnt") == 0

    def test_prev_violation_compared_across_iterations(self, config):
        mcp = MockECOMCPServer(scenario="convergence", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver(), skip_agent_entry=True)
        cfg = config("prev_compare")

        _consume(g, _initial_input("designCompare"), cfg)
        _consume(g, Command(resume="setup"), cfg)

        v_after_iter1_fix = g.get_state(cfg).values
        assert v_after_iter1_fix.get("setup_vio") == 15
        assert v_after_iter1_fix.get("prev_setup_vio") == 80


# ============================================================
# L7-3: 状态机完整性 —— 所有 phase_status 覆盖
# ============================================================


class TestStateMachineCompleteness:
    def test_phase_status_completed_after_success(self, config):
        mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver(), skip_agent_entry=True)
        cfg = config("phase_complete")

        _consume(g, _initial_input(), cfg)
        _consume(g, Command(resume="setup"), cfg)
        _consume(g, Command(resume="stop"), cfg)

        phase_status = g.get_state(cfg).values.get("phase_status", {})
        for phase in PHASES:
            assert phase_status.get(phase) == "done", (
                f"phase_status[{phase}] 应为 'done', 实际: {phase_status.get(phase)}"
            )

    def test_step_status_completed_after_success(self, config):
        mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver(), skip_agent_entry=True)
        cfg = config("step_complete")

        _consume(g, _initial_input(), cfg)
        _consume(g, Command(resume="setup"), cfg)
        _consume(g, Command(resume="stop"), cfg)

        step_status = g.get_state(cfg).values.get("step_status", {})
        expected_steps = [
            "init", "run_eco_route", "run_ext",
            "run_sta", "run_pv", "run_signoff",
            "run_pt_fix_setup",
        ]
        for step in expected_steps:
            assert step_status.get(step) == "done", (
                f"step_status[{step}] 应为 'done', 实际: {step_status.get(step)}"
            )

    def test_all_phases_present_in_phase_status_dict(self, config):
        mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver(), skip_agent_entry=True)
        cfg = config("all_phases")

        _consume(g, _initial_input(), cfg)
        _consume(g, Command(resume="setup"), cfg)
        _consume(g, Command(resume="stop"), cfg)

        phase_status = g.get_state(cfg).values.get("phase_status", {})
        for phase in PHASES:
            assert phase in phase_status, f"phase_status 缺少 {phase}"


# ============================================================
# L7-4: 错误 scenario 的 E2E 完整状态追踪
# ============================================================


class TestErrorScenarioE2E:
    def test_phase2_pv_error_has_correct_error_message(self, config):
        mcp = MockECOMCPServer(scenario="phase2_pv_error", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver(), skip_agent_entry=True)
        cfg = config("pv_err_e2e")

        _consume(g, _initial_input("designErr"), cfg)

        v = g.get_state(cfg).values
        assert v.get("error_msg", "").startswith("[run_pv] ")
        assert "LVS" in v["error_msg"]
        assert v.get("step_status", {}).get("run_pv") == "error"
        assert v.get("step_status", {}).get("run_sta") == "done"
        assert v.get("step_status", {}).get("run_signoff") == "done"

    def test_phase2_signoff_error(self, config):
        mcp = MockECOMCPServer(scenario="phase2_signoff_error", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver(), skip_agent_entry=True)
        cfg = config("signoff_err_e2e")

        _consume(g, _initial_input(), cfg)

        v = g.get_state(cfg).values
        assert "Signoff" in v.get("error_msg", "")
        assert v.get("step_status", {}).get("run_signoff") == "error"
