"""生成 ECO Agent LangGraph 拓扑图。

直接读 builder.nodes + builder.edges + builder.branches，
拼成 Mermaid flowchart → 输出 graph_topology.html。
"""
from __future__ import annotations

from src.graph_builder import build_graph
from src.mcp_server.mock import MockECOMCPServer
from langgraph.checkpoint.memory import MemorySaver


def main() -> None:
    mcp = MockECOMCPServer(scenario="happy_path")
    g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
    builder = g.builder

    lines: list[str] = [
        "graph TD",
        "    classDef startend fill:#e1f5fe,stroke:#0288d1,stroke-width:2px",
        "    classDef phase fill:#fff3e0,stroke:#f57c00,stroke-width:1px",
        "    classDef step fill:#f1f8e9,stroke:#558b2f,stroke-width:1px",
        "    classDef fixstep fill:#fce4ec,stroke:#c2185b,stroke-width:1px",
        "    classDef gate fill:#ede7f6,stroke:#5e35b1,stroke-width:2px",
        "    classDef error fill:#ffebee,stroke:#c62828,stroke-width:2px",
        "    classDef final fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px",
        "",
        "    subgraph ENTRY [入口层]",
        "        __start__([START])",
        "        agent_entry{{agent_entry<br/>意图解析}}",
        "        chat_fallback[chat_fallback<br/>无意图则跳过]",
        "    end",
        "",
        "    subgraph PHASE0 [Phase 0 - 初始化]",
        "        init[init<br/>加载Design/校验]",
        "    end",
        "",
        "    subgraph PHASE1 [Phase 1 - Route + Ext]",
        "        run_eco_route[run_eco_route<br/>布线]",
        "        run_ext[run_ext<br/>寄生提取]",
        "    end",
        "",
        "    subgraph PHASE2 [Phase 2 - 并行 STA + PV + Signoff]",
        "        run_sta[run_sta<br/>静态时序分析]",
        "        run_pv[run_pv<br/>物理验证]",
        "        run_signoff[run_signoff<br/>签收检查]",
        "        phase2_gate{{phase2_gate<br/>barrier汇合}}",
        "        phase2_summary[phase2_summary<br/>违例汇总+中断]",
        "    end",
        "",
        "    subgraph PHASE3 [Phase 3 - 物理修复]",
        "        run_pt_fix_setup[run_pt_fix_setup<br/>Setup修复]",
        "        run_pt_fix_hold[run_pt_fix_hold<br/>Hold修复]",
        "        run_pt_fix_leakage[run_pt_fix_leakage<br/>Leakage修复]",
        "        phase3_gate{{phase3_gate<br/>barrier汇合}}",
        "        phase3_summary[phase3_summary<br/>修复汇总+迭代判断]",
        "    end",
        "",
        "    subgraph ERROR [异常处理]",
        "        error_handler[error_handler<br/>错误恢复/abort]",
        "    end",
        "",
        "    subgraph EXIT [出口]",
        "        finalize[finalize<br/>FINISHED报告生成]",
        "        __end__([END])",
        "    end",
        "",
    ]

    # 静态边（builder.edges）
    for src, dst in builder.edges:
        lines.append(f"    {src} --> {dst}")

    # 条件分支（builder.branches → {source: {name: BranchSpec(path=fn, ends={label: node})}}）
    branch_labels = {
        ("run_ext", "route"): {
            "run_sta": "进入Phase2",
            "error_handler": "失败",
        },
        ("phase2_gate", "route_after_phase2_gate"): {
            "phase2_summary": "全部OK",
            "error_handler": "有失败",
        },
        ("phase2_summary", "route"): {
            "run_pt_fix_setup": "选setup修复",
            "run_pt_fix_hold": "选hold修复",
            "run_pt_fix_leakage": "选leakage修复",
            "error_handler": "abort",
            "finalize": "已收敛",
        },
        ("phase3_gate", "route_after_phase3_gate"): {
            "phase3_summary": "全部OK",
            "error_handler": "有失败",
        },
        ("phase3_summary", "route_after_phase3_summary"): {
            "init": "继续迭代",
            "finalize": "收敛/停止",
            "error_handler": "abort",
        },
        ("agent_entry", "route_from_agent"): {
            "init": "有意图",
            "chat_fallback": "无意图",
        },
        ("init", "route_after_init"): {
            "run_eco_route": "OK",
            "error_handler": "校验失败",
        },
        ("run_eco_route", "route_after_run_eco_route"): {
            "run_ext": "OK",
            "error_handler": "布线失败",
        },
    }

    for source, branches in builder.branches.items():
        for bname, bspec in branches.items():
            mapping = branch_labels.get((source, bname), {})
            if not mapping and bspec.ends:
                # 没预设 label 的，直接用 ends 里的映射
                for key, target in bspec.ends.items():
                    label = mapping.get(target, key)
                    if label:
                        lines.append(f"    {source} -->|{label}| {target}")
                    else:
                        lines.append(f"    {source} --> {target}")
            for target, label in mapping.items():
                lines.append(f"    {source} -->|{label}| {target}")

    lines.extend([
        "",
        "    class __start__,__end__ startend",
        "    class run_eco_route,run_ext,run_sta,run_pv,run_signoff step",
        "    class run_pt_fix_setup,run_pt_fix_hold,run_pt_fix_leakage fixstep",
        "    class phase2_gate,phase3_gate gate",
        "    class error_handler error",
        "    class finalize final",
    ])

    mermaid = "\n".join(lines)

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>ECO Agent 拓扑图</title>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
<style>
  body {{ font-family: -apple-system, "Segoe UI", sans-serif; margin: 20px; background: #fafafa; }}
  h1 {{ color: #333; }}
  .mermaid {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
  .legend {{ margin-top: 20px; padding: 15px; background: white; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
  .legend span {{ display: inline-block; padding: 4px 10px; margin-right: 10px; border-radius: 4px; font-size: 13px; }}
</style>
</head><body>
<h1>ECO Agent - LangGraph 完整拓扑</h1>
<div class="mermaid">
{mermaid}
</div>
<div class="legend">
  <span style="background:#e1f5fe;border:1px solid #0288d1">START/END</span>
  <span style="background:#f1f8e9;border:1px solid #558b2f">Phase 1/2 普通步骤</span>
  <span style="background:#fce4ec;border:1px solid #c2185b">Phase 3 修复步骤</span>
  <span style="background:#ede7f6;border:1px solid #5e35b1">Barrier 汇合门</span>
  <span style="background:#ffebee;border:1px solid #c62828">错误处理</span>
  <span style="background:#e8f5e9;border:1px solid #2e7d32">最终报告</span>
</div>
<script>mermaid.initialize({{ startOnLoad: true, flowchart: {{ curve: 'basis' }} }});</script>
</body></html>
"""

    out = "graph_topology.html"
    with open(out, "w") as f:
        f.write(html)
    print(f"✅ 已生成 {out}")


if __name__ == "__main__":
    main()
