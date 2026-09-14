from __future__ import annotations

import argparse
import os
import sqlite3
import sys

from dotenv import load_dotenv
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from src.calling_layer.commands import SessionExit, new_thread_id
from src.calling_layer.event_loop import run_event_loop
from src.calling_layer.formatters import format_event, format_final_state, format_interrupt
from src.graph_builder import build_graph
from src.mcp_server.mock import MockECOMCPServer
from src.utils.config_loader import apply_pipeline_overrides, load_config
from src.utils.session_store import (
    STATUS_COMPLETED,
    STATUS_INTERRUPTED,
    classify_snapshot,
    delete_thread,
)

load_dotenv()


def _create_checkpointer(backend: str, db_path: str, check_same_thread: bool = False):
    if backend == "sqlite":
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(db_path, check_same_thread=check_same_thread)
        return SqliteSaver(conn), db_path
    return MemorySaver(), ":memory:"


def _create_llm(llm_cfg: dict):
    """enabled=true 时 LLM 是硬性要求：密钥缺失/依赖缺失都直接报错退出，
    不静默降级到规则引擎（静默降级会让行为与配置不符且难以察觉）。
    想用规则引擎请显式设置 llm.enabled=false。"""
    if not llm_cfg.get("enabled", False):
        return None

    provider = llm_cfg.get("provider", "openai").lower()
    model = llm_cfg.get("model", "gpt-4o-mini")
    base_url = llm_cfg.get("base_url")
    # api_key_env 是自由配置项：密钥存放在哪个环境变量由你决定，与 provider 解耦。
    # 例如 DeepSeek 用 DEEPSEEK_API_KEY、豆包用 ARK_API_KEY（名字随你起）。
    api_key_env = llm_cfg.get("api_key_env", "OPENAI_API_KEY")
    temperature = llm_cfg.get("temperature", 0)

    api_key = os.environ.get(api_key_env)
    if not api_key:
        print(
            f"\n[LLM] 配置错误: llm.enabled=true（model={model}），"
            f"但环境变量 {api_key_env} 未设置。\n\n"
            "  解决方式（三选一）:\n"
            f"    1) export {api_key_env}=sk-你的密钥\n"
            f"    2) 在项目根目录 .env 文件中写入: {api_key_env}=sk-你的密钥\n"
            f"    3) 密钥存在别的变量里: 修改配置 llm.api_key_env 指向该变量名\n"
            "       （api_key_env 与 provider 解耦，变量名由你自定）\n\n"
            "  如果确实不想用 LLM，请把配置 llm.enabled 改为 false。"
        )
        sys.exit(1)

    kwargs: dict = {"model": model, "temperature": temperature, "api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url

    if provider == "openai":
        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            print(
                "\n[LLM] 配置错误: llm.enabled=true 但依赖 langchain-openai 未安装。\n"
                "  解决方式: pip install langchain-openai\n"
                "  如果确实不想用 LLM，请把配置 llm.enabled 改为 false。"
            )
            sys.exit(1)
        chat = ChatOpenAI(**kwargs)
        # 项目约定 llm_callable 是 Callable[[list], object]（tests 也以普通函数伪造）；
        # ChatOpenAI 是 LangChain Runnable，需 .invoke()，在此适配成普通函数
        return lambda messages: chat.invoke(messages)

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

    thread_id = input("Thread ID（回车=自动生成新会话ID；输入已有ID可续跑）: ").strip()
    cfg["eco_agent"]["default_thread_id"] = thread_id

    return cfg


def _auto_resume(state: dict) -> str:
    step_status = state.get("step_status", {})
    has_error = any(s == "error" for s in step_status.values())

    if has_error:
        return "abort"

    current_phase = state.get("current_phase", "")
    phase_status = state.get("phase_status", {})
    phase3_done = phase_status.get("phase3") == "done"

    if current_phase == "phase3" or phase3_done:
        return "stop"

    setup_vio = state.get("setup_vio", 0)
    hold_vio = state.get("hold_vio", 0)

    if setup_vio > 0:
        return "setup"
    if hold_vio > 0:
        return "hold"
    return "leakage"


def _run_non_interactive(graph, config: dict, initial_input: dict | None) -> dict:
    current_input = initial_input  # resume 模式下为 None → 从断点继续

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
    parser.add_argument("--thread-id", type=str, default=None, help="指定会话 thread_id（checkpoint 身份）")
    parser.add_argument("--fresh", action="store_true", help="强制生成全新 thread_id，不看任何已有 checkpoint")
    parser.add_argument("--no-interactive", action="store_true", help="完全非交互模式（跳过 agent_entry，全自动跑流水线）")
    parser.add_argument("--interactive", action="store_true", help="启动前弹交互式配置覆盖（默认不弹，直接用 yaml 值）")
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
        help="覆盖 Phase3 step 列表，逗号分隔（例: run_pt_fix_setup,run_pt_fix_hold）",
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

    print("=" * 50)
    print("  ECO Agent - 基于 LangGraph 的 ECO 固定流水线原型")
    print("=" * 50)
    if args.interactive:
        cfg = _interactive_override(cfg)

    scenario = args.scenario or cfg["eco_agent"]["default_scenario"]
    yaml_thread_id = cfg["eco_agent"].get("default_thread_id") or ""
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
    print(f"[配置] scenario={scenario}, design={design_name}")
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

    # ========== Session 生命周期决策 ==========
    # 原则：启动零提示。交互模式每次默认全新会话；会话的续跑/切换/查看
    # 一律在会话内用 /resume、/sessions 完成。
    resume_mode = False

    # ---- 决定本次使用的 thread_id ----
    if args.fresh:
        thread_id = new_thread_id("run" if skip_agent_entry else "chat")
    elif args.thread_id:
        thread_id = args.thread_id
    elif skip_agent_entry and yaml_thread_id:
        # 非交互批处理：用确定性 id，进程崩溃后重跑同一命令即可自动续跑
        thread_id = yaml_thread_id
    else:
        # 对话模式默认每次都是全新会话
        thread_id = new_thread_id("chat")

    status = classify_snapshot(
        graph.get_state({"configurable": {"thread_id": thread_id}})
    )
    if status == STATUS_INTERRUPTED:
        if args.thread_id or skip_agent_entry:
            # 显式指定 id / 批处理模式：语义是「接管这个确定性会话」，自动续跑
            snap = graph.get_state({"configurable": {"thread_id": thread_id}})
            v = snap.values or {}
            print(
                f"\n[Session] 中断会话已自动续跑: design={v.get('design_name') or '-'}, "
                f"停在 {v.get('current_phase', '')}/{v.get('current_step', '')}"
            )
            resume_mode = True
        else:
            # 随机 id 撞上旧断点（理论上几乎不可能）：换个新 id 开新会话
            thread_id = new_thread_id("chat")
    elif status == STATUS_COMPLETED:
        print(f"\n[Session] 发现已结束的残留会话 '{thread_id}'，自动清理后开启新会话")
        delete_thread(checkpointer, thread_id)

    mode_label = "resume 续跑" if resume_mode else ("non-interactive 批处理" if skip_agent_entry else "新对话")
    print(f"[Session] thread_id={thread_id}，模式={mode_label}")

    config = {"configurable": {"thread_id": thread_id}}
    if resume_mode:
        initial_input = None
    elif skip_agent_entry:
        initial_input = {"design_name": design_name}
    else:
        initial_input = {"messages": []}

    try:
        if skip_agent_entry:
            _run_non_interactive(
                graph=graph,
                config=config,
                initial_input=initial_input,
            )
        else:
            run_event_loop(
                graph=graph,
                config=config,
                initial_input=initial_input,
            )

        # 正常走到 END：会话生命周期结束，清理 checkpoint（中断/崩溃现场不会走到这里）
        finished_id = config["configurable"]["thread_id"]
        try:
            delete_thread(checkpointer, finished_id)
            print(f"[Session] 会话 '{finished_id}' 已结束，checkpoint 已清理")
        except Exception as exc:
            print(f"[Session] 警告: checkpoint 清理失败: {exc}")
        print("\n执行完成。")
    except SessionExit:
        # 流水线中断点 /exit：断点保留，正常退出
        print("\n执行暂停。")
    except KeyboardInterrupt:
        tid = config["configurable"]["thread_id"]
        print(f"\n\n[用户中断] 程序已终止。断点已保存，重启后输入 /resume {tid} 续跑。")
        sys.exit(0)
    except Exception as e:
        tid = config["configurable"]["thread_id"]
        print(f"\n\n[错误] {e}")
        print(f"现场已保留，重启后输入 /resume {tid} 续跑。")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
