from __future__ import annotations

from langgraph.types import interrupt

from src.state import ECOState
from src.utils.constants import normalize_iter_choice


def node_phase3_summary(state: ECOState) -> dict:
    step_status = state.get("step_status", {})
    phase_status = state.get("phase_status", {})

    iteration_cnt = state.get("iteration_cnt", 1)
    setup_vio = state.get("setup_vio", 0)
    hold_vio = state.get("hold_vio", 0)
    prev_setup_vio = state.get("prev_setup_vio", 0)
    prev_hold_vio = state.get("prev_hold_vio", 0)
    pv_pass = state.get("pv_pass", False)
    signoff_pass = state.get("signoff_pass", False)
    step_elapsed = state.get("step_elapsed", {})
    user_fix_strategy = state.get("user_fix_strategy", "")

    lines = [
        f"── Phase3 完成（第{iteration_cnt}轮迭代）──",
    ]

    # 自环重问：上一轮输入非法时，在报告前追加无效输入提示
    invalid_input = state.get("invalid_input", "")
    if invalid_input:
        lines.append(
            f"[无效输入] '{invalid_input}' 不是合法选择，请重新选择。"
        )

    lines.extend([
        "【本轮修复结果】",
        f"修复策略：{user_fix_strategy}",
        f"修复后 Setup 违例：{setup_vio}条（上一轮 {prev_setup_vio}条）",
        f"修复后 Hold 违例：{hold_vio}条（上一轮 {prev_hold_vio}条）",
        "── 是否继续下一轮迭代？──",
        "可选：continue / stop",
    ])

    interrupt_msg = "\n".join(lines)

    raw_choice = interrupt(interrupt_msg)
    user_iter_choice = normalize_iter_choice(raw_choice)

    if not user_iter_choice:
        # 非法输入：不提交选择、不追加迭代历史，回到本节点重新 interrupt。
        # 未知输入绝不能被默认当成 stop 而静默终结流水线。
        return {
            "current_phase": "phase3",
            "interrupt_msg": interrupt_msg,
            "user_iter_choice": "",
            "invalid_input": str(raw_choice),
        }

    # 校验通过后才追加本轮迭代记录（避免自环重放时 history 重复 append）
    updated_phase_status = {**phase_status, "phase3": "done"}

    iter_record = {
        "iter": iteration_cnt,
        "setup_vio_before_fix": prev_setup_vio,
        "hold_vio_before_fix": prev_hold_vio,
        "setup_vio_after_fix": setup_vio,
        "hold_vio_after_fix": hold_vio,
        "pv_pass": pv_pass,
        "signoff_pass": signoff_pass,
        "fix_strategy": user_fix_strategy,
        "step_elapsed": dict(step_elapsed),
    }

    existing_history = list(state.get("iteration_history", []))
    existing_history.append(iter_record)

    result = {
        "phase_status": updated_phase_status,
        "current_phase": "phase3",
        "interrupt_msg": interrupt_msg,
        "user_iter_choice": user_iter_choice,
        "invalid_input": "",
        "iteration_history": existing_history,
    }

    if user_iter_choice == "continue":
        result["iteration_cnt"] = iteration_cnt + 1
        result["prev_setup_vio"] = setup_vio
        result["prev_hold_vio"] = hold_vio

    return result
