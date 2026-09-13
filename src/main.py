from __future__ import annotations

import argparse
import os
import sqlite3
import sys

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver

from src.calling_layer.event_loop import run_event_loop
from src.graph_builder import build_graph
from src.mcp_server.mock import MockECOMCPServer
from src.utils.checkpoint_config import DEFAULT_DB_PATH
from src.utils.config_loader import load_config


def _create_checkpointer(backend: str, db_path: str, check_same_thread: bool = False):
    if backend == "sqlite":
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(db_path, check_same_thread=check_same_thread)
        return SqliteSaver(conn), db_path
    return MemorySaver(), ":memory:"


def _interactive_override(cfg: dict) -> dict:
    print("\n── 交互式覆盖（回车使用 config 值）──")

    default_scenario = cfg["eco_agent"]["default_scenario"]
    scenario = input(f"Scenario（回车={default_scenario} / convergence / phase2_sta_error / ...）: ").strip()
    if scenario:
        cfg["eco_agent"]["default_scenario"] = scenario

    ckpt = cfg["checkpoint"]
    backend = input(f"Checkpoint backend（回车={ckpt['backend']} / sqlite / memory）: ").strip().lower()
    if backend and backend in ("sqlite", "memory"):
        ckpt["backend"] = backend

    if ckpt["backend"] == "sqlite":
        db_path = input(f"SQLite DB 路径（回车={ckpt['db_path']}）: ").strip()
        if db_path:
            ckpt["db_path"] = db_path

    default_thread = cfg["eco_agent"]["default_thread_id"]
    thread_id = input(f"Thread ID（回车={default_thread}）: ").strip()
    if thread_id:
        cfg["eco_agent"]["default_thread_id"] = thread_id

    return cfg


def main():
    parser = argparse.ArgumentParser(description="ECO Agent - LangGraph ECO 固定流水线")
    parser.add_argument("--config", type=str, default=None, help="YAML 配置文件路径")
    parser.add_argument("--scenario", type=str, default=None, help="覆盖 scenario")
    parser.add_argument("--no-interactive", action="store_true", help="完全非交互模式（跳过 input 提示）")
    args = parser.parse_args()

    cfg = load_config(args.config)

    if args.no_interactive:
        if args.scenario:
            cfg["eco_agent"]["default_scenario"] = args.scenario
    else:
        print("=" * 50)
        print("  ECO Agent - 基于 LangGraph 的 ECO 固定流水线原型")
        print("=" * 50)
        cfg = _interactive_override(cfg)

    scenario = args.scenario or cfg["eco_agent"]["default_scenario"]
    thread_id = cfg["eco_agent"]["default_thread_id"]
    ckpt_cfg = cfg["checkpoint"]
    mock_cfg = cfg["mock_server"]

    mcp_server = MockECOMCPServer(
        scenario=scenario,
        simulate_delay=mock_cfg["simulate_delay"],
    )

    checkpointer, actual_path = _create_checkpointer(
        ckpt_cfg["backend"],
        ckpt_cfg["db_path"],
        ckpt_cfg.get("check_same_thread", False),
    )

    print(f"\n[配置] scenario={scenario}, thread_id={thread_id}")
    print(f"[Checkpointer] {type(checkpointer).__name__} → {actual_path}")

    graph = build_graph(mcp_server=mcp_server, checkpointer=checkpointer)

    config = {"configurable": {"thread_id": thread_id}}

    try:
        final_state = run_event_loop(
            graph=graph,
            config=config,
            initial_input=None,
        )
        print("\n执行完成。")
    except KeyboardInterrupt:
        print("\n\n[用户中断] 程序已终止。断点已保存，重启可用同一 thread_id 续跑。")
        sys.exit(0)
    except Exception as e:
        print(f"\n\n[错误] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
