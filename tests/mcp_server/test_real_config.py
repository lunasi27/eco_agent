"""RealECOMCPServer 配置加载 / resolve / 校验 / dry-run 测试（不依赖 csh）。"""

from __future__ import annotations

import pytest

from src.mcp_server.protocol import STEP_NAMES
from src.mcp_server.real import DEFAULT_TIMEOUT_S, RealECOMCPServer

from .helpers import WRAPPER_PATH, write_config


def _make(tmp_path, step_command: dict, **kwargs) -> RealECOMCPServer:
    config_path = write_config(tmp_path, step_command=step_command, **kwargs)
    return RealECOMCPServer(
        config_path=str(config_path),
        wrapper_path=str(WRAPPER_PATH),
    )


# ── 仓库自带配置 ──

class TestShippedConfigs:
    def test_shipped_config_loads(self):
        # cwd 是仓库根目录（pytest rootdir）
        server = RealECOMCPServer(wrapper_path=str(WRAPPER_PATH))
        configured = set(server._config_resolved["step_command"])
        assert configured == set(STEP_NAMES)

    def test_shipped_timeout_values(self):
        server = RealECOMCPServer(wrapper_path=str(WRAPPER_PATH))
        assert server._get_timeout("run_sta") == 7200
        assert server._get_timeout("run_pt_fix_hold") == 1800

    def test_shipped_commands_fully_resolved(self):
        server = RealECOMCPServer(wrapper_path=str(WRAPPER_PATH))
        for step, cmd in server._config_resolved["step_command"].items():
            cmds = cmd if isinstance(cmd, list) else [cmd]
            for c in cmds:
                # 只有 fix 类允许 {fix_strategy} 留到运行时
                assert "{project_name}" not in c
                assert "{work_dir}" not in c
                assert "{base." not in c
                assert "{design_name}" not in c

    def test_shipped_run_fields_resolved(self):
        server = RealECOMCPServer(wrapper_path=str(WRAPPER_PATH))
        cfg = server._config_resolved
        assert cfg["design_name"] == "SOC_XXX_SUB"
        assert cfg["preco_db"].endswith("/DB/SOC_XXX_SUB_fixhold.enc")
        assert "/2.APR.SOC_XXX_SUB" in cfg["preco_db"]
        assert cfg["execution_dir"] == cfg["base"]["run_eco_route"]


# ── 占位符 resolve ──

class TestPlaceholderResolution:
    def test_nested_base_reference(self, tmp_path):
        # base.run_signoff = "{base.run_eco_route}" 应多轮迭代完整展开
        server = _make(tmp_path, {"run_ext": "echo x"})
        base = server._config_resolved["base"]
        assert base["run_signoff"] == base["run_eco_route"]
        assert base["run_signoff"] == f"{tmp_path}/apr"

    def test_design_name_inside_step_command(self, tmp_path):
        # 回归：step_command 里的 {design_name} 旧实现会解析成空
        server = _make(tmp_path, {"run_pv": "echo {design_name}"})
        assert server._config_resolved["step_command"]["run_pv"] == "echo TestDesign"

    def test_sub_design_name_override(self, tmp_path):
        server = _make(
            tmp_path, {"run_pv": "echo {design_name}"}, design_name="SubBlock_A",
        )
        assert server._config_resolved["step_command"]["run_pv"] == "echo SubBlock_A"
        assert server._config_resolved["design_name"] == "SubBlock_A"

    def test_fix_strategy_preserved_until_call(self, tmp_path):
        server = _make(
            tmp_path, {"run_pt_fix_setup": "pds_pteco {fix_strategy}"},
        )
        report = server.run_step(
            "run_pt_fix_setup", fix_strategy="setup_via_repair", dry_run=True,
        )
        assert report["commands"] == ["pds_pteco setup_via_repair"]

    def test_preco_db_resolves_base_reference(self, tmp_path):
        # preco_db = "{base.run_eco_route}/DB/..." 应引用 base 解析后的值
        server = _make(tmp_path, {"run_ext": "echo x"})
        cfg = server._config_resolved
        assert cfg["preco_db"] == f"{tmp_path}/apr/DB/TestDesign.enc"


