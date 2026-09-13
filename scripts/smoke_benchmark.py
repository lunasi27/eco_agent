"""
Smoke benchmark: 跑一遍 happy_path 完整流水线并输出耗时。
L1-L7 已由 pytest 完整覆盖，此脚本仅作快速冒烟验证 + 基准计时。

用法:
    PYTHONPATH=. python scripts/smoke_benchmark.py
"""
from __future__ import annotations

import sys
import time

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from src.graph_builder import build_graph
from src.mcp_server.mock import MockECOMCPServer


def run_happy_path(scenario: str = "happy_path") -> float:
    t0 = time.perf_counter()

    mcp = MockECOMCPServer(scenario=scenario, simulate_delay=0)
    g = build_graph(mcp_server=mcp, checkpointer=MemorySaver(), skip_agent_entry=True)
    cfg = {"configurable": {"thread_id": f"bench_{scenario}"}}

    list(g.stream({"design_name": "bench_design"}, cfg))
    list(g.stream(Command(resume="setup"), cfg))
    list(g.stream(Command(resume="stop"), cfg))

    elapsed = time.perf_counter() - t0
    s = g.get_state(cfg)
    return elapsed, s.values


def main():
    scenario = sys.argv[1] if len(sys.argv) > 1 else "happy_path"
    print(f"=== Smoke Benchmark: scenario={scenario} ===")
    elapsed, state = run_happy_path(scenario)
    print(f"  elapsed: {elapsed:.3f}s")
    print(f"  setup_vio: {state.get('setup_vio')}")
    print(f"  hold_vio:  {state.get('hold_vio')}")
    print(f"  iteration_history: {len(state.get('iteration_history', []))} rounds")
    print("=== PASS ===")


if __name__ == "__main__":
    main()
