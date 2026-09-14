from __future__ import annotations

import uuid

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from src.graph_builder import build_graph
from src.mcp_server.mock import MockECOMCPServer


@pytest.fixture
def graph_happy():
    mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
    return build_graph(mcp_server=mcp, checkpointer=MemorySaver())


@pytest.fixture
def graph_conv():
    mcp = MockECOMCPServer(scenario="convergence", simulate_delay=0)
    return build_graph(mcp_server=mcp, checkpointer=MemorySaver())


@pytest.fixture
def config():
    def _cfg(name: str) -> dict:
        return {"configurable": {"thread_id": f"test_{name}_{uuid.uuid4().hex[:8]}"}}
    return _cfg


def _initial_input():
    return {
        "messages": [HumanMessage(content="帮我跑 designE2E 的 ECO")],
        "design_name": "designE2E",
    }


def _stream_nodes(graph, initial_input, cfg) -> list[str]:
    events = list(graph.stream(initial_input, cfg))
    return [list(ev.keys())[0] for ev in events]


# ============================================================
# L3-1: Phase1 串行拓扑
# ============================================================


class TestPhase1SerialTopology:
    def test_init_to_run_eco_route_to_run_ext_order(self, graph_happy, config):
        cfg = config("p1_serial")
        nodes = _stream_nodes(graph_happy, _initial_input(), cfg)
        assert "init" in nodes
        assert "run_eco_route" in nodes
        assert "run_ext" in nodes
        assert nodes.index("init") < nodes.index("run_eco_route") < nodes.index("run_ext")

    def test_no_duplicate_phase1_nodes(self, graph_happy, config):
        cfg = config("p1_nodupe")
        nodes = _stream_nodes(graph_happy, _initial_input(), cfg)
        assert nodes.count("run_eco_route") == 1
        assert nodes.count("run_ext") == 1


# ============================================================
# L3-2: Phase2 并行 Send 拓扑
# ============================================================


class TestPhase2ParallelTopology:
    def test_three_parallel_steps_all_execute(self, graph_happy, config):
        cfg = config("p2_parallel")
        nodes = _stream_nodes(graph_happy, _initial_input(), cfg)
        assert "run_sta" in nodes
        assert "run_pv" in nodes
        assert "run_signoff" in nodes

    def test_phase2_state_merge_correct(self, graph_happy, config):
        cfg = config("p2_merge")
        list(graph_happy.stream(_initial_input(), cfg))
        state = graph_happy.get_state(cfg).values
        assert state["setup_vio"] == 125
        assert state["hold_vio"] == 47
        assert state["pv_pass"] is True
        assert state["signoff_pass"] is True

    def test_phase2_summary_triggers_interrupt(self, graph_happy, config):
        cfg = config("p2_interrupt")
        list(graph_happy.stream(_initial_input(), cfg))
        state = graph_happy.get_state(cfg)
        assert state.next, "phase2_summary 应该触发 interrupt"
        assert state.values.get("current_phase") == "phase2"
        assert state.interrupts
        interrupt_value = state.interrupts[0].value
        assert "Phase2 完成" in str(interrupt_value)


# ============================================================
# L3-3: Phase3 分支路由
# ============================================================


class TestPhase3Branching:
    def test_setup_strategy_runs_fix_setup(self, graph_happy, config):
        cfg = config("p3_setup")
        list(graph_happy.stream(_initial_input(), cfg))
        list(graph_happy.stream(Command(resume="setup"), cfg))
        state = graph_happy.get_state(cfg).values
        assert state.get("setup_vio") == 30
        assert state.get("user_fix_strategy") == "setup"

    def test_hold_strategy_runs_fix_hold(self, graph_happy, config):
        cfg = config("p3_hold")
        list(graph_happy.stream(_initial_input(), cfg))
        list(graph_happy.stream(Command(resume="hold"), cfg))
        state = graph_happy.get_state(cfg).values
        assert state.get("hold_vio") == 10
        assert state.get("user_fix_strategy") == "hold"


# ============================================================
# L3-4: 迭代循环
# ============================================================


