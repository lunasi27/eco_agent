from __future__ import annotations

from langgraph.types import interrupt

from src.state import ECOState


def _format_comparison(current: int, previous: int, label: str) -> str:
    if previous == 0 and current == 0:
        return f"{label}违例：{current}条"
    if previous == 0:
        return f"{label}违例：{current}条（首轮迭代，无历史对比）"
    diff = current - previous
    if diff == 0:
        return f"{label}违例：{current}条（较上一轮 持平）"
    elif diff < 0:
        return f"{label}违例：{current}条（较上一轮 {diff} 收敛）"
    else:
        return f"{label}违例：{current}条（较上一轮 +{diff} 恶化）"


def node_phase2_summary(state: ECOState) -> dict:
    step_status = state.get("step_status", {})
    phase_status = state.get("phase_status", {})

    if step_status.get("run_sta") == "error":
        return {
            "phase_status": {**phase_status, "phase2": "error"},
            "current_phase": "phase2",
            "current_step": "run_sta",
            "error_msg": state.get("error_msg", "run_sta 执行失败"),
        }

    iteration_cnt = state.get("iteration_cnt", 1)
    setup_vio = state.get("setup_vio", 0)
    hold_vio = state.get("hold_vio", 0)
    pv_pass = state.get("pv_pass", False)
    signoff_pass = state.get("signoff_pass", False)
    prev_setup_vio = state.get("prev_setup_vio", 0)
    prev_hold_vio = state.get("prev_hold_vio", 0)

    lines = [
        f"── Phase2 完成（第{iteration_cnt}轮迭代）──",
        "【STA时序报告】",
        _format_comparison(setup_vio, prev_setup_vio, "Setup"),
        _format_comparison(hold_vio, prev_hold_vio, "Hold"),
        "【PV电气校验】",
        f"PV通过：{pv_pass}",
        "【Signoff签核检查】",
        f"Signoff通过：{signoff_pass}",
    ]

    if step_status.get("run_pv") == "error":
        lines.append("[警告] PV 执行异常，请查看日志")
    if step_status.get("run_signoff") == "error":
        lines.append("[警告] Signoff 执行异常，请查看日志")

    lines.extend([
        "── 请选择修复策略 ──",
        "可选：setup / hold / leakage / leakage",
    ])

    interrupt_msg = "\n".join(lines)

    updated_phase_status = {**phase_status, "phase2": "done"}

    result = {
        "phase_status": updated_phase_status,
        "prev_setup_vio": setup_vio,
        "prev_hold_vio": hold_vio,
        "current_phase": "phase2",
        "interrupt_msg": interrupt_msg,
    }

    user_fix_strategy = interrupt(interrupt_msg)

    result["user_fix_strategy"] = user_fix_strategy

    return result
