from __future__ import annotations

import json
import os

from src.state import ECOState
from src.utils.constants import STEP_TO_PHASE


def _scan_step_logs(run_dir: str) -> list[str]:
    try:
        entries = []
        for fname in sorted(os.listdir(run_dir)):
            if fname.endswith(".log"):
                fpath = os.path.join(run_dir, fname)
                try:
                    with open(fpath) as f:
                        for line in f:
                            if "Status" in line:
                                status = line.split(":", 1)[1].strip()
                                step = fname.replace(".log", "")
                                mark = "✓" if status == "DONE" else "✗"
                                entries.append(
                                    f"  [{mark}] {step}.log  (status={status})"
                                )
                                break
                except OSError:
                    pass
        return entries
    except OSError:
        return []


def _aggregate_phase_elapsed(step_elapsed: dict) -> dict:
    phase_totals: dict[str, float] = {}
    for step, elapsed in step_elapsed.items():
        phase = STEP_TO_PHASE.get(step, "unknown")
        phase_totals[phase] = phase_totals.get(phase, 0.0) + elapsed
    return {k: round(v, 4) for k, v in phase_totals.items()}


def _build_convergence_text(history: list[dict]) -> str:
    if not history:
        return "  (no iteration history)"

    lines = []
    setup_values = []
    hold_values = []
    for rec in history:
        lines.append(
            f"  Iter {rec['iter']:>2}: setup {rec['setup_vio_before_fix']:>4} "
            f"→ {rec['setup_vio_after_fix']:>4}"
            f"  |  hold {rec['hold_vio_before_fix']:>4} "
            f"→ {rec['hold_vio_after_fix']:>4}"
            f"  [{rec.get('fix_strategy', '')}]"
        )
        setup_values.append((rec["iter"], rec["setup_vio_after_fix"]))
        hold_values.append((rec["iter"], rec["hold_vio_after_fix"]))

    if len(setup_values) >= 2:
        first = setup_values[0][1]
        last = setup_values[-1][1]
        pct = ((first - last) / first * 100) if first > 0 else 0
        lines.append(f"  Setup 收敛率: {pct:.1f}%  ({first} → {last})")

    if len(hold_values) >= 2:
        first_h = hold_values[0][1]
        last_h = hold_values[-1][1]
        pct_h = ((first_h - last_h) / first_h * 100) if first_h > 0 else 0
        lines.append(f"  Hold  收敛率: {pct_h:.1f}%  ({first_h} → {last_h})")

    return "\n".join(lines)


def node_finalize(state: ECOState) -> dict:
    run_dir = state.get("run_dir", "")
    iteration_cnt = state.get("iteration_cnt", 1)
    design_name = state.get("design_name", "")
    setup_vio = state.get("setup_vio", 0)
    hold_vio = state.get("hold_vio", 0)
    pv_pass = state.get("pv_pass", False)
    signoff_pass = state.get("signoff_pass", False)
    prev_setup = state.get("prev_setup_vio", None)
    prev_hold = state.get("prev_hold_vio", None)
    step_elapsed = state.get("step_elapsed", {})
    iteration_history = state.get("iteration_history", [])

    phase_elapsed = _aggregate_phase_elapsed(step_elapsed)
    total_elapsed = round(sum(step_elapsed.values()), 4)

    lines = []
    lines.append("═══════════════════════════════════════")
    lines.append("  ECO 流水线执行报告")
    lines.append("═══════════════════════════════════════")
    lines.append(f"Design         : {design_name}")
    lines.append(f"Total iterations: {iteration_cnt}")
    lines.append(f"Final Setup  : {setup_vio}")
    lines.append(f"Final Hold   : {hold_vio}")
    lines.append(f"PV pass      : {pv_pass}")
    lines.append(f"Signoff pass : {signoff_pass}")
    lines.append("")
    lines.append("── 收敛历史 ──")
    lines.append(_build_convergence_text(iteration_history))
    lines.append("")
    lines.append(f"── 耗时汇总（总 {total_elapsed:.2f}s） ──")

    for phase, elapsed in sorted(phase_elapsed.items()):
        pct = (elapsed / total_elapsed * 100) if total_elapsed > 0 else 0
        lines.append(f"  {phase:<10}: {elapsed:>7.4f}s  ({pct:>5.1f}%)")

    lines.append("")
    lines.append("── Step 明细耗时 ──")
    for step, elapsed in sorted(step_elapsed.items()):
        phase = STEP_TO_PHASE.get(step, "?")
        lines.append(f"  [{phase}] {step:<16} {elapsed:>8.4f}s")

    if run_dir and os.path.isdir(run_dir):
        log_files = _scan_step_logs(run_dir)
        rpt_files = sorted(f for f in os.listdir(run_dir) if f.endswith(".rpt"))
        lines.append("")
        lines.append(f"── 日志文件（{len(log_files)}） ──")
        lines.extend(log_files or ["  (none)"])
        lines.append("")
        lines.append(f"── 报告文件（{len(rpt_files)}） ──")
        if rpt_files:
            lines.extend(f"  - {f}" for f in rpt_files)
        else:
            lines.append("  (none)")

    json_report = {
        "design_name": design_name,
        "iteration_cnt": iteration_cnt,
        "final": {
            "setup_vio": setup_vio,
            "hold_vio": hold_vio,
            "pv_pass": pv_pass,
            "signoff_pass": signoff_pass,
        },
        "convergence": {
            "setup_prev": prev_setup,
            "hold_prev": prev_hold,
        },
        "elapsed_seconds": {
            "total": total_elapsed,
            "per_phase": phase_elapsed,
            "per_step": {k: float(v) for k, v in step_elapsed.items()},
        },
        "iteration_history": [
            {k: (int(v) if isinstance(v, float) and v == int(v) else v)
             for k, v in rec.items()}
            for rec in iteration_history
        ],
    }

    if run_dir:
        try:
            os.makedirs(run_dir, exist_ok=True)
            with open(os.path.join(run_dir, "FINISHED"), "w") as f:
                f.write("\n".join(lines) + "\n")
            with open(os.path.join(run_dir, "REPORT.json"), "w") as f:
                json.dump(json_report, f, indent=2, ensure_ascii=False)
        except OSError:
            pass

    summary = (
        f"流水线已完成。Design: {design_name}, 迭代轮次: {iteration_cnt}, "
        f"最终 Setup 违例: {setup_vio}, Hold 违例: {hold_vio}"
    )
    if prev_setup is not None and prev_setup != setup_vio:
        trend = "收敛" if setup_vio < prev_setup else ("恶化" if setup_vio > prev_setup else "持平")
        summary += f"，Setup {trend} {prev_setup}→{setup_vio}"

    return {
        "current_phase": "finalize",
        "interrupt_msg": summary,
    }
