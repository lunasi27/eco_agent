from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


def _last_writer_reducer(existing, updates):
    return updates


def _dict_merge_reducer(existing: dict, updates: dict) -> dict:
    merged = dict(existing) if existing else {}
    for k, v in (updates or {}).items():
        merged[k] = v
    return merged


class ECOState(TypedDict, total=False):
    design_name: Annotated[str, _last_writer_reducer]
    design_dir: Annotated[str, _last_writer_reducer]
    run_dir: Annotated[str, _last_writer_reducer]
    iteration_cnt: Annotated[int, _last_writer_reducer]
    setup_vio: Annotated[int, _last_writer_reducer]
    hold_vio: Annotated[int, _last_writer_reducer]
    pv_pass: Annotated[bool, _last_writer_reducer]
    signoff_pass: Annotated[bool, _last_writer_reducer]
    prev_setup_vio: Annotated[int, _last_writer_reducer]
    prev_hold_vio: Annotated[int, _last_writer_reducer]
    interrupt_msg: Annotated[str, _last_writer_reducer]
    user_fix_strategy: Annotated[str, _last_writer_reducer]
    user_iter_choice: Annotated[str, _last_writer_reducer]
    user_error_choice: Annotated[str, _last_writer_reducer]
    # 最近一次断点非法输入原文；节点据此在自环重问时提示"无效输入：xxx"
    invalid_input: Annotated[str, _last_writer_reducer]
    retry_step: Annotated[str, _last_writer_reducer]
    phase_status: Annotated[dict, _dict_merge_reducer]
    step_status: Annotated[dict, _dict_merge_reducer]
    step_elapsed: Annotated[dict, _dict_merge_reducer]
    iteration_history: Annotated[list, _last_writer_reducer]
    current_phase: Annotated[str, _last_writer_reducer]
    current_step: Annotated[str, _last_writer_reducer]
    error_msg: Annotated[str, _last_writer_reducer]
    awaiting_design: Annotated[bool, _last_writer_reducer]
    messages: Annotated[list[BaseMessage], add_messages]
