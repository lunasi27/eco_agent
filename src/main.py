from __future__ import annotations

import argparse
import os
import sqlite3
import sys

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from src.calling_layer.event_loop import run_event_loop
from src.calling_layer.formatters import format_event, format_final_state, format_interrupt
from src.graph_builder import build_graph
from src.mcp_server.mock import MockECOMCPServer
from src.utils.checkpoint_config import DEFAULT_DB_PATH
from src.utils.config_loader import apply_pipeline_overrides, load_config


def _create_checkpointer(backend: str, db_path: str, check_same_thread: bool = False):
    if backend == "sqlite":
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(db_path, check_same_thread=check_same_thread)
        return SqliteSaver(conn), db_path
    return MemorySaver(), ":memory:"


def _create_llm(llm_cfg: dict):
    if not llm_cfg.get("enabled", False):
        return None

    provider = llm_cfg.get("provider", "openai").lower()
    model = llm_cfg.get("model", "gpt-4o-mini")
    base_url = llm_cfg.get("base_url")
    api_key_env = llm_cfg.get("api_key_env", "OPENAI_API_KEY")
    temperature = llm_cfg.get("temperature", 0)

    api_key = os.environ.get(api_key_env)
    if not api_key:
        print(f"[LLM] 警告: {api_key_env} 环境变量未设置，LLM 将不可用，回退到规则引擎")
        return None

    kwargs: dict = {"model": model, "temperature": temperature, "api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url

    if provider == "openai":
        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            print("[LLM] 警告: langchain-openai 未安装，回退到规则引擎")
            return None
        return ChatOpenAI(**kwargs)

    raise ValueError(f"[LLM] 不支持的 provider: {provider}")


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


def _auto_resume(state: dict) -> str:
    setup_vio = state.get("setup_vio", 0)
    hold_vio = state.get("hold_vio", 0)
    user_iter_choice = state.get("user_iter_choice", "")
    current_phase = state.get("current_phase", "")

    if user_iter_choice == "" and current_phase == "phase3":
        return "stop"

    if setup_vio > 0:
        return "setup"
    if hold_vio > 0:
        return "hold"
    return "leakage"


def _run_non_interactive(graph, config: dict, initial_input: dict) -> dict:
    current_input = initial_input

    while True:
        print("\n" + "-" * 40)
        for event in graph.stream(current_input, config):
            print(format_event(event))

        state = graph.get_state(config)
        if not state.next:
            print(format_final_state(state.values))
            return state.values

        auto_answer = _auto_resume(state.values)
        print(format_interrupt(state.values))
        print(f"[自动回答] {auto_answer}")

        current_input = Command(resume=auto_answer)


def main():
    parser = argparse.ArgumentParser(description="ECO Agent - LangGraph ECO 固定流水线")
    parser.add_argument("--config", type=str, default=None, help="YAML 配置文件路径")
    parser.add_argument("--scenario", type=str, default=None, help="覆盖 scenario")
    parser.add_argument("--design-name", type=str, default=None, help="覆盖 design_name")
    parser.add_argument("--no-interactive", action="store_true", help="完全非交互模式（跳过 input 提示）")
    parser.add_argument("--pipeline-name", type=str, default=None, help="pipeline 逻辑名（用于日志/UI 显示）")
    parser.add_argument(
        "--pipeline-steps-phase2",
        type=str,
        default=None,
        help="覆盖 Phase2 step 列表，逗号分隔（例: run_sta,run_pv）",
    )
    parser.add_argument(
        "--pipeline-steps-phase3",
        type=str,
        default=None,
        help="覆盖 Phase3 step 列表，逗号分隔（例: run_fix_setup,run_fix_hold）",
    )
    parser.add_argument("--skip-phase3", action="store_true", help="跳过 Phase3（Phase2_summary 直接 → finalize）")
    parser.add_argument(
        "--p3-router",
        type=str,
        default=None,
        help="Phase3 路由策略: user_choice / auto_setup / auto_hold / auto_leakage",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)

    cfg = apply_pipeline_overrides(
        cfg,
        p2_steps=(
            args.pipeline_steps_phase2.split(",")
            if args.pipeline_steps_phase2
            else None
        ),
        p3_steps=(
            args.pipeline_steps_phase3.split(",")
            if args.pipeline_steps_phase3
            else None
        ),
        skip_phase3=args.skip_phase3,
        p3_router=args.p3_router,
        pipeline_name=args.pipeline_name,
    )

    pipeline_cfg = cfg["pipeline"]
    p2_labels = pipeline_cfg["phases"]["phase2"]["steps"]
    p3_labels = pipeline_cfg["phases"].get("phase3", {}).get("steps", [])
    p3_router_val = pipeline_cfg["phases"].get("phase3", {}).get("router", "")

    if args.no_interactive:
        if args.scenario:
            cfg["eco_agent"]["default_scenario"] = args.scenario
        if args.design_name:
            cfg["eco_agent"]["default_design_name"] = args.design_name
    else:
        print("=" * 50)
        print("  ECO Agent - 基于 LangGraph 的 ECO 固定流水线原型")
        print("=" * 50)
        cfg = _interactive_override(cfg)

    scenario = args.scenario or cfg["eco_agent"]["default_scenario"]
    thread_id = cfg["eco_agent"]["default_thread_id"]
    design_name = args.design_name or cfg["eco_agent"]["default_design_name"]
    ckpt_cfg = cfg["checkpoint"]
    mock_cfg = cfg["mock_server"]
    llm_cfg = cfg["llm"]
    skip_agent_entry = args.no_interactive

    mcp_server = MockECOMCPServer(
        scenario=scenario,
        simulate_delay=mock_cfg["simulate_delay"],
    )

    checkpointer, actual_path = _create_checkpointer(
        ckpt_cfg["backend"],
        ckpt_cfg["db_path"],
        ckpt_cfg.get("check_same_thread", False),
    )

    llm_callable = _create_llm(llm_cfg)

    print(f"\n[Pipeline] name={pipeline_cfg['name']}, Phase2={p2_labels}, Phase3={p3_labels or '(跳过)'}, router={p3_router_val or 'N/A'}")
    print(f"[配置] scenario={scenario}, design={design_name}, thread_id={thread_id}")
    print(f"[Checkpointer] {type(checkpointer).__name__} → {actual_path}")
    if llm_callable is not None:
        print(f"[LLM] provider={llm_cfg['provider']}, model={llm_cfg['model']}")
    else:
        print("[LLM] 未启用，使用规则引擎做意图解析")

    graph = build_graph(
        mcp_server=mcp_server,
        checkpointer=checkpointer,
        llm_callable=llm_callable,
        skip_agent_entry=skip_agent_entry,
        pipeline=pipeline_cfg,
    )

    config = {"configurable": {"thread_id": thread_id}}
    initial_input = {"design_name": design_name} if skip_agent_entry else None

    try:
        if skip_agent_entry:
            final_state = _run_non_interactive(
                graph=graph,
                config=config,
                initial_input=initial_input,
            )
        else:
            final_state = run_event_loop(
                graph=graph,
                config=config,
                initial_input=initial_input,
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
