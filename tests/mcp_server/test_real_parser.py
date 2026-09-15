from __future__ import annotations

from src.mcp_server.real import RealECOMCPServer


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

    def test_self_reference_token_preserved(self):
        # {fix_strategy} 在 init 阶段以自引用形式占位，必须原样保留到运行时
        assert RealECOMCPServer._resolve_one(
            "pds_pteco {fix_strategy}",
            {"fix_strategy": "{fix_strategy}"},
        ) == "pds_pteco {fix_strategy}"
