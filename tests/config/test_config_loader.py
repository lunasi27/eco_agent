from __future__ import annotations

import os
import tempfile

import pytest

from src.utils.checkpoint_config import DEFAULT_DB_PATH, DEFAULT_DB_URL, MEM_DB_URL
from src.utils.config_loader import DEFAULTS, get_section, load_config


class TestCheckpointConfig:
    def test_constants_exist(self):
        assert DEFAULT_DB_PATH.endswith(".sqlite")
        assert "sqlite" in DEFAULT_DB_URL
        assert ":memory:" in MEM_DB_URL


class TestConfigLoader:
    def test_load_defaults_when_no_path(self):
        cfg = load_config()
        assert cfg == DEFAULTS

    def test_load_nonexistent_file_returns_defaults(self):
        cfg = load_config("config/nonexistent.yaml")
        assert cfg == DEFAULTS

    def test_load_default_yaml_overrides(self):
        cfg = load_config("config/default.yaml")
        assert cfg["checkpoint"]["backend"] == "sqlite"
        assert cfg["eco_agent"]["default_scenario"] == "happy_path"
        assert cfg["eco_agent"]["default_thread_id"] == "demo_session"

    def test_load_production_yaml(self):
        cfg = load_config("config/production.yaml")
        assert cfg["init_validation"]["require_design_dir"] is True
        assert cfg["init_validation"]["required_env_vars"] == [
            "EDA_LICENSE_FILE",
            "LM_LICENSE_FILE",
        ]
        assert cfg["checkpoint"]["backend"] == "sqlite"

    def test_deep_merge_preserves_unmentioned_sections(self):
        cfg = load_config("config/default.yaml")
        mock_cfg = get_section(cfg, "mock_server")
        assert "simulate_delay" in mock_cfg
        assert "write_logs" in mock_cfg

    def test_get_section_returns_copy(self):
        section = get_section(DEFAULTS, "checkpoint")
        section["backend"] = "custom"
        assert DEFAULTS["checkpoint"]["backend"] != "custom"

    def test_load_via_pathlib_string(self):
        cfg = load_config(os.path.abspath("config/default.yaml"))
        assert cfg["checkpoint"]["backend"] == "sqlite"

    def test_in_memory_config_can_be_saved_and_reloaded(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_path = os.path.join(td, "custom.yaml")
            with open(tmp_path, "w") as f:
                f.write("eco_agent:\n  default_scenario: convergence\n")
            cfg = load_config(tmp_path)
            assert cfg["eco_agent"]["default_scenario"] == "convergence"
            assert cfg["checkpoint"]["backend"] == DEFAULTS["checkpoint"]["backend"]
