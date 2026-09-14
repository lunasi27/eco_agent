from __future__ import annotations

import pytest

from src.mcp_server.real import RealECOMCPServer


@pytest.fixture
def real() -> RealECOMCPServer:
    return RealECOMCPServer.__new__(RealECOMCPServer)


class TestResolveOne:
    def test_simple_token(self):
        assert RealECOMCPServer._resolve_one(
            "{project_name}", {"project_name": "SOC_XXX"},
        ) == "SOC_XXX"

    def test_multiple_tokens(self):
        assert RealECOMCPServer._resolve_one(
            "{work_dir}/{project_name}",
            {"work_dir": "/data/user", "project_name": "SOC_XXX"},
        ) == "/data/user/SOC_XXX"

    def test_dotted_token(self):
        assert RealECOMCPServer._resolve_one(
            "{base.run_eco_route}/log",
            {"base": {"run_eco_route": "/data/APR"}},
        ) == "/data/APR/log"

    def test_multiple_dotted(self):
        assert RealECOMCPServer._resolve_one(
            "{base.run_pt_fix}/scripts/{design_name}",
            {
                "base": {"run_pt_fix": "/data/PTECO"},
                "design_name": "Top",
            },
        ) == "/data/PTECO/scripts/Top"

    def test_no_placeholders(self):
        assert RealECOMCPServer._resolve_one(
            "/fixed/path/to/tool",
            {"project_name": "X"},
        ) == "/fixed/path/to/tool"

    def test_unknown_token_becomes_empty(self):
        assert RealECOMCPServer._resolve_one(
            "{unknown}/stuff",
            {"project_name": "X"},
        ) == "/stuff"

    def test_nested_dict_value_not_expanded(self):
        assert RealECOMCPServer._resolve_one(
            "{base}",
            {"base": {"run_eco_route": "/data/APR"}},
        ) == "{base}"


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


class TestParseOutput:
    def test_integer_pattern(self, real):
        text = "Setup Violations: 42\nHold Violations: 15\n"
        result = real._parse_output(text, {
            "setup_vio": r"Setup Violations:\s*(\d+)",
            "hold_vio":  r"Hold Violations:\s*(\d+)",
        })
        assert result == {"setup_vio": 42, "hold_vio": 15}

    def test_boolean_pattern_no_group(self, real):
        result = real._parse_output(
            "LVS check PASSED\n",
            {"pv_pass": "LVS check.*PASSED"},
        )
        assert result == {"pv_pass": True}

    def test_missing_pattern(self, real):
        result = real._parse_output(
            "nothing here\n",
            {"missing": r"Does Not Exist"},
        )
        assert result == {}

    def test_empty_patterns(self, real):
        result = real._parse_output("data\n", {})
        assert result == {}

    def test_case_insensitive(self, real):
        result = real._parse_output(
            "setup violations: 99\n",
            {"setup_vio": r"Setup Violations:\s*(\d+)"},
        )
        assert result == {"setup_vio": 99}
