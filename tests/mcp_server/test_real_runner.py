"""RealECOMCPServer ↔ run_step_wrapper.csh 端到端集成测试。

只在装有 /bin/csh 的机器上运行（EDA 环境），本机无 csh 时自动 skip。
测试配置全部在 tmp_path 下动态生成（见 conftest.make_server）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from .helpers import FIXTURE_SCRIPTS, resolve_csh_bin

pytestmark = pytest.mark.skipif(
    resolve_csh_bin() is None,
    reason="no csh available (set ECO_CSH_BIN to override, default /bin/csh)",
)

# fixture 脚本通过可用的 csh 显式调用（不依赖 shebang 里的 /bin/csh）
CSH = resolve_csh_bin()


def _newest_log(tmp_path: Path) -> str:
    logs = sorted(tmp_path.glob("*.log"), key=lambda p: p.stat().st_mtime)
    assert logs, f"期望在 {tmp_path} 下产生日志文件，但没有"
    return logs[-1].read_text()


def test_env_injected_and_log_written(make_server, tmp_path):
    server = make_server(
        {"run_ext": f"{CSH} -f {FIXTURE_SCRIPTS / '_test_env.csh'}"},
        timeout_s={"run_ext": 10},
        logs={"run_ext": str(tmp_path)},
    )
    result = server.run_step("run_ext")
    assert result == {}  # run_ext 没有内置 parse patterns

    log_text = _newest_log(tmp_path)
    assert "DESIGN_NAME=TestDesign" in log_text
    assert "STEP_NAME=run_ext" in log_text
    assert "command 1/1" in log_text


def test_subprocess_timeout_raises(make_server):
    server = make_server(
        {"run_ext": f"{CSH} -f {FIXTURE_SCRIPTS / '_test_sleep.csh'}"},
        timeout_s={"run_ext": 1},
    )
    with pytest.raises(Exception, match="执行超时"):
        server.run_step("run_ext")


def test_nonzero_exit_raises(make_server, tmp_path):
    server = make_server(
        {"run_ext": f"{CSH} -f {FIXTURE_SCRIPTS / '_test_fail.csh'}"},
        timeout_s={"run_ext": 5},
        logs={"run_ext": str(tmp_path)},
    )
    with pytest.raises(Exception, match="退出码 1"):
        server.run_step("run_ext")

    log_text = _newest_log(tmp_path)
    assert "command 1/1 FAILED" in log_text


def test_multi_command_fail_fast(make_server, tmp_path):
    server = make_server(
        {
            "run_ext": [
                "echo FIRST_MARKER_SHOULD_RUN",
                f"{CSH} -f {FIXTURE_SCRIPTS / '_test_fail.csh'}",
                "echo THIRD_MARKER_NEVER_RUN",
            ],
        },
        timeout_s={"run_ext": 5},
        logs={"run_ext": str(tmp_path)},
    )
    with pytest.raises(Exception, match="command 2/3"):
        server.run_step("run_ext")

    log_text = _newest_log(tmp_path)
    assert "FIRST_MARKER_SHOULD_RUN" in log_text
    assert "command 2/3 FAILED" in log_text
    assert "THIRD_MARKER_NEVER_RUN" not in log_text


def test_multi_command_all_success(make_server):
    server = make_server(
        {
            "run_ext": [
                "echo LINE_ONE",
                "echo LINE_TWO",
            ],
        },
        timeout_s={"run_ext": 5},
    )
    assert server.run_step("run_ext") == {}


def test_design_name_placeholder_in_step_command(make_server, tmp_path):
    # 回归：{design_name} 在 config.yaml.step_command 里必须正确展开（旧实现会解析成空）
    server = make_server(
        {"run_pv": "echo DESIGN_VALUE={design_name}"},
        timeout_s={"run_pv": 5},
        logs={"run_pv": str(tmp_path)},
    )
    server.run_step("run_pv")

    log_text = _newest_log(tmp_path)
    assert "DESIGN_VALUE=TestDesign" in log_text


def test_run_sta_multi_command_parses_violations(make_server, tmp_path):
    server = make_server(
        {
            "run_sta": [
                'echo "Setup Violations: 42"',
                'echo "Hold Violations: 15"',
            ],
        },
        timeout_s={"run_sta": 5},
    )
    assert server.run_sta("MyDesign", str(tmp_path)) == {
        "setup_vio": 42,
        "hold_vio": 15,
    }


def test_run_pv_parses_pass(make_server, tmp_path):
    server = make_server(
        {"run_pv": 'echo "LVS check PASSED"'},
        timeout_s={"run_pv": 5},
    )
    assert server.run_pv("MyDesign", str(tmp_path)) == {"pv_pass": True}
