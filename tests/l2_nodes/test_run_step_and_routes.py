from __future__ import annotations

import pytest

from langgraph.types import Send

from src.mcp_server.mock import MockECOMCPServer
from src.nodes.step_runner import run_step
from src.routers.phase_routes import (
    route_after_init,
    route_after_phase2_summary,
    route_after_phase3_summary,
    route_after_run_eco_route,
    route_after_run_ext,
)
from src.utils.constants import normalize_fix_strategy, normalize_iter_choice


BASE_STATE = {
    "design_name": "designA",
    "run_dir": "/tmp/run1",
    "iteration_cnt": 1,
    "phase_status": {"phase1": "pending", "phase2": "pending", "phase3": "pending"},
    "step_status": {},
}


# ============================================================
# _run_step 单元测试
# ============================================================


class TestRunStep:
    def test_normal_path_with_result_keys(self):
        server = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
        result = run_step(
            state=BASE_STATE,
            step_name="run_sta",
            phase_name="phase2",
            mcp_server=server,
            result_keys=["setup_vio", "hold_vio"],
        )
        assert result["current_step"] == "run_sta"
        assert result["current_phase"] == "phase2"
        assert result["step_status"]["run_sta"] == "done"
        assert result["phase_status"]["phase2"] == "done"
        assert result["setup_vio"] == 125
        assert result["hold_vio"] == 47
        assert "error_msg" not in result

    def test_exception_path(self):
        server = MockECOMCPServer(scenario="phase2_sta_error", simulate_delay=0)
        result = run_step(
            state=BASE_STATE,
            step_name="run_sta",
            phase_name="phase2",
            mcp_server=server,
            result_keys=["setup_vio", "hold_vio"],
        )
        assert result["step_status"]["run_sta"] == "error"
        assert result["phase_status"]["phase2"] == "error"
        assert "error_msg" in result
        assert "run_sta" in result["error_msg"]

    def test_result_keys_not_in_response_not_written(self):
        server = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
        state_with_existing_vio = {**BASE_STATE, "setup_vio": 999}
        result = run_step(
            state=state_with_existing_vio,
            step_name="run_eco_route",
            phase_name="phase1",
            mcp_server=server,
            result_keys=["setup_vio", "nonexistent_key"],
        )
        assert result["step_status"]["run_eco_route"] == "done"
        assert "nonexistent_key" not in result
        assert result.get("setup_vio") is None or result.get("setup_vio") != 999

    def test_extra_params_passed_to_fix_step(self):
        server = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
        result = run_step(
            state=BASE_STATE,
            step_name="run_pt_fix_setup",
            phase_name="phase3",
            mcp_server=server,
            result_keys=["setup_vio"],
            extra_params={"fix_strategy": "setup"},
        )
        assert result["step_status"]["run_pt_fix_setup"] == "done"
        assert result["setup_vio"] == 30

    def test_phase_status_preserves_existing_phases(self):
        server = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
        state_with_prev = {
            **BASE_STATE,
            "phase_status": {"phase1": "done", "phase2": "pending", "phase3": "pending"},
            "step_status": {"run_eco_route": "done"},
        }
        result = run_step(
            state=state_with_prev,
            step_name="run_ext",
            phase_name="phase1",
            mcp_server=server,
        )
        assert result["phase_status"]["phase1"] == "done"
        assert result["step_status"]["run_eco_route"] == "done"
        assert result["step_status"]["run_ext"] == "done"

    def test_no_result_keys(self):
        server = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
        result = run_step(
            state=BASE_STATE,
            step_name="run_eco_route",
            phase_name="phase1",
            mcp_server=server,
        )
        assert result["step_status"]["run_eco_route"] == "done"
        assert "setup_vio" not in result


# ============================================================
# 路由函数单元测试
# ============================================================


class TestRouteAfterInit:
    def test_init_error_goes_to_error_handler(self):
        state = {**BASE_STATE, "step_status": {"init": "error"}}
        assert route_after_init(state) == "error_handler"

    def test_init_ok_goes_to_run_eco_route(self):
        state = {**BASE_STATE, "step_status": {"init": "done"}}
        assert route_after_init(state) == "run_eco_route"


