"""EDA 工具日志解析。

parse patterns 描述的是「某个 EDA 工具的日志长什么样」，随工具版本走，
不随项目、不随迭代变，因此内置在代码里而不是放在用户 yaml 中。

后续升级方向（对用户配置无感知）：
- 正则不够用时，把 STEP_PARSE_PATTERNS 的值从正则换成 parser 函数；
- PDS 系统将来能直接统计 violations 时，改为读 PDS 的结构化输出。
"""

from __future__ import annotations

import re

# step → {输出字段: 正则}
# - 正则带捕获组时，取 group(1) 并做类型转换（int/float/bool/str）
# - 不带捕获组时，匹配到即为 True
STEP_PARSE_PATTERNS: dict[str, dict[str, str]] = {
    "run_sta": {
        "setup_vio": r"Setup Violations:\s*(\d+)",
        "hold_vio": r"Hold Violations:\s*(\d+)",
    },
    "run_pv": {
        "pv_pass": r"LVS check.*PASSED",
    },
    "run_signoff": {
        "signoff_pass": r"Signoff DRC.*PASSED",
    },
    "run_pt_fix_setup": {
        "setup_vio": r"Setup Violations After Fix:\s*(\d+)",
    },
    "run_pt_fix_hold": {
        "hold_vio": r"Hold Violations After Fix:\s*(\d+)",
    },
    "run_xtop_fix_hold": {
        "hold_vio": r"Hold Violations After Fix:\s*(\d+)",
    },
}


def parse_output(step_name: str, text: str) -> dict:
    """从 step 的 stdout+stderr 文本中按内置正则提取结构化字段。"""
    patterns = STEP_PARSE_PATTERNS.get(step_name, {})
    if not patterns:
        return {}

    result: dict = {}
    for key, pattern in patterns.items():
        m = re.search(pattern, text, re.IGNORECASE)
        if not m:
            continue
        if m.lastindex and m.lastindex >= 1:
            result[key] = coerce_value(m.group(1))
        else:
            result[key] = True
    return result


def coerce_value(value: str):
    """把正则捕获到的字符串自动转成 int / float / bool，转换不了就保留字符串。"""
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    lower = value.strip().lower()
    if lower in ("true", "yes", "pass", "passed", "success", "ok"):
        return True
    if lower in ("false", "no", "fail", "failed", "error"):
        return False
    return value