# ── 启动期校验 ──

class TestValidation:
    def test_unknown_step_raises(self, tmp_path):
        with pytest.raises(Exception, match="未注册的 step"):
            _make(tmp_path, {"not_a_step": "echo x"})

    def test_empty_command_string_raises(self, tmp_path):
        with pytest.raises(Exception, match="空字符串"):
            _make(tmp_path, {"run_ext": "   "})

    def test_empty_command_list_raises(self, tmp_path):
        with pytest.raises(Exception, match="空 list"):
            _make(tmp_path, {"run_ext": []})

    def test_list_with_blank_item_raises(self, tmp_path):
        with pytest.raises(Exception, match="空 list"):
            _make(tmp_path, {"run_ext": ["echo ok", "  "]})

    def test_zero_timeout_raises(self, tmp_path):
        with pytest.raises(Exception, match="正整数"):
            _make(tmp_path, {"run_ext": "echo x"}, timeout_s={"run_ext": 0})

    def test_string_timeout_raises(self, tmp_path):
        with pytest.raises(Exception, match="正整数"):
            _make(
                tmp_path, {"run_ext": "echo x"},
                timeout_s={"run_ext": "7200"},  # type: ignore[dict-item]
            )


# ── run_step 行为 ──

class TestRunStepGuards:
    def test_invalid_step_name(self, tmp_path):
        server = _make(tmp_path, {"run_ext": "echo x"})
        with pytest.raises(Exception, match="不是合法 step"):
            server.run_step("run_xtop")  # 合法前缀但不是完整 step 名

    def test_step_not_configured(self, tmp_path):
        # run_sta 是合法 step，但这套临时配置里没配它的命令
        server = _make(tmp_path, {"run_ext": "echo x"})
        with pytest.raises(Exception, match="未在 config.yaml.step_command"):
            server.run_step("run_sta", dry_run=True)

    def test_unresolved_placeholder_raises(self, tmp_path):
        # {base} 指向 dict 会原样保留 → 运行前 guard 必须拦住
        server = _make(tmp_path, {"run_ext": "echo {base}"})
        with pytest.raises(Exception, match="未解析的占位符"):
            server.run_step("run_ext")


# ── timeout ──

class TestTimeout:
    def test_configured_timeout(self, tmp_path):
        server = _make(
            tmp_path, {"run_ext": "echo x"}, timeout_s={"run_ext": 1234},
        )
        assert server._get_timeout("run_ext") == 1234

    def test_default_timeout_fallback(self, tmp_path):
        server = _make(tmp_path, {"run_ext": "echo x"})
        assert server._get_timeout("run_sta") == DEFAULT_TIMEOUT_S
        assert DEFAULT_TIMEOUT_S > 0


# ── dry-run ──

class TestDryRun:
    def test_string_command(self, tmp_path):
        server = _make(tmp_path, {"run_ext": "echo single"})
        report = server.run_step("run_ext", dry_run=True)
        assert report["status"] == "dry_run"
        assert report["commands"] == ["echo single"]
        assert report["step"] == "run_ext"

    def test_list_commands(self, tmp_path):
        server = _make(
            tmp_path,
            {"run_sta": ["mkdir -p out", "run_sta.csh x y z"]},
            timeout_s={"run_sta": 999},
        )
        report = server.run_step("run_sta", dry_run=True)
        assert report["commands"] == ["mkdir -p out", "run_sta.csh x y z"]
        assert report["timeout_s"] == 999

    def test_dry_run_does_not_require_csh(self, tmp_path):
        # dry-run 不起子进程，无 csh 环境也能跑（本测试存在本身即验证）
        server = _make(tmp_path, {"run_ext": "echo x"})
        report = server.run_step("run_ext", dry_run=True)
        assert report["execution_dir"] == str(tmp_path)