class TestIterationLoop:
    def test_continue_loops_back_to_init(self, graph_conv, config):
        cfg = config("loop_continue")
        list(graph_conv.stream(_initial_input(), cfg))
        list(graph_conv.stream(Command(resume="setup"), cfg))

        continue_events = [
            list(ev.keys())[0]
            for ev in graph_conv.stream(Command(resume="continue"), cfg)
        ]
        assert "init" in continue_events, f"continue 后应回到 init, 实际事件: {continue_events}"

        state = graph_conv.get_state(cfg).values
        assert state.get("iteration_cnt") == 2, f"应自增到 2, 实际 {state.get('iteration_cnt')}"

        assert "run_eco_route" in continue_events
        assert "run_ext" in continue_events

    def test_stop_goes_to_finalize_then_end(self, graph_happy, config):
        cfg = config("loop_stop")
        list(graph_happy.stream(_initial_input(), cfg))
        list(graph_happy.stream(Command(resume="setup"), cfg))
        list(graph_happy.stream(Command(resume="stop"), cfg))

        state = graph_happy.get_state(cfg)
        assert not state.next, f"stop 后应无后续中断，实际: {state}"


# ============================================================
# L3-5: 错误路由
# ============================================================


class TestErrorRouting:
    def test_phase2_sta_error_routes_to_error_handler(self, config):
        mcp = MockECOMCPServer(scenario="phase2_sta_error", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
        cfg = config("err_sta")

        list(g.stream(_initial_input(), cfg))
        state = g.get_state(cfg)
        assert state.next, "Error Handler 应触发 interrupt"
        assert "STA 执行超时" in state.values.get("error_msg", "")

    def test_phase1_ext_error_routes_to_error_handler(self, config):
        mcp = MockECOMCPServer(scenario="phase1_ext_error", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
        cfg = config("err_ext")

        list(g.stream(_initial_input(), cfg))
        state = g.get_state(cfg)
        assert state.next
        assert "StarRCX license" in state.values.get("error_msg", "")

    def test_phase3_fix_error_routes_to_error_handler(self, config):
        mcp = MockECOMCPServer(scenario="phase3_fix_error", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
        cfg = config("err_fix")

        list(g.stream(_initial_input(), cfg))
        list(g.stream(Command(resume="setup"), cfg))
        state = g.get_state(cfg)
        assert state.next, "phase3 出错后 Error Handler 应触发 interrupt"
        assert "ECO Fix 工具异常" in state.values.get("error_msg", "")


# ============================================================
# L3-6: 图拓扑注册完整性
# ============================================================


class TestFullTopologyRegistration:
    EXPECTED_NODES = {
        "agent_entry",
        "init", "run_eco_route", "run_ext",
        "run_sta", "run_pv", "run_signoff", "phase2_gate", "phase2_summary",
        "run_pt_fix_setup", "run_pt_fix_hold", "run_pt_fix_leakage", "phase3_gate", "phase3_summary",
        "error_handler", "finalize", "chat_fallback",
    }

    def test_all_expected_nodes_registered(self, graph_happy):
        registered = {k for k in graph_happy.nodes.keys() if not k.startswith("__")}
        missing = self.EXPECTED_NODES - registered
        extra = registered - self.EXPECTED_NODES
        assert not missing and not extra, (
            f"节点注册不匹配。缺失: {missing}, 多余: {extra}"
        )

    def test_get_state_initial(self, graph_happy, config):
        cfg = config("initial")
        list(graph_happy.stream(_initial_input(), cfg))
        state = graph_happy.get_state(cfg).values
        assert "design_name" in state
        assert "setup_vio" in state
        assert "hold_vio" in state


# ============================================================
# L3-7: phase2_gate / phase3_gate 边界分支
# ============================================================


class TestGateEdgeCases:
    def test_phase2_gate_running_when_pending(self):
        from src.nodes.node_gates import node_phase2_gate

        state = {
            "step_status": {"run_sta": "done", "run_pv": "done", "run_signoff": "pending"},
            "phase_status": {},
        }
        result = node_phase2_gate(state)
        assert result["phase_status"]["phase2"] == "running"

    def test_phase2_gate_error_takes_priority_over_pending(self):
        from src.nodes.node_gates import node_phase2_gate

        state = {
            "step_status": {"run_sta": "error", "run_pv": "done", "run_signoff": "pending"},
            "phase_status": {},
        }
        result = node_phase2_gate(state)
        assert result["phase_status"]["phase2"] == "error"

    def test_phase2_gate_done_when_all_finished(self):
        from src.nodes.node_gates import node_phase2_gate

        state = {
            "step_status": {"run_sta": "done", "run_pv": "done", "run_signoff": "done"},
            "phase_status": {},
        }
        result = node_phase2_gate(state)
        assert result["phase_status"]["phase2"] == "done"

    def test_phase3_gate_fallback_to_all_fix_steps_when_none_executed(self):
        from src.nodes.node_gates import node_phase3_gate

        state = {
            "step_status": {},
            "phase_status": {},
        }
        result = node_phase3_gate(state)
        assert result["phase_status"]["phase3"] == "running"

    def test_phase3_gate_uses_only_executed_steps(self):
        from src.nodes.node_gates import node_phase3_gate

        state = {
            "step_status": {"run_pt_fix_setup": "done"},
            "phase_status": {},
        }
        result = node_phase3_gate(state)
        assert result["phase_status"]["phase3"] == "done", (
            "只执行过 run_pt_fix_setup 且 done, gate 应认为已完成"
        )

    def test_phase3_gate_error_in_executed_step_takes_priority(self):
        from src.nodes.node_gates import node_phase3_gate

        state = {
            "step_status": {"run_pt_fix_setup": "done", "run_pt_fix_hold": "error"},
            "phase_status": {},
        }
        result = node_phase3_gate(state)
        assert result["phase_status"]["phase3"] == "error"

    def test_phase3_gate_done_when_all_executed_steps_done(self):
        from src.nodes.node_gates import node_phase3_gate

        state = {
            "step_status": {"run_pt_fix_setup": "done", "run_pt_fix_hold": "done"},
            "phase_status": {},
        }
        result = node_phase3_gate(state)
        assert result["phase_status"]["phase3"] == "done"


# ============================================================
# L3-8: node_finalize 边界 case
# ============================================================


class TestFinalizeEdgeCases:
    def test_node_finalize_run_dir_without_rpt_files(self, tmp_path):
        import os
        from src.nodes.node_finalize import node_finalize

        empty_run = tmp_path / "empty_run"
        empty_run.mkdir()
        (empty_run / "run_sta.log").write_text("DONE")

        node_finalize({
            "design_name": "empty_rpt_test",
            "iteration_cnt": 1,
            "run_dir": str(empty_run),
            "setup_vio": 100,
            "hold_vio": 5,
            "pv_pass": False,
            "signoff_pass": False,
            "step_elapsed": {"run_sta": 0.5, "run_ext": 1.2},
            "iteration_history": [],
        })

        finished = empty_run / "FINISHED"
        assert finished.exists(), "FINISHED 应被生成"
        content = finished.read_text()
        assert "(none)" in content, "无 .rpt 时应在 FINISHED 输出 (none)"

    def test_node_finalize_run_dir_nonexistent(self):
        from src.nodes.node_finalize import node_finalize

        result = node_finalize({
            "design_name": "no_run_dir",
            "iteration_cnt": 1,
            "run_dir": "/no/such/run_dir_xyz",
            "setup_vio": 100,
            "hold_vio": 5,
            "pv_pass": False,
            "signoff_pass": False,
            "step_elapsed": {"run_sta": 0.5},
            "iteration_history": [],
        })

        assert result["current_phase"] == "finalize"

    def test_node_finalize_empty_iteration_history(self):
        from src.nodes.node_finalize import node_finalize

        result = node_finalize({
            "design_name": "no_history",
            "iteration_cnt": 0,
            "run_dir": "",
            "setup_vio": 0,
            "hold_vio": 0,
            "pv_pass": True,
            "signoff_pass": True,
            "step_elapsed": {},
            "iteration_history": [],
        })

        assert result["current_phase"] == "finalize"
        assert "no_history" in result["interrupt_msg"]
