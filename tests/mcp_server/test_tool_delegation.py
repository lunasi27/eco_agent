from __future__ import annotations

import asyncio
import json

import pytest

from src.mcp_server.mock import MockECOMCPServer
from src.mcp_server.mcp_app import create_mcp_app

from .helpers import resolve_csh_bin


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

    def test_run_pt_fix_setup(self):
        mock = MockECOMCPServer(scenario="happy_path")
        mcp = create_mcp_app(eco_server=mock)
        result = _call(mcp, "run_pt_fix_setup", {
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
    resolve_csh_bin() is None,
    reason="no csh available (set ECO_CSH_BIN to override, default /bin/csh)",
)
class TestToolDelegationReal:
    def test_real_run_sta_via_mcp_tool(self, make_server):
        # 端到端：MCP Tool → Protocol 方法 → run_step → wrapper → parse
        mcp_server = make_server(
            {
                "run_sta": [
                    'echo "Setup Violations: 42"',
                    'echo "Hold Violations: 15"',
                ],
            },
            timeout_s={"run_sta": 10},
        )
        mcp = create_mcp_app(eco_server=mcp_server)
        result = _call(mcp, "run_sta", {
            "design_name": "MyDesign",
            "run_dir": "/tmp/runs",
        })
        assert result == {"setup_vio": 42, "hold_vio": 15}
