from __future__ import annotations

import os
import tempfile

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from src.mcp_server.mock import MockECOMCPServer
from src.nodes.node_error_handler import make_error_handler_node
from src.nodes.node_init import make_init_node
from src.routers.phase_routes import route_after_init
from src.state import ECOState
from src.utils.file_utils import (
    check_env_vars,
    check_tool_available,
    find_design_files,
    validate_design_dir,
)


class TestFileUtilsDesignDetection:
    def test_find_design_files_in_empty_dir(self):
        with tempfile.TemporaryDirectory() as td:
            files = find_design_files(td)
            assert files == {"netlist": [], "sdc": []}

    def test_find_design_files_discovers_netlist(self):
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, "rtl"))
            open(os.path.join(td, "rtl", "top.v"), "w").close()
            open(os.path.join(td, "rtl", "sub.sv"), "w").close()
            open(os.path.join(td, "rtl", "other.txt"), "w").close()

            files = find_design_files(td)
            names = sorted(os.path.basename(p) for p in files["netlist"])
            assert "top.v" in names
            assert "sub.sv" in names
            assert len(files["netlist"]) == 2

    def test_validate_design_dir_nonexistent(self):
        result = validate_design_dir("/no/such/path", require_netlist=True)
        assert not result["ok"]
        assert "不存在" in result["errors"][0]
        assert result["exists"] is False

    def test_validate_design_dir_no_netlist(self):
        with tempfile.TemporaryDirectory() as td:
            result = validate_design_dir(td, require_netlist=True)
            assert not result["ok"]
            assert len(result["errors"]) == 1

    def test_validate_design_dir_ok(self):
        with tempfile.TemporaryDirectory() as td:
            open(os.path.join(td, "top.v"), "w").close()
            open(os.path.join(td, "cst.sdc"), "w").close()
            result = validate_design_dir(td, require_netlist=True)
            assert result["ok"]
            assert result["netlist_count"] == 1
            assert result["sdc_count"] == 1


class TestEnvAndToolChecks:
    def test_check_env_vars_missing(self):
        os.environ.pop("MY_CUSTOM_VAR", None)
        result = check_env_vars(["MY_CUSTOM_VAR", "PATH"])
        assert result["missing"] == ["MY_CUSTOM_VAR"]
        assert result["present"] == 1
        assert result["total"] == 2

    def test_check_env_vars_all_present(self):
        result = check_env_vars(["PATH"])
        assert result["missing"] == []
        assert result["present"] == 1

    def test_check_tool_available_found(self):
        assert check_tool_available("python3") or check_tool_available("python")

    def test_check_tool_available_not_found(self):
        assert not check_tool_available("totally_nonexistent_tool_xyz_12345")


def _make_init_graph(**options):
    b = StateGraph(ECOState)
    b.add_node("init", make_init_node(None, **options))
    b.add_node("run_eco_route", lambda s: {"current_step": "run_eco_route"})
    b.add_node("error_handler", make_error_handler_node())
    b.add_edge(START, "init")
    b.add_conditional_edges("init", route_after_init)
    b.add_edge("run_eco_route", END)
    b.add_edge("error_handler", END)
    return b.compile(checkpointer=MemorySaver())


