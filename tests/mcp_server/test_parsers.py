from __future__ import annotations

from src.mcp_server.parsers import STEP_PARSE_PATTERNS, coerce_value, parse_output


class TestCoerceValue:
    def test_int(self):
        assert coerce_value("42") == 42

    def test_float(self):
        assert coerce_value("3.14") == 3.14

    def test_bool_true_passed(self):
        assert coerce_value("PASSED") is True

    def test_bool_true_pass(self):
        assert coerce_value("pass") is True

    def test_bool_false_failed(self):
        assert coerce_value("FAILED") is False

    def test_bool_false_no(self):
        assert coerce_value("no") is False

    def test_plain_string(self):
        assert coerce_value("hello") == "hello"


class TestParseOutput:
    def test_sta_integer_patterns(self):
        text = "Setup Violations: 42\nHold Violations: 15\n"
        assert parse_output("run_sta", text) == {"setup_vio": 42, "hold_vio": 15}

    def test_pv_boolean_pattern_no_group(self):
        assert parse_output("run_pv", "LVS check PASSED\n") == {"pv_pass": True}

    def test_signoff_boolean_pattern(self):
        assert parse_output("run_signoff", "Signoff DRC ... PASSED") == {
            "signoff_pass": True
        }

    def test_fix_setup_after_fix_pattern(self):
        assert parse_output(
            "run_pt_fix_setup", "Setup Violations After Fix: 3",
        ) == {"setup_vio": 3}

    def test_missing_pattern(self):
        assert parse_output("run_sta", "nothing here\n") == {}

    def test_step_without_patterns_returns_empty(self):
        # run_eco_route 等 step 没有内置解析规则
        assert "run_eco_route" not in STEP_PARSE_PATTERNS
        assert parse_output("run_eco_route", "anything") == {}

    def test_case_insensitive(self):
        assert parse_output("run_sta", "setup violations: 99\n") == {"setup_vio": 99}
