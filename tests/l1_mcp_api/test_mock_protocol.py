from __future__ import annotations

import pytest

from src.mcp_server.mock import MockECOMCPServer, MOCK_SCENARIOS


STEP_METHODS = [
    "run_eco_route",
    "run_ext",
    "run_sta",
    "run_pv",
    "run_signoff",
    "run_pt_fix_setup",
    "run_pt_fix_hold",
]


@pytest.fixture
def happy_server():
    return MockECOMCPServer(scenario="happy_path", simulate_delay=0)


@pytest.fixture
def convergence_server():
    return MockECOMCPServer(scenario="convergence", simulate_delay=0)


class TestProtocolCompliance:
    def test_all_7_methods_exist(self, happy_server):
        for method in STEP_METHODS:
            assert hasattr(happy_server, method), f"缺少方法 {method}"
            assert callable(getattr(happy_server, method))

    @pytest.mark.parametrize("method", STEP_METHODS[:5])
    def test_method_signature_basic(self, happy_server, method):
        import inspect
        sig = inspect.signature(getattr(happy_server, method))
        params = list(sig.parameters.keys())
        assert "design_name" in params
        assert "run_dir" in params

    @pytest.mark.parametrize("method", STEP_METHODS[5:])
    def test_method_signature_with_fix_strategy(self, happy_server, method):
        import inspect
        sig = inspect.signature(getattr(happy_server, method))
        params = list(sig.parameters.keys())
        assert "design_name" in params
        assert "run_dir" in params
        assert "fix_strategy" in params

    def test_all_scenarios_defined(self):
        expected = {
            "happy_path",
            "convergence",
            "phase1_ext_error",
            "phase2_sta_error",
            "phase2_pv_error",
            "phase2_signoff_error",
            "phase3_fix_error",
        }
        assert set(MOCK_SCENARIOS.keys()) == expected


class TestReturnSchema:
    def test_run_eco_route_return(self, happy_server):
        res = happy_server.run_eco_route("designA", "/tmp/run1")
        assert isinstance(res, dict)
        assert res.get("route_done") is True

    def test_run_ext_return(self, happy_server):
        res = happy_server.run_ext("designA", "/tmp/run1")
        assert isinstance(res, dict)
        assert res.get("ext_done") is True

    def test_run_sta_return(self, happy_server):
        res = happy_server.run_sta("designA", "/tmp/run1")
        assert isinstance(res, dict)
        assert isinstance(res.get("setup_vio"), int)
        assert isinstance(res.get("hold_vio"), int)
        assert res["setup_vio"] == 125
        assert res["hold_vio"] == 47

    def test_run_pv_return(self, happy_server):
        res = happy_server.run_pv("designA", "/tmp/run1")
        assert isinstance(res, dict)
        assert isinstance(res.get("pv_pass"), bool)

    def test_run_signoff_return(self, happy_server):
        res = happy_server.run_signoff("designA", "/tmp/run1")
        assert isinstance(res, dict)
        assert isinstance(res.get("signoff_pass"), bool)
        assert isinstance(res.get("violations"), list)

    def test_run_pt_fix_setup_return(self, happy_server):
        res = happy_server.run_pt_fix_setup("designA", "/tmp/run1", "setup")
        assert isinstance(res, dict)
        assert res.get("fix_done") is True
        assert isinstance(res.get("setup_vio"), int)
        assert res["setup_vio"] == 30

    def test_run_pt_fix_hold_return(self, happy_server):
        res = happy_server.run_pt_fix_hold("designA", "/tmp/run1", "hold")
        assert isinstance(res, dict)
        assert res.get("fix_done") is True
        assert isinstance(res.get("hold_vio"), int)
        assert res["hold_vio"] == 10

    def test_convergence_scenario_sta(self, convergence_server):
        res = convergence_server.run_sta("designA", "/tmp/run2")
        assert res["setup_vio"] == 80
        assert res["hold_vio"] == 25

    def test_convergence_scenario_fix_setup(self, convergence_server):
        res = convergence_server.run_pt_fix_setup("designA", "/tmp/run2", "setup")
        assert res["setup_vio"] == 15


class TestExceptionPassthrough:
    def test_phase1_ext_error(self):
        server = MockECOMCPServer(scenario="phase1_ext_error", simulate_delay=0)
        with pytest.raises(Exception, match="StarRCX license"):
            server.run_ext("designA", "/tmp/run3")

    def test_phase2_sta_error(self):
        server = MockECOMCPServer(scenario="phase2_sta_error", simulate_delay=0)
        with pytest.raises(Exception, match="STA 执行超时"):
            server.run_sta("designA", "/tmp/run3")

    def test_phase2_pv_error(self):
        server = MockECOMCPServer(scenario="phase2_pv_error", simulate_delay=0)
        with pytest.raises(Exception, match="PV LVS"):
            server.run_pv("designA", "/tmp/run3")

    def test_phase2_signoff_error(self):
        server = MockECOMCPServer(scenario="phase2_signoff_error", simulate_delay=0)
        with pytest.raises(Exception, match="Signoff DRC"):
            server.run_signoff("designA", "/tmp/run3")

    def test_phase3_fix_error(self):
        server = MockECOMCPServer(scenario="phase3_fix_error", simulate_delay=0)
        with pytest.raises(Exception, match="ECO Fix 工具异常"):
            server.run_pt_fix_setup("designA", "/tmp/run3", "setup")

    def test_non_fail_steps_in_error_scenario_still_work(self):
        server = MockECOMCPServer(scenario="phase2_sta_error", simulate_delay=0)
        res_pv = server.run_pv("designA", "/tmp/run3")
        assert res_pv["pv_pass"] is True

        res_signoff = server.run_signoff("designA", "/tmp/run3")
        assert res_signoff["signoff_pass"] is True


class TestIdempotency:
    def test_same_params_twice_same_result(self, happy_server):
        r1 = happy_server.run_sta("designA", "/tmp/idem")
        r2 = happy_server.run_sta("designA", "/tmp/idem")
        assert r1 == r2

    def test_all_api_idempotent(self, happy_server):
        for method_name in STEP_METHODS:
            kwargs = {"design_name": "d", "run_dir": "/tmp/idem"}
            if "fix" in method_name:
                kwargs["fix_strategy"] = "setup"
            m = getattr(happy_server, method_name)
            assert m(**kwargs) == m(**kwargs), f"{method_name} 非幂等"

    def test_default_scenario_fallback(self):
        server = MockECOMCPServer(scenario="nonexistent_scenario", simulate_delay=0)
        res = server.run_sta("d", "/tmp")
        assert res == MOCK_SCENARIOS["happy_path"]["run_sta"]
