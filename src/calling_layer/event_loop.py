from __future__ import annotations

from langgraph.types import Command

from src.calling_layer.commands import (
    SessionExit,
    _in_conversation,  # noqa: F401  (re-export，兼容旧引用)
    _pending_interrupt,  # noqa: F401
    dispatch,
)
from src.calling_layer.formatters import (
    format_event,
    format_final_state,
    format_interrupt,
)

QUIT_HINT_TAIL = "重启后输入 /resume <thread_id> 续跑。"


def run_event_loop(graph, config: dict, initial_input: dict | None = None) -> dict:
    # resume 模式下 initial_input=None → graph.stream(None) 从 checkpoint 断点继续；
    # 新会话则传入 {"messages": []} 触发 agent_entry 的首次 interrupt。
    current_input = initial_input

    while True:
        print("\n" + "-" * 40)
        for event in graph.stream(current_input, config):
            print(format_event(event))

        state = graph.get_state(config)
        if not _pending_interrupt(state):
            print(format_final_state(state.values))
            return state.values

        # 命令子循环：/ 开头的输入在本地处理（图保持挂起），直到产生真正
        # 传给图的 resume 输入或退出/切换会话为止。
        while True:
            print(format_interrupt(state.values))
            user_input = input("> ").strip()

            if user_input.startswith("/"):
                result = dispatch(user_input, graph, config, state)
                if result.action == "handled":
                    continue
                if result.action == "quit":
                    print("再见！会话已结束。")
                    return state.values
                if result.action == "quit_keep":
                    print("流水线断点已保留。" + QUIT_HINT_TAIL)
                    raise SessionExit()
                if result.action == "resume_text":
                    current_input = Command(resume=result.resume_text)
                    break
                if result.action == "switch":
                    # /resume、/new：改写 thread_id 后用 next_input 重新 stream。
                    # next_input=None → 目标会话从断点继续；{"messages": []} → 新会话打招呼。
                    config["configurable"]["thread_id"] = result.new_thread_id
                    current_input = result.next_input
                    break
                continue

            current_input = Command(resume=user_input)
            break
