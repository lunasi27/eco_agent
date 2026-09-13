from __future__ import annotations

import os
from typing import Any

import yaml


DEFAULTS: dict[str, Any] = {
    "eco_agent": {
        "default_scenario": "happy_path",
        "default_thread_id": "demo_session",
    },
    "llm": {
        "enabled": False,
        "provider": "openai",
        "model": "gpt-4o-mini",
        "base_url": None,
        "api_key_env": "OPENAI_API_KEY",
        "temperature": 0,
    },
    "checkpoint": {
        "backend": "memory",
        "db_path": "runs/checkpoints.sqlite",
        "check_same_thread": False,
    },
    "mock_server": {
        "simulate_delay": 0.3,
        "write_logs": True,
    },
    "init_validation": {
        "require_design_dir": False,
        "require_netlist": False,
        "require_sdc": False,
        "required_env_vars": [],
        "required_tools": [],
    },
    "finalize": {
        "write_json_report": True,
        "write_finished_marker": True,
        "log_glob": "*.log",
        "rpt_glob": "*.rpt",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for k, v in override.items():
        if (
            k in merged
            and isinstance(merged[k], dict)
            and isinstance(v, dict)
        ):
            merged[k] = _deep_merge(merged[k], v)
        else:
            merged[k] = v
    return merged


def load_config(path: str | None = None) -> dict:
    if path and os.path.isfile(path):
        with open(path) as f:
            user_cfg = yaml.safe_load(f) or {}
        return _deep_merge(DEFAULTS, user_cfg)
    return dict(DEFAULTS)


def get_section(cfg: dict, section: str) -> dict:
    return dict(cfg.get(section, {}))
