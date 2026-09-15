from __future__ import annotations

from typing import Any


def format_event(event: dict) -> str:
    if not event:
        return ""
    node_name = list(event.keys())[0]
    node_output = event[node_name]
    return f"  [{node_name}] → {_format_output(node_output)}"


def _format_output(output: Any) -> str:
    if output is None:
        return "done"
    if isinstance(output, dict):
        summary_fields = ["step_status", "phase_status", "current_phase", "current_step", "error_msg"]
        parts = []
        for k in summary_fields:
            if k in output:
                parts.append(f"{k}={output[k]}")
        if parts:
            return ", ".join(parts)
        return str({k: v for k, v in output.items() if k != "messages"})
    return str(output)


def format_interrupt(state) -> str:
    """格式化中断提示。

    关键：interrupt() 暂停节点时，节点内对 state 的写入（包括 interrupt_msg）
    **尚未提交到 checkpoint**，因此不能从 state.values['interrupt_msg'] 读取
    （那会读到空值或上一轮的旧消息）。真实的中断消息在 StateSnapshot
    顶层的 state.interrupts[].value（等价于 state.tasks[].interrupts[].value）。
    """
    values = state.values or {}

    interrupts = getattr(state, "interrupts", None)
    if not interrupts:
        # 兼容不同 LangGraph 版本：从 tasks 里收集
        collected = []
        for task in (getattr(state, "tasks", None) or ()):
            collected.extend(getattr(task, "interrupts", None) or ())
        interrupts = collected

    msg = ""
    for intr in interrupts:
        val = getattr(intr, "value", "")
        # interrupt() 的 value 可能是单个值或元组（多 interrupt 场景）
        if isinstance(val, (list, tuple)):
            val = val[0] if val else ""
        if val:
            msg = str(val)
            break

    if not msg:
        msg = values.get("interrupt_msg", "")
    current_phase = values.get("current_phase", "")
    return f"\n{'='*50}\n[中断] phase={current_phase}\n{msg}\n{'='*50}"


def format_final_state(state_values: dict) -> str:
    design_name = state_values.get("design_name", "")
    iteration_cnt = state_values.get("iteration_cnt", 0)
    setup_vio = state_values.get("setup_vio", 0)
    hold_vio = state_values.get("hold_vio", 0)
    return (
        f"\n===== 流水线执行完毕 =====\n"
        f"Design: {design_name}\n"
        f"迭代轮次: {iteration_cnt}\n"
        f"最终 Setup 违例: {setup_vio}\n"
        f"最终 Hold 违例: {hold_vio}"
    )
