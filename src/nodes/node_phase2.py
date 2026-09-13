from __future__ import annotations

from langgraph.types import interrupt

from src.state import ECOState
from src.utils.constants import PHASE_STEPS


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


def make_phase2_summary_node(
    p2_steps: tuple[str, ...],
    has_phase3: bool = True,
    p3_router: str = "user_choice",
):
    """
    Phase2 summary 节点工厂函数。
    - p2_steps: 闭包捕获的 Phase2 实际注册 step 列表（决定报告内容）
    - has_phase3: 是否有 Phase3（决定是否需要 interrupt 让用户选策略）
    - p3_router: Phase3 路由策略（user_choice 才需要 interrupt，auto_xxx 不需要）
    """

    def node(state: ECOState) -> dict:
        step_status = state.get("step_status", {})
        phase_status = state.get("phase_status", {})

        # 检查 Phase2 所有 step 的 error（闭包捕获的 p2_steps）
        for step in p2_steps:
            if step_status.get(step) == "error":
                return {
                    "phase_status": {**phase_status, "phase2": "error"},
                    "current_phase": "phase2",
                    "current_step": step,
                    "error_msg": state.get("error_msg", f"{step} 执行失败"),
                }

        iteration_cnt = state.get("iteration_cnt", 1)
        setup_vio = state.get("setup_vio", 0)
        hold_vio = state.get("hold_vio", 0)
        prev_setup_vio = state.get("prev_setup_vio", 0)
        prev_hold_vio = state.get("prev_hold_vio", 0)

        # 根据实际注册的 step 动态生成报告
        lines = [f"── Phase2 完成（第{iteration_cnt}轮迭代）──"]

        if "run_sta" in p2_steps:
            lines.append("【STA时序报告】")
            lines.append(_format_comparison(setup_vio, prev_setup_vio, "Setup"))
            lines.append(_format_comparison(hold_vio, prev_hold_vio, "Hold"))

        if "run_pv" in p2_steps:
            pv_pass = state.get("pv_pass", False)
            lines.append("【PV电气校验】")
            lines.append(f"PV通过：{pv_pass}")

        if "run_signoff" in p2_steps:
            signoff_pass = state.get("signoff_pass", False)
            lines.append("【Signoff签核检查】")
            lines.append(f"Signoff通过：{signoff_pass}")

        # 动态生成非阻断警告
        for step in p2_steps:
            if step != "run_sta" and step_status.get(step) == "error":
                label_map = {"run_pv": "PV", "run_signoff": "Signoff"}
                lines.append(f"[警告] {label_map.get(step, step)} 执行异常，请查看日志")

        updated_phase_status = {**phase_status, "phase2": "done"}

        result = {
            "phase_status": updated_phase_status,
            "prev_setup_vio": setup_vio,
            "prev_hold_vio": hold_vio,
            "current_phase": "phase2",
        }

        # 只有当有 Phase3 且 router 是 user_choice 时才需要 interrupt
        if has_phase3 and p3_router == "user_choice":
            lines.extend([
                "── 请选择修复策略 ──",
                "可选：setup / hold / leakage / leakage",
            ])
            interrupt_msg = "\n".join(lines)
            result["interrupt_msg"] = interrupt_msg
            user_fix_strategy = interrupt(interrupt_msg)
            result["user_fix_strategy"] = user_fix_strategy
        else:
            # 没有 Phase3 或 Phase3 硬路由——不需要 interrupt，直接完成
            result["interrupt_msg"] = "\n".join(lines)

        return result

    return node


def node_phase2_summary(state: ECOState) -> dict:
    """向后兼容：硬编码默认 Phase2 配置。"""
    return make_phase2_summary_node(
        PHASE_STEPS["phase2"], has_phase3=True, p3_router="user_choice"
    )(state)
