from __future__ import annotations

import shutil

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("csh") is None, reason="csh not installed"
)


def test_subprocess_injects_env(tmp_path):
    from src.mcp_server.real import RealECOMCPServer
    real = RealECOMCPServer(config_path="tests/fixtures/test_eda_tools.yaml")
    real._run_script("_test_echo", "TestDesign", str(tmp_path))
    log = (tmp_path / "_test_echo.log").read_text()
    assert "DESIGN_NAME=TestDesign" in log
    assert f"RUN_DIR={tmp_path}" in log
    assert "TEST_VAR=hello_world" in log


def test_subprocess_timeout_raises(tmp_path):
    from src.mcp_server.real import RealECOMCPServer
    real = RealECOMCPServer(config_path="tests/fixtures/test_eda_tools.yaml")
    with pytest.raises(Exception, match="执行超时"):
        real._run_script("_test_sleep", "X", str(tmp_path))


def test_subprocess_nonzero_exit_raises(tmp_path):
    from src.mcp_server.real import RealECOMCPServer
    real = RealECOMCPServer(config_path="tests/fixtures/test_eda_tools.yaml")
    with pytest.raises(Exception, match="退出码 1"):
        real._run_script("_test_fail", "X", str(tmp_path))


def test_run_script_creates_run_dir(tmp_path):
    import os
    from src.mcp_server.real import RealECOMCPServer
    real = RealECOMCPServer(config_path="tests/fixtures/test_eda_tools.yaml")
    run_dir = str(tmp_path / "nested" / "dir")
    real._run_script("_test_echo", "X", run_dir)
    assert os.path.isdir(run_dir)
    assert os.path.isfile(os.path.join(run_dir, "_test_echo.log"))


def test_run_sta_via_scripts_eda(tmp_path):
    from src.mcp_server.real import RealECOMCPServer
    real = RealECOMCPServer(config_path="tests/fixtures/test_eda_tools.yaml")
    res = real._run_script("run_sta", "MyDesign", str(tmp_path))
    assert res == {"setup_vio": 42, "hold_vio": 15}


def test_run_pv_via_scripts_eda(tmp_path):
    from src.mcp_server.real import RealECOMCPServer
    real = RealECOMCPServer(config_path="tests/fixtures/test_eda_tools.yaml")
    res = real._run_script("run_pv", "MyDesign", str(tmp_path))
    assert res == {"pv_pass": True}
