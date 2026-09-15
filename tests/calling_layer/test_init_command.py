"""/init 命令测试：扫描 EDA 目录 -> 生成 eco_agent/config.yaml。"""
from __future__ import annotations

import yaml
from pathlib import Path

from src.calling_layer.init_command import run_init, scan_eda_directory


def _make_eda_dir(tmp_path: Path, design: str = "SOC_TEST") -> Path:
    """在 tmp_path 下造一个最小 EDA 工程目录结构。"""
    (tmp_path / f"2.APR.{design}").mkdir()
    (tmp_path / f"3.EXT.{design}").mkdir()
    (tmp_path / f"4.PTECO.{design}").mkdir()
    (tmp_path / f"4.xtop.{design}").mkdir()
    (tmp_path / "0.outgoing" / design).mkdir(parents=True)
    (tmp_path / "common" / "0.c_shell").mkdir(parents=True)
    (tmp_path / "common" / "0.c_shell" / "project.cshrc").write_text("# IT env")
    return tmp_path


class TestScan:
    def test_scan_finds_project_name(self, tmp_path):
        _make_eda_dir(tmp_path, "MY_BLOCK")
        info = scan_eda_directory(tmp_path)
        assert info["project_name"] == "MY_BLOCK"

    def test_scan_finds_all_phase_dirs(self, tmp_path):
        _make_eda_dir(tmp_path)
        info = scan_eda_directory(tmp_path)
        phases = {p for p, _ in info["found_dirs"]}
        assert phases == {"APR", "EXT", "PTECO", "xtop"}

    def test_scan_finds_cshrc(self, tmp_path):
        _make_eda_dir(tmp_path)
        info = scan_eda_directory(tmp_path)
        assert info["project_cshrc"] is not None
        assert info["project_cshrc"].endswith("project.cshrc")

    def test_scan_non_eda_dir(self, tmp_path):
        (tmp_path / "random_dir").mkdir()
        (tmp_path / "some_file.txt").write_text("hello")
        info = scan_eda_directory(tmp_path)
        assert info["project_name"] is None

    def test_scan_nonexistent_dir(self, tmp_path):
        info = scan_eda_directory(tmp_path / "nope")
        assert info["project_name"] is None

    def test_scan_cshrc_not_found(self, tmp_path):
        _make_eda_dir(tmp_path)
        (tmp_path / "common" / "0.c_shell" / "project.cshrc").unlink()
        info = scan_eda_directory(tmp_path)
        assert info["project_cshrc"] is None


class TestRunInit:
    def test_generates_config_yaml(self, tmp_path):
        _make_eda_dir(tmp_path, "SOC_XXX")
        report = run_init(str(tmp_path))
        config_path = tmp_path / "eco_agent" / "config.yaml"
        assert config_path.exists()

        cfg = yaml.safe_load(config_path.read_text())
        assert cfg["project_name"] == "SOC_XXX"
        assert cfg["work_dir"] == str(tmp_path)
        assert "project.cshrc" in cfg["project_cshrc"]

    def test_report_shows_project_name(self, tmp_path):
        _make_eda_dir(tmp_path, "MY_DESIGN")
        report = run_init(str(tmp_path))
        assert "MY_DESIGN" in report
        assert "eco_agent/config.yaml" in report

    def test_report_shows_todo_items(self, tmp_path):
        _make_eda_dir(tmp_path)
        report = run_init(str(tmp_path))
        assert "run_sta_rpt" in report
        assert "dsub_queue" in report

    def test_report_shows_startup_command(self, tmp_path):
        _make_eda_dir(tmp_path)
        report = run_init(str(tmp_path))
        assert "ECO_CONFIG=" in report
        assert "ECO_BACKEND=real" in report

    def test_non_eda_dir_reports_error(self, tmp_path):
        report = run_init(str(tmp_path))
        assert "未能" in report or "❌" in report

    def test_nonexistent_dir_reports_error(self, tmp_path):
        report = run_init(str(tmp_path / "nope"))
        assert "❌" in report

    def test_existing_config_backed_up(self, tmp_path):
        _make_eda_dir(tmp_path, "SOC_XXX")
        eco_dir = tmp_path / "eco_agent"
        eco_dir.mkdir()
        old_config = eco_dir / "config.yaml"
        old_config.write_text("old: config\n")

        run_init(str(tmp_path))
        backup = eco_dir / "config.yaml.bak"
        assert backup.exists()
        assert backup.read_text() == "old: config\n"

    def test_generated_config_has_all_step_commands(self, tmp_path):
        _make_eda_dir(tmp_path, "SOC_XXX")
        run_init(str(tmp_path))
        cfg = yaml.safe_load(
            (tmp_path / "eco_agent" / "config.yaml").read_text()
        )
        from src.mcp_server.protocol import STEP_NAMES
        assert set(cfg["step_command"]) == set(STEP_NAMES)

    def test_default_work_dir_uses_cwd(self, tmp_path, monkeypatch):
        _make_eda_dir(tmp_path, "CWD_TEST")
        monkeypatch.chdir(tmp_path)
        report = run_init("")
        assert "CWD_TEST" in report
        assert (tmp_path / "eco_agent" / "config.yaml").exists()
