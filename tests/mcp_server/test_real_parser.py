from __future__ import annotations

import pytest

from src.mcp_server.real import RealECOMCPServer


@pytest.fixture
def real() -> RealECOMCPServer:
    real = RealECOMCPServer.__new__(RealECOMCPServer)
    real._common_env = {"PDK_DIR": "/test/pdk", "EDA_LICENSE": "/test/licenses"}
    return real


class TestResolveEnvDict:
    def test_design_name_placeholder(self, real):
        result = real._resolve_env_dict(
            {"NETLIST": "{design_dir}/{design_name}.v"},
            design_name="MyDesign", run_dir="/tmp/runs",
        )
        assert result["NETLIST"] == "{design_dir}/MyDesign.v"

    def test_run_dir_placeholder(self, real):
        result = real._resolve_env_dict(
            {"LOG": "{run_dir}/run.log"},
            design_name="X", run_dir="/tmp/runs",
        )
        assert result["LOG"] == "/tmp/runs/run.log"

    def test_fix_strategy_placeholder(self, real):
        result = real._resolve_env_dict(
            {"FIX": "{fix_strategy}"},
            design_name="X", run_dir="/tmp", fix_strategy="setup_via_repair",
        )
        assert result["FIX"] == "setup_via_repair"

    def test_pdk_dir_placeholder(self, real):
        result = real._resolve_env_dict(
            {"RULES": "{pdk_dir}/calibre/lvs.rules"},
            design_name="X", run_dir="/tmp",
        )
        assert result["RULES"] == "/test/pdk/calibre/lvs.rules"

    def test_multiple_placeholders(self, real):
        result = real._resolve_env_dict(
            {"OUT": "{run_dir}/{design_name}_out.spef"},
            design_name="Top", run_dir="/runs/1",
        )
        assert result["OUT"] == "/runs/1/Top_out.spef"

    def test_no_placeholders(self, real):
        result = real._resolve_env_dict(
            {"CONST": "/fixed/path"},
            design_name="X", run_dir="/tmp",
        )
        assert result["CONST"] == "/fixed/path"

    def test_none_values_are_strings(self, real):
        result = real._resolve_env_dict(
            {"DESIGN": "{design_name}"},
            design_name=None, run_dir=None,
        )
        assert result["DESIGN"] == ""


class TestCoerce:
    def test_int(self):
        assert RealECOMCPServer._coerce("42") == 42

    def test_float(self):
        assert RealECOMCPServer._coerce("3.14") == 3.14

    def test_bool_true_passed(self):
        assert RealECOMCPServer._coerce("PASSED") is True

    def test_bool_true_pass(self):
        assert RealECOMCPServer._coerce("pass") is True

    def test_bool_false_failed(self):
        assert RealECOMCPServer._coerce("FAILED") is False

    def test_bool_false_no(self):
        assert RealECOMCPServer._coerce("no") is False

    def test_plain_string(self):
        assert RealECOMCPServer._coerce("hello") == "hello"


class TestParseLog:
    def test_integer_pattern(self, real, tmp_path):
        log = tmp_path / "test.log"
        log.write_text("Setup Violations: 42\nHold Violations: 15\n")
        result = real._parse_log(str(log), {
            "setup_vio": r"Setup Violations:\s*(\d+)",
            "hold_vio":  r"Hold Violations:\s*(\d+)",
        })
        assert result == {"setup_vio": 42, "hold_vio": 15}

    def test_boolean_pattern_no_group(self, real, tmp_path):
        log = tmp_path / "test.log"
        log.write_text("LVS check PASSED\n")
        result = real._parse_log(str(log), {"pv_pass": "LVS check.*PASSED"})
        assert result == {"pv_pass": True}

    def test_missing_pattern(self, real, tmp_path):
        log = tmp_path / "test.log"
        log.write_text("nothing here\n")
        result = real._parse_log(str(log), {"missing": r"Does Not Exist"})
        assert result == {}

    def test_empty_patterns(self, real, tmp_path):
        log = tmp_path / "test.log"
        log.write_text("data\n")
        result = real._parse_log(str(log), {})
        assert result == {}

    def test_case_insensitive(self, real, tmp_path):
        log = tmp_path / "test.log"
        log.write_text("setup violations: 99\n")
        result = real._parse_log(str(log), {
            "setup_vio": r"Setup Violations:\s*(\d+)",
        })
        assert result == {"setup_vio": 99}