class TestInitValidation:
    def test_missing_design_dir_triggers_error(self):
        g = _make_init_graph(require_design_dir=True, require_netlist=True)
        cfg = {"configurable": {"thread_id": "t1"}}
        list(g.stream(
            {"design_name": "d", "design_dir": "/no/such/dir", "phase_status": {}, "step_status": {}},
            cfg,
        ))
        s = g.get_state(cfg)
        assert s.values["phase_status"]["init"] == "error"
        assert "design_dir" in s.values["error_msg"]

    def test_missing_netlist_triggers_error(self):
        with tempfile.TemporaryDirectory() as td:
            g = _make_init_graph(require_design_dir=True, require_netlist=True)
            cfg = {"configurable": {"thread_id": "t2"}}
            list(g.stream(
                {"design_name": "d", "design_dir": td, "phase_status": {}, "step_status": {}},
                cfg,
            ))
            s = g.get_state(cfg)
            assert s.values["phase_status"]["init"] == "error"
            assert "网表" in s.values["error_msg"]

    def test_missing_sdc_triggers_error(self):
        with tempfile.TemporaryDirectory() as td:
            open(os.path.join(td, "top.v"), "w").close()
            g = _make_init_graph(require_design_dir=True, require_netlist=True, require_sdc=True)
            cfg = {"configurable": {"thread_id": "t3"}}
            list(g.stream(
                {"design_name": "d", "design_dir": td, "phase_status": {}, "step_status": {}},
                cfg,
            ))
            s = g.get_state(cfg)
            assert s.values["phase_status"]["init"] == "error"
            assert "SDC" in s.values["error_msg"]

    def test_missing_env_var_triggers_error(self):
        os.environ.pop("MY_ECO_LICENSE", None)
        g = _make_init_graph(required_env_vars=["MY_ECO_LICENSE"])
        cfg = {"configurable": {"thread_id": "t4"}}
        list(g.stream(
            {"design_name": "d", "phase_status": {}, "step_status": {}},
            cfg,
        ))
        s = g.get_state(cfg)
        assert s.values["phase_status"]["init"] == "error"
        assert "MY_ECO_LICENSE" in s.values["error_msg"]

    def test_valid_design_dir_passes(self):
        with tempfile.TemporaryDirectory() as td:
            open(os.path.join(td, "top.v"), "w").close()
            open(os.path.join(td, "cst.sdc"), "w").close()
            g = _make_init_graph(
                require_design_dir=True,
                require_netlist=True,
                require_sdc=True,
                required_env_vars=["PATH"],
            )
            cfg = {"configurable": {"thread_id": "t5"}}
            list(g.stream(
                {"design_name": "d", "design_dir": td, "phase_status": {}, "step_status": {}},
                cfg,
            ))
            s = g.get_state(cfg)
            assert s.values["phase_status"]["init"] == "done"
            assert "run_dir" in s.values
            assert s.values.get("design_dir") == td

    def test_default_relaxed_mode_passes_without_files(self):
        g = _make_init_graph()
        cfg = {"configurable": {"thread_id": "t6"}}
        list(g.stream(
            {"design_name": "d", "phase_status": {}, "step_status": {}},
            cfg,
        ))
        s = g.get_state(cfg)
        assert s.values["phase_status"]["init"] == "done"
        assert s.values.get("run_dir", "").startswith("runs/")

    def test_multiple_errors_aggregated(self):
        os.environ.pop("LICENSE_A", None)
        os.environ.pop("LICENSE_B", None)
        g = _make_init_graph(
            require_design_dir=True,
            required_env_vars=["LICENSE_A", "LICENSE_B"],
        )
        cfg = {"configurable": {"thread_id": "t7"}}
        list(g.stream(
            {"design_name": "d", "phase_status": {}, "step_status": {}},
            cfg,
        ))
        s = g.get_state(cfg)
        err = s.values["error_msg"]
        assert "design_dir 未提供" in err
        assert "LICENSE_A" in err
        assert "LICENSE_B" in err
        assert s.values["phase_status"]["init"] == "error"


