"""会话内 slash 命令层（对标 Claude Code 的 /command 交互）。

命令由事件循环在「图挂起等待用户输入」时拦截分发，分四类动作：
- ``handled``     ：本地处理完（打印信息），图仍挂起，重新打提示符
- ``resume_text`` ：合成一段自然语言作为 resume 输入传给图（/run_eco）
- ``switch``      ：切换 thread_id（/resume /new），改写 config 后重新 stream
- ``quit``        ：对话阶段退出（正常返回，main 负责 checkpoint 清理）
- ``quit_keep``   ：流水线阶段退出（断点保留，事件循环抛 SessionExit）

原则：流水线提示的领域答案（setup/hold/leakage 等）保持纯文本，命令一律
以 ``/`` 前缀，二者零冲突。
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from src.utils.session_store import (
    STATUS_COMPLETED,
    STATUS_MISSING,
    classify_snapshot,
    delete_thread,
    describe_sessions,
)

CONVERSATION_NODES = {"agent_entry", "chat_fallback"}

QUIT_HINT = "重启后输入 /resume <thread_id> 续跑（/sessions 可查看所有会话）"


class SessionExit(Exception):
    """用户在流水线中断点执行 /exit：断点保留，进程正常退出。"""


@dataclass
class CommandResult:
    action: str  # handled | resume_text | switch | quit | quit_keep
    resume_text: str = ""
    new_thread_id: str = ""
    next_input: object = None


def new_thread_id(prefix: str) -> str:
    return f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def _pending_interrupt(state) -> bool:
    """图是否停在一个未回答的 interrupt 上。

    注意不能只看 state.next：LangGraph 恢复任务时任务从头重放，若任务已带
    写入记录会被排除出 state.next，因此还要检查 tasks 里的挂起中断。
    """
    if state.next:
        return True
    return any(getattr(t, "interrupt", None) is not None for t in (state.tasks or ()))


def _in_conversation(state) -> bool:
    """当前挂起的中断是否处于对话节点（打招呼/闲聊循环）。

    首次 interrupt 表现为 state.next=(节点名,)；恢复过的任务则挂在
    state.tasks 的 interrupt 字段上，两处都要查。
    """
    names = set(state.next or ())
    names |= {
        getattr(t, "name", "")
        for t in (state.tasks or ())
        if getattr(t, "interrupt", None) is not None
    }
    return bool(names) and names <= CONVERSATION_NODES


HELP_TEXT = """可用命令：
  /help                显示本帮助
  /init [工作目录]     扫描 EDA 目录，生成 eco_agent/config.yaml
  /status              当前会话状态（阶段/步骤/违例数）
  /run_eco <design名>  直接指定 design 开始跑 ECO 流水线
  /sessions            列出所有已保存的会话
  /resume <thread_id>  切换/恢复某个中断中的会话
  /new                 放弃当前对话，开启新会话
  /exit                退出（流水线跑到一半时断点保留，可 /resume 找回）"""


def _print_session_list(infos: list) -> None:
    if not infos:
        print("(没有任何已保存的会话)")
        return
    status_label = {
        STATUS_COMPLETED: "已完成(残留)",
        "interrupted": "中断中(可续跑)",
    }
    print(f"{'thread_id':<42} {'状态':<14} {'design':<16} {'停在':<22} step")
    print("-" * 96)
    for info in infos:
        if info.status == STATUS_COMPLETED:
            location = "(已到 END)"
        elif info.current_step:
            location = f"{info.current_phase}/{info.current_step}"
        else:
            location = "-"
        print(
            f"{info.thread_id:<42} {status_label[info.status]:<14} "
            f"{info.design_name or '-':<16} {location:<22} {info.superstep}"
        )


def _print_status(graph, config: dict, state) -> None:
    v = state.values or {}
    tid = config["configurable"]["thread_id"]
    print(f"会话: {tid}")
    if _in_conversation(state):
        print("阶段: 对话中（等待输入）")
    else:
        print(f"阶段: {v.get('current_phase', '-')}/{v.get('current_step', '-')}")
    print(f"design: {v.get('design_name') or '-'}    迭代轮次: {v.get('iteration', 0)}")
    print(f"Setup 违例: {v.get('setup_vio', '-')}    Hold 违例: {v.get('hold_vio', '-')}")
    if v.get("error_msg"):
        print(f"最近错误: {v['error_msg']}")


def _abandon_current(graph, config: dict, state) -> str:
    """离开当前会话：对话阶段断点无保留价值 → 删除；流水线断点 → 保留。

    返回给用户看的说明文字。
    """
    if _in_conversation(state):
        try:
            delete_thread(graph.checkpointer, config["configurable"]["thread_id"])
        except Exception:
            pass
        return ""
    return "当前流水线断点已保留，之后可用 /resume 找回。"


def _cmd_resume(graph, config: dict, state, args: list[str]) -> CommandResult:
    if not args:
        print("用法: /resume <thread_id>（/sessions 可查看所有会话）")
        return CommandResult(action="handled")

    target = args[0]
    current = config["configurable"]["thread_id"]
    if target == current:
        print("已经在这个会话里了。")
        return CommandResult(action="handled")

    snap = graph.get_state({"configurable": {"thread_id": target}})
    status = classify_snapshot(snap)
    if status == STATUS_MISSING:
        print(f"找不到会话 '{target}'，可用 /sessions 查看已有会话。")
        return CommandResult(action="handled")
    if status == STATUS_COMPLETED:
        print(f"会话 '{target}' 已正常结束，没有可续跑的断点，请开新会话（/new）。")
        return CommandResult(action="handled")

    note = _abandon_current(graph, config, state)
    if note:
        print(note)
    print(f"切换到会话: {target}，从断点继续。")
    return CommandResult(action="switch", new_thread_id=target, next_input=None)


def _cmd_new(graph, config: dict, state) -> CommandResult:
    note = _abandon_current(graph, config, state)
    if note:
        print(note)
    fresh = new_thread_id("chat")
    print(f"新会话: {fresh}")
    return CommandResult(action="switch", new_thread_id=fresh, next_input={"messages": []})


def _cmd_run_eco(config: dict, state, args: list[str]) -> CommandResult:
    if not args:
        print("用法: /run_eco <design名>，例如 /run_eco designA")
        return CommandResult(action="handled")
    if not _in_conversation(state):
        print("流水线进行中，不能在此阶段启动新 ECO；先跑完或 /exit 后再试。")
        return CommandResult(action="handled")

    design = " ".join(args)
    return CommandResult(action="resume_text", resume_text=f"帮我跑 {design} 的 ECO")


def dispatch(user_input: str, graph, config: dict, state) -> CommandResult:
    """解析并执行 / 命令。user_input 保证以 / 开头（调用方已判断）。"""
    parts = user_input.split()
    name = parts[0].lower()
    args = parts[1:]

    if name in ("/help", "/?"):
        print(HELP_TEXT)
        return CommandResult(action="handled")
    if name == "/init":
        from src.calling_layer.init_command import run_init
        print(run_init(" ".join(args) if args else ""))
        return CommandResult(action="handled")
    if name == "/status":
        _print_status(graph, config, state)
        return CommandResult(action="handled")
    if name == "/sessions":
        _print_session_list(describe_sessions(graph, graph.checkpointer))
        return CommandResult(action="handled")
    if name == "/resume":
        return _cmd_resume(graph, config, state, args)
    if name == "/new":
        return _cmd_new(graph, config, state)
    if name == "/run_eco":
        return _cmd_run_eco(config, state, args)
    if name in ("/exit", "/quit"):
        if _in_conversation(state):
            return CommandResult(action="quit")
        # 流水线跑到一半：断点保留，事件循环抛 SessionExit 由 main 收尾
        return CommandResult(action="quit_keep")
    print(f"未知命令 '{name}'。{HELP_TEXT}")
    return CommandResult(action="handled")
