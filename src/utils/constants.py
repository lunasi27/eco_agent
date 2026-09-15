VALID_FIX_STRATEGIES = {"setup", "hold", "leakage"}
VALID_ITER_CHOICES = {"continue", "stop"}


def normalize_fix_strategy(raw: str) -> str:
    """只接受提示中给出的关键词 setup/hold/leakage（大小写、首尾空白不敏感）；
    其他输入一律返回空串，由节点自环重问。"""
    if raw is None:
        return ""
    val = raw.strip().lower()
    return val if val in VALID_FIX_STRATEGIES else ""


def normalize_iter_choice(raw: str) -> str:
    """只接受提示中给出的关键词 continue/stop（大小写、首尾空白不敏感）；
    其他输入一律返回空串，由节点自环重问。"""
    if raw is None:
        return ""
    val = raw.strip().lower()
    return val if val in VALID_ITER_CHOICES else ""

PHASES = ("init", "phase1", "phase2", "phase3")

STEP_NAMES = (
    "run_eco_route",
    "run_ext",
    "run_sta",
    "run_pv",
    "run_signoff",
    "run_pt_fix_setup",
    "run_pt_fix_hold",
    "run_pt_fix_leakage",
)

PHASE_STEPS = {
    "phase1": ("run_eco_route", "run_ext"),
    "phase2": ("run_sta", "run_pv", "run_signoff"),
    "phase3": ("run_pt_fix_setup", "run_pt_fix_hold", "run_pt_fix_leakage"),
}

STRATEGY_TO_STEP = {
    "setup": "run_pt_fix_setup",
    "hold": "run_pt_fix_hold",
    "leakage": "run_pt_fix_leakage",
}

STEP_TO_PHASE = {
    "run_eco_route": "phase1",
    "run_ext": "phase1",
    "run_sta": "phase2",
    "run_pv": "phase2",
    "run_signoff": "phase2",
    "run_pt_fix_setup": "phase3",
    "run_pt_fix_hold": "phase3",
    "run_pt_fix_leakage": "phase3",
}

BASE_RUN_DIR = "runs"

INITIAL_PHASE_STATUS = {
    "init": "pending",
    "phase1": "pending",
    "phase2": "pending",
    "phase3": "pending",
}
