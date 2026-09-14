from __future__ import annotations

from src.mcp_server.mcp_app import create_mcp_app


EXPECTED_TOOLS = {
    "run_eco_route",
    "run_ext",
    "run_sta",
    "run_pv",
    "run_signoff",
    "run_pt_fix_setup",
    "run_pt_fix_hold",
    "run_pt_fix_leakage",
    "run_pt_fix_drv",
    "run_xtop_fix_hold",
}


def test_all_10_tools_registered():
    mcp = create_mcp_app()
    tools = mcp._tool_manager._tools
    assert set(tools.keys()) == EXPECTED_TOOLS


def test_tool_count():
    mcp = create_mcp_app()
    assert len(mcp._tool_manager._tools) == 10


def test_run_sta_input_schema():
    mcp = create_mcp_app()
    run_sta = mcp._tool_manager._tools["run_sta"]
    schema = run_sta.parameters
    props = schema["properties"]
    assert props["design_name"]["type"] == "string"
    assert props["run_dir"]["type"] == "string"
    assert set(schema["required"]) == {"design_name", "run_dir"}


def test_run_pt_fix_setup_input_schema():
    mcp = create_mcp_app()
    tool = mcp._tool_manager._tools["run_pt_fix_setup"]
    schema = tool.parameters
    assert "fix_strategy" in schema["properties"]
    assert set(schema["required"]) == {"design_name", "run_dir", "fix_strategy"}


def test_run_sta_has_docstring():
    mcp = create_mcp_app()
    tool = mcp._tool_manager._tools["run_sta"]
    assert "静态时序分析" in tool.description or "STA" in tool.description


def test_server_name_default():
    mcp = create_mcp_app()
    assert mcp.name == "eco-agent"


def test_server_name_custom():
    mcp = create_mcp_app(server_name="custom-mcp")
    assert mcp.name == "custom-mcp"
