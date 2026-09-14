from __future__ import annotations

import os
import tempfile

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from src.graph_builder import build_graph
from src.mcp_server.mock import MockECOMCPServer


def _list_files(run_dir: str, suffix: str) -> list[str]:
    if not os.path.isdir(run_dir):
        return []
    return sorted(f for f in os.listdir(run_dir) if f.endswith(suffix))


def _read_text(run_dir: str, fname: str) -> str:
    with open(os.path.join(run_dir, fname)) as f:
        return f.read()


class TestMockLogDiskWrite:
    def test_happy_path_writes_all_logs(self):
        with tempfile.TemporaryDirectory() as td:
            mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
            rd = os.path.join(td, "runs", "d", "iter_001")

            mcp.run_eco_route("d", rd)
            mcp.run_ext("d", rd)
            mcp.run_sta("d", rd)
            mcp.run_pv("d", rd)
            mcp.run_signoff("d", rd)

            logs = _list_files(rd, ".log")
            rpts = _list_files(rd, ".rpt")
            assert len(logs) == 5
            assert len(rpts) == 5
            for step in ("run_eco_route", "run_ext", "run_sta", "run_pv", "run_signoff"):
                assert f"{step}.log" in logs
                assert f"{step}.rpt" in rpts

    def test_step_log_has_done_status_and_data(self):
        with tempfile.TemporaryDirectory() as td:
            mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
            rd = os.path.join(td, "runs", "d", "iter_001")

            mcp.run_sta("d", rd)
            content = _read_text(rd, "run_sta.log")

            assert "Status    : DONE" in content
            assert "Exit code: 0" in content
            assert "setup_vio = 125" in content
            assert "hold_vio = 47" in content

    def test_step_log_has_error_status_on_failure(self):
        with tempfile.TemporaryDirectory() as td:
            mcp = MockECOMCPServer(scenario="phase2_sta_error", simulate_delay=0)
            rd = os.path.join(td, "runs", "d", "iter_001")

            mcp.run_eco_route("d", rd)
            mcp.run_ext("d", rd)
            try:
                mcp.run_sta("d", rd)
            except Exception:
                pass

            content = _read_text(rd, "run_sta.log")
            assert "Status    : ERROR" in content
            assert "Exit code: non-zero" in content
            assert "STA 执行超时" in content

    def test_fix_step_logs_are_written(self):
        with tempfile.TemporaryDirectory() as td:
            mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
            rd = os.path.join(td, "runs", "d", "iter_001")

            mcp.run_pt_fix_setup("d", rd, fix_strategy="setup")
            mcp.run_pt_fix_hold("d", rd, fix_strategy="hold")

            logs = _list_files(rd, ".log")
            assert "run_pt_fix_setup.log" in logs
            assert "run_pt_fix_hold.log" in logs
            content = _read_text(rd, "run_pt_fix_setup.log")
            assert "Status    : DONE" in content
            assert "setup_vio = 30" in content

    def test_scenario_is_embedded_in_log_header(self):
        with tempfile.TemporaryDirectory() as td:
            mcp = MockECOMCPServer(scenario="phase2_pv_error", simulate_delay=0)
            rd = os.path.join(td, "runs", "d", "iter_001")

            mcp.run_sta("d", rd)
            try:
                mcp.run_pv("d", rd)
            except Exception:
                pass

            sta_log = _read_text(rd, "run_sta.log")
            pv_log = _read_text(rd, "run_pv.log")
            assert "Scenario  : phase2_pv_error" in sta_log
            assert "Scenario  : phase2_pv_error" in pv_log


class TestMockExecutionLog:
    def test_execution_log_tracks_each_step(self):
        mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
        with tempfile.TemporaryDirectory() as td:
            rd = os.path.join(td, "runs", "d", "iter_001")
            mcp.run_eco_route("d", rd)
            mcp.run_ext("d", rd)
            mcp.run_sta("d", rd)

        assert len(mcp.execution_log) == 3
        steps = [e["step"] for e in mcp.execution_log]
        assert steps == ["run_eco_route", "run_ext", "run_sta"]
        for entry in mcp.execution_log:
            assert entry["status"] == "done"
            assert entry["design"] == "d"
            assert entry["elapsed_s"] >= 0

    def test_execution_log_records_error_entries(self):
        mcp = MockECOMCPServer(scenario="phase2_sta_error", simulate_delay=0)
        with tempfile.TemporaryDirectory() as td:
            rd = os.path.join(td, "runs", "d", "iter_001")
            mcp.run_eco_route("d", rd)
            try:
                mcp.run_sta("d", rd)
            except Exception:
                pass

        assert len(mcp.execution_log) == 2
        assert mcp.execution_log[0]["status"] == "done"
        assert mcp.execution_log[1]["step"] == "run_sta"
        assert mcp.execution_log[1]["status"] == "error"


class TestFinalizeLogAggregation:
    def test_finished_file_lists_all_mock_logs(self):
        mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
        cfg = {"configurable": {"thread_id": f"fin_log_{os.getpid()}"}}

        list(g.stream(
            {"messages": [HumanMessage(content="帮我跑 d 的 ECO")], "design_name": "d"},
            cfg,
        ))
        list(g.stream(Command(resume="setup"), cfg))
        list(g.stream(Command(resume="stop"), cfg))

        rd = g.get_state(cfg).values["run_dir"]
        finished_path = os.path.join(rd, "FINISHED")
        assert os.path.exists(finished_path), f"FINISHED not found at {finished_path}"
        finished = _read_text(rd, "FINISHED")

        assert "日志文件（7）" in finished
        assert "run_eco_route.log" in finished
        assert "run_sta.log" in finished
        assert "run_pt_fix_setup.log" in finished
        assert "[✓] run_eco_route.log  (status=DONE)" in finished
        assert "报告文件（7）" in finished
        assert "run_pt_fix_setup.rpt" in finished

    def test_finished_file_has_convergence_comparison(self):
        mcp = MockECOMCPServer(scenario="convergence", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
        cfg = {"configurable": {"thread_id": f"fin_conv_{os.getpid()}"}}

        list(g.stream(
            {"messages": [HumanMessage(content="帮我跑 d 的 ECO")], "design_name": "d"},
            cfg,
        ))
        list(g.stream(Command(resume="setup"), cfg))
        list(g.stream(Command(resume="continue"), cfg))
        list(g.stream(Command(resume="setup"), cfg))
        list(g.stream(Command(resume="stop"), cfg))

        rd = g.get_state(cfg).values["run_dir"]
        finished_path = os.path.join(rd, "FINISHED")
        assert os.path.exists(finished_path), f"FINISHED not found at {finished_path}"
        finished = _read_text(rd, "FINISHED")
        assert "收敛历史" in finished
        assert "→" in finished
        assert "Setup 收敛率" in finished
