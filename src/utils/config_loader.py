from __future__ import annotations

import os
from typing import Any

import yaml

from src.utils.constants import PHASE_STEPS, STEP_TO_PHASE, VALID_FIX_STRATEGIES


DEFAULT_PIPELINE: dict[str, Any] = {
    "name": "default",
    "phases": {
        "phase1": {
            "steps": list(PHASE_STEPS["phase1"]),
            "type": "serial",
        },
        "phase2": {
            "steps": list(PHASE_STEPS["phase2"]),
            "type": "parallel",
            "error_policy": "all_block",
        },
        "phase3": {
            "steps": list(PHASE_STEPS["phase3"]),
            "type": "branch",
            "router": "user_choice",
            "gate": True,
        },
    },
}


DEFAULTS: dict[str, Any] = {
    "eco_agent": {
        "default_scenario": "happy_path",
        "default_thread_id": "demo_session",
        "default_design_name": "demo_design",
    },
    "pipeline": DEFAULT_PIPELINE,
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


VALID_PHASE_TYPES = {"serial", "parallel", "branch"}
VALID_ROUTERS = {"user_choice", "auto_setup", "auto_hold", "auto_leakage", "skip"}


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
        merged = _deep_merge(DEFAULTS, user_cfg)
    else:
        merged = dict(DEFAULTS)

    _validate_pipeline(merged.get("pipeline", {}))
    return merged


def _validate_pipeline(pipeline: dict[str, Any]) -> None:
    phases = pipeline.get("phases", {})

    p1_steps = phases.get("phase1", {}).get("steps", PHASE_STEPS["phase1"])
    for step in p1_steps:
        if STEP_TO_PHASE.get(step) != "phase1":
            raise ValueError(
                f"pipeline.phases.phase1.steps 包含非法 step: '{step}' "
                f"（属于 {STEP_TO_PHASE.get(step, '未知')}）"
            )

    p2_steps = phases.get("phase2", {}).get("steps", PHASE_STEPS["phase2"])
    if not p2_steps:
        raise ValueError("pipeline.phases.phase2.steps 不能为空")
    for step in p2_steps:
        if STEP_TO_PHASE.get(step) != "phase2":
            raise ValueError(
                f"pipeline.phases.phase2.steps 包含非法 step: '{step}' "
                f"（属于 {STEP_TO_PHASE.get(step, '未知')}）"
            )

    phase3_cfg = phases.get("phase3", {})
    p3_steps = phase3_cfg.get("steps", PHASE_STEPS["phase3"])
    for step in p3_steps:
        if STEP_TO_PHASE.get(step) != "phase3":
            raise ValueError(
                f"pipeline.phases.phase3.steps 包含非法 step: '{step}' "
                f"（属于 {STEP_TO_PHASE.get(step, '未知')}）"
            )

    if p3_steps:
        router = phase3_cfg.get("router", "user_choice")
        if router not in VALID_ROUTERS:
            raise ValueError(
                f"pipeline.phases.phase3.router 非法: '{router}' "
                f"（有效值: {VALID_ROUTERS}）"
            )
        if router.startswith("auto_"):
            fix_type = router.replace("auto_", "")
            required_step = f"run_pt_fix_{fix_type}"
            if required_step not in p3_steps:
                raise ValueError(
                    f"pipeline.phases.phase3.router='{router}' 需要 step "
                    f"'{required_step}'，但不在 phase3.steps 里"
                )


def apply_pipeline_overrides(
    cfg: dict[str, Any],
    *,
    p2_steps: list[str] | None = None,
    p3_steps: list[str] | None = None,
    skip_phase3: bool = False,
    p3_router: str | None = None,
    pipeline_name: str | None = None,
) -> dict[str, Any]:
    """
    CLI 参数覆盖 pipeline 配置。每次调用都返回新 dict，不修改原 cfg。
    """
    new_cfg = dict(cfg)
    pipeline = _deep_merge(DEFAULT_PIPELINE, cfg.get("pipeline", {}))

    if pipeline_name is not None:
        pipeline["name"] = pipeline_name

    if p2_steps is not None:
        pipeline.setdefault("phases", {}).setdefault("phase2", {})["steps"] = list(p2_steps)

    if skip_phase3:
        pipeline.setdefault("phases", {}).setdefault("phase3", {})["steps"] = []
    elif p3_steps is not None:
        pipeline.setdefault("phases", {}).setdefault("phase3", {})["steps"] = list(p3_steps)

    if p3_router is not None:
        pipeline.setdefault("phases", {}).setdefault("phase3", {})["router"] = p3_router

    new_cfg["pipeline"] = pipeline
    _validate_pipeline(pipeline)
    return new_cfg


def get_section(cfg: dict, section: str) -> dict:
    return dict(cfg.get(section, {}))