class TestRouteAfterRunEcoRoute:
    def test_error(self):
        state = {**BASE_STATE, "step_status": {"run_eco_route": "error"}}
        assert route_after_run_eco_route(state) == "error_handler"

    def test_done(self):
        state = {**BASE_STATE, "step_status": {"run_eco_route": "done"}}
        assert route_after_run_eco_route(state) == "run_ext"


class TestRouteAfterRunExt:
    def test_error_goes_to_error_handler(self):
        state = {**BASE_STATE, "step_status": {"run_ext": "error"}}
        assert route_after_run_ext(state) == "error_handler"

    def test_done_returns_three_sends(self):
        state = {**BASE_STATE, "step_status": {"run_ext": "done"}}
        result = route_after_run_ext(state)
        assert isinstance(result, list)
        assert len(result) == 3
        targets = [s.node for s in result]
        assert targets == ["run_sta", "run_pv", "run_signoff"]


class TestRouteAfterPhase2Summary:
    def test_run_sta_error_goes_to_error_handler(self):
        state = {**BASE_STATE, "step_status": {"run_sta": "error"}}
        assert route_after_phase2_summary(state) == "error_handler"

    def test_setup_strategy(self):
        state = {
            **BASE_STATE,
            "step_status": {"run_sta": "done", "run_pv": "done"},
            "user_fix_strategy": "setup",
        }
        assert route_after_phase2_summary(state) == "run_pt_fix_setup"

    def test_hold_strategy(self):
        state = {
            **BASE_STATE,
            "step_status": {"run_sta": "done"},
            "user_fix_strategy": "hold",
        }
        assert route_after_phase2_summary(state) == "run_pt_fix_hold"

    def test_invalid_strategy_loops_back_to_summary(self):
        state = {
            **BASE_STATE,
            "step_status": {"run_sta": "done"},
            "user_fix_strategy": "unknown_strategy",
        }
        assert route_after_phase2_summary(state) == "phase2_summary"


class TestRouteAfterPhase3Summary:
    def test_continue_goes_to_init(self):
        state = {**BASE_STATE, "user_iter_choice": "continue"}
        assert route_after_phase3_summary(state) == "init"

    def test_stop_goes_to_finalize(self):
        state = {**BASE_STATE, "user_iter_choice": "stop"}
        assert route_after_phase3_summary(state) == "finalize"

    def test_default_empty_loops_back_to_summary(self):
        """空选择/乱码不能被默认当成 stop，应回到 phase3_summary 重问"""
        state = {**BASE_STATE, "user_iter_choice": ""}
        assert route_after_phase3_summary(state) == "phase3_summary"

    def test_garbage_loops_back_to_summary(self):
        state = {**BASE_STATE, "user_iter_choice": "asdf"}
        assert route_after_phase3_summary(state) == "phase3_summary"


# ============================================================
# 断点输入校验：只接受提示中给出的规范关键词（大小写/空白不敏感）
# ============================================================


class TestNormalizeFixStrategy:
    @pytest.mark.parametrize("raw,expected", [
        ("setup", "setup"), ("SETUP", "setup"), ("  setup ", "setup"),
        ("hold", "hold"), ("HOLD", "hold"), (" hold ", "hold"),
        ("leakage", "leakage"), ("LEAKAGE", "leakage"),
    ])
    def test_valid_keywords(self, raw, expected):
        assert normalize_fix_strategy(raw) == expected

    @pytest.mark.parametrize("raw", [
        "asdf", "", "  ", None,
        # 不提供任何别名：缩写、数字、中文一律拒绝
        "s", "1", "设置", "保持", "漏电",
    ])
    def test_invalid_returns_empty(self, raw):
        assert normalize_fix_strategy(raw) == ""


class TestNormalizeIterChoice:
    @pytest.mark.parametrize("raw,expected", [
        ("continue", "continue"), ("CONTINUE", "continue"), (" continue ", "continue"),
        ("stop", "stop"), ("STOP", "stop"),
    ])
    def test_valid_keywords(self, raw, expected):
        assert normalize_iter_choice(raw) == expected

    @pytest.mark.parametrize("raw", [
        "asdf", "", "  ", None, "setup",
        # 不提供任何别名：缩写、数字、中文一律拒绝
        "c", "s", "y", "1", "继续", "停止",
    ])
    def test_invalid_returns_empty(self, raw):
        assert normalize_iter_choice(raw) == ""
