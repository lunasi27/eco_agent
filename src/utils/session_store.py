"""Checkpoint session（thread）的生命周期管理。

LangGraph 用 ``thread_id`` 标识一次会话，checkpointer 会把每个 superstep
的图状态持久化下来。框架本身不提供 TTL / 自动消亡，本模块补齐三件事：

- ``list_thread_ids`` / ``describe_sessions``：枚举所有会话
- ``classify_snapshot``：区分「不存在 / 中断中 / 已完成」
- ``delete_thread``：会话正常结束后清理 checkpoint
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver

STATUS_INTERRUPTED = "interrupted"
STATUS_COMPLETED = "completed"
STATUS_MISSING = "missing"


@dataclass
class SessionInfo:
    thread_id: str
    status: str  # interrupted | completed
    design_name: str = ""
    current_phase: str = ""
    current_step: str = ""
    superstep: int = 0


def classify_snapshot(state) -> str:
    """根据 StateSnapshot 判断会话状态。

    - ``missing``     ：checkpoint 中不存在该 thread
    - ``interrupted`` ：停在某个 interrupt() 上（可续跑）
    - ``completed``   ：已走到 END（残留的完成态快照）

    判断中断不能只看 state.next：恢复过的任务会带写入记录而被排除出
    state.next，须同时检查 tasks 中的挂起中断。
    """
    if state is None or state.metadata is None or not state.values:
        return STATUS_MISSING
    if state.next or any(
        getattr(t, "interrupt", None) is not None for t in (state.tasks or ())
    ):
        return STATUS_INTERRUPTED
    return STATUS_COMPLETED


def list_thread_ids(checkpointer: "BaseCheckpointSaver") -> list[str]:
    """枚举 checkpointer 中所有去重后的 thread_id（按最近活跃顺序）。"""
    tids: list[str] = []
    for tup in checkpointer.list(None):
        tid = (tup.config or {}).get("configurable", {}).get("thread_id")
        if tid and tid not in tids:
            tids.append(tid)
    return tids


def describe_sessions(graph, checkpointer: "BaseCheckpointSaver") -> list[SessionInfo]:
    """列出所有会话及其分类信息，中断中的会话排前面。"""
    infos: list[SessionInfo] = []
    for tid in list_thread_ids(checkpointer):
        snap = graph.get_state({"configurable": {"thread_id": tid}})
        status = classify_snapshot(snap)
        if status == STATUS_MISSING:
            continue
        values = snap.values or {}
        infos.append(
            SessionInfo(
                thread_id=tid,
                status=status,
                design_name=values.get("design_name", ""),
                current_phase=values.get("current_phase", ""),
                current_step=values.get("current_step", ""),
                superstep=(snap.metadata or {}).get("step", 0),
            )
        )
    infos.sort(key=lambda x: (x.status != STATUS_INTERRUPTED, -x.superstep))
    return infos


def delete_thread(checkpointer: "BaseCheckpointSaver", thread_id: str) -> None:
    """删除某个 thread 的全部 checkpoint（SqliteSaver / InMemorySaver 均支持）。"""
    checkpointer.delete_thread(thread_id)
