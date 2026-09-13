from __future__ import annotations

import asyncio
import json

import pytest

from src.mcp_server.mock import MockECOMCPServer
from src.mcp_server.mcp_app import create_mcp_app


def _call(mcp, tool_name: str, args: dict) -> dict:
    results = asyncio.run(mcp.call_tool(tool_name, args))
    return json.loads(results[0].text)


class TestToolDelegationMock:
    def test_run_sta_happy_path(self):
        mock = MockECOMCPServer(scenario="happy_path")
        mcp = create_mcp_app(eco_server=mock)
        result = _call(mcp, "run_sta", {
            "design_name": "MyDesign",
            "run_dir": "/tmp/runs",
        })
        assert result == {"setup_vio": 125, "hold_vio": 47}

    def test_run_eco_route(self):
        mock = MockECOMCPServer(scenario="happy_path")
        mcp = create_mcp_app(eco_server=mock)
        result = _call(mcp, "run_eco_route", {
            "design_name": "MyDesign",
            "run_dir": "/tmp/runs",
        })
        assert result == {"route_done": True}

    def test_run_pv(self):
        mock = MockECOMCPServer(scenario="happy_path")
        mcp = create_mcp_app(eco_server=mock)
        result = _call(mcp, "run_pv", {
            "design_name": "MyDesign",
            "run_dir": "/tmp/runs",
        })
        assert result == {"pv_pass": True}

    def test_run_fix_setup(self):
        mock = MockECOMCPServer(scenario="happy_path")
        mcp = create_mcp_app(eco_server=mock)
        result = _call(mcp, "run_fix_setup", {
            "design_name": "MyDesign",
            "run_dir": "/tmp/runs",
            "fix_strategy": "setup_via_repair",
        })
        assert result == {"fix_done": True, "setup_vio": 30}

    def test_error_scenario_propagates(self):
        mock = MockECOMCPServer(scenario="phase2_sta_error")
        mcp = create_mcp_app(eco_server=mock)
        with pytest.raises(Exception, match="STA 执行超时"):
            _call(mcp, "run_sta", {
                "design_name": "MyDesign",
                "run_dir": "/tmp/runs",
            })


@pytest.mark.skipif(
    __import__("shutil").which("csh") is None, reason="csh not installed"
)
class TestToolDelegationReal:
    def test_real_run_sta_via_csh(self, tmp_path):
        from src.mcp_server.real import RealECOMCPServer
        real = RealECOMCPServer(config_path="tests/fixtures/test_eda_tools.yaml")
        mcp = create_mcp_app(eco_server=real)
        result = _call(mcp, "run_sta", {
            "design_name": "MyDesign",
            "run_dir": str(tmp_path),
        })
        assert result == {"setup_vio": 42, "hold_vio": 15}

    def test_real_run_pv_via_csh(self, tmp_path):
        from src.mcp_server.real import RealECOMCPServer
        real = RealECOMCPServer(config_path="tests/fixtures/test_eda_tools.yaml")
        mcp = create_mcp_app(eco_server=real)
        result = _call(mcp, "run_pv", {
            "design_name": "MyDesign",
            "run_dir": str(tmp_path),
        })
        assert result == {"pv_pass": True}