class TestInitEdgeCases:
    def test_missing_design_name_triggers_interrupt(self):
        from langgraph.types import Command

        g = _make_init_graph()
        cfg = {"configurable": {"thread_id": "t8"}}
        list(g.stream(
            {"messages": [], "phase_status": {}, "step_status": {}},
            cfg,
        ))
        s = g.get_state(cfg)
        assert s.next, "设计名缺失应触发 interrupt"
        assert s.interrupts

        list(g.stream(Command(resume="MyDesign"), cfg))
        s2 = g.get_state(cfg).values
        assert s2["phase_status"]["init"] == "done"
        assert s2["design_name"] == "MyDesign"

    def test_missing_required_tool_triggers_error(self):
        g = _make_init_graph(required_tools=["totally_nonexistent_eda_tool_xyz_12345"])
        cfg = {"configurable": {"thread_id": "t9"}}
        list(g.stream(
            {"design_name": "d", "phase_status": {}, "step_status": {}},
            cfg,
        ))
        s = g.get_state(cfg)
        assert s.values["phase_status"]["init"] == "error"
        assert "EDA 工具" in s.values["error_msg"]

    def test_ensure_dir_failure_triggers_error(self, tmp_path, monkeypatch):
        def _failing_ensure_dir(path):
            return False

        import src.nodes.node_init as ni
        monkeypatch.setattr(ni, "ensure_dir", _failing_ensure_dir)

        g = _make_init_graph()
        cfg = {"configurable": {"thread_id": "t10"}}
        list(g.stream(
            {"design_name": "d", "phase_status": {}, "step_status": {}},
            cfg,
        ))
        s = g.get_state(cfg)
        assert s.values["phase_status"]["init"] == "error"
        assert "运行目录" in s.values["error_msg"]


# ============================================================
# P2-3: file_utils 边界 case
# ============================================================


class TestFileUtilsEdgeCases:
    def test_check_write_permission_when_parent_exists(self, tmp_path):
        from src.utils.file_utils import check_write_permission

        target = tmp_path / "file.txt"
        result = check_write_permission(str(target))
        assert result is True

    def test_check_read_permission_on_existing_file(self, tmp_path):
        from src.utils.file_utils import check_read_permission

        f = tmp_path / "r.dat"
        f.write_text("x")
        assert check_read_permission(str(f)) is True

    def test_check_read_permission_on_nonexistent(self):
        from src.utils.file_utils import check_read_permission

        assert check_read_permission("/no/such/file_xyz_12345") is False

    def test_build_report_path(self):
        from src.utils.file_utils import build_report_path

        assert build_report_path("/run", "sta") == "/run/sta.rpt"

    def test_find_design_files_empty_dir(self, tmp_path):
        from src.utils.file_utils import find_design_files

        result = find_design_files(str(tmp_path))
        assert result == {"netlist": [], "sdc": []}

    def test_find_design_files_nonexistent_path(self):
        from src.utils.file_utils import find_design_files

        result = find_design_files("/no/such/dir_xyz_12345")
        assert result == {"netlist": [], "sdc": []}

    def test_find_design_files_sv_extension(self, tmp_path):
        from src.utils.file_utils import find_design_files

        (tmp_path / "top.v").write_text("")
        (tmp_path / "child.sv").write_text("")
        (tmp_path / "skip.log").write_text("")
        result = find_design_files(str(tmp_path))
        names = sorted(os.path.basename(p) for p in result["netlist"])
        assert names == ["child.sv", "top.v"]

    def test_validate_design_dir_empty_string(self):
        from src.utils.file_utils import validate_design_dir

        result = validate_design_dir("")
        assert result["ok"] is False
        assert "未指定" in result["errors"][0]

    def test_validate_design_dir_nonexistent(self):
        from src.utils.file_utils import validate_design_dir

        result = validate_design_dir("/no/such/dir_abc_999")
        assert result["ok"] is False
        assert "不存在" in result["errors"][0]

    def test_find_design_files_skips_hidden_dirs(self, tmp_path):
        from src.utils.file_utils import find_design_files

        hidden = tmp_path / ".git"
        hidden.mkdir()
        (hidden / "secret.v").write_text("")
        (tmp_path / "visible.v").write_text("")

        result = find_design_files(str(tmp_path))
        names = os.path.basename(result["netlist"][0]) if result["netlist"] else ""
        assert names == "visible.v"
