from __future__ import annotations

import json as _json
import re
from typing import Callable

from langchain_core.messages import HumanMessage, SystemMessage

from src.state import ECOState


ECO_KEYWORDS = (
    "eco",
    "跑",
    "修复",
    "时序",
    "setup",
    "hold",
    "违例",
    "迭代",
)

LLM_SYSTEM_PROMPT = SystemMessage(content="""你是 ECO Agent 的意图解析器。根据用户输入，判断是否是 ECO 流水线任务。
ECO 任务示例: '帮我跑 designA 的 ECO', '对 design_top 做时序修复', '执行 ECO 迭代'
非 ECO 任务示例: '你好', '今天天气怎么样'

如果是 ECO 任务，请提取 design_name（设计名）。design_name 是一个简短的标识符，
通常是大写字母开头或数字开头的短字符串，如 designA、design_top、dma_top 等。

严格按以下 JSON 格式输出，不要输出任何其他内容:
{"is_eco": true, "design_name": "designA"}
或者
{"is_eco": false, "design_name": ""}""")


def is_eco_task(text: str) -> bool:
    if not text:
        return False
    lower = text.lower()
    for kw in ECO_KEYWORDS:
        if kw.lower() in lower:
            return True
    return False


_DESIGN_NAME_PATTERNS = [
    re.compile(r"跑[_\s]*([A-Za-z]\w*)[_\s]*的?[_\s]*ECO", re.IGNORECASE),
    re.compile(r"对[_\s]*([A-Za-z]\w*)[_\s]*做", re.IGNORECASE),
    re.compile(r"([A-Z][A-Za-z0-9_]*)\s*(?:的?\s*ECO|ECO)", re.IGNORECASE),
    re.compile(r"(design|top|module|block)([A-Z]\w*|[_\-]\w+)", re.IGNORECASE),
]


def extract_design_name_rule_based(text: str) -> str:
    if not text:
        return ""
    for i, pat in enumerate(_DESIGN_NAME_PATTERNS):
        m = pat.search(text)
        if m:
            if i == 3:
                return m.group(1) + m.group(2)
            return m.group(1)
    parts = re.split(r"[\s,，。!！?？、]|的|和|与|对", text)
    for p in parts:
        p = p.strip()
        if 2 <= len(p) <= 32 and re.match(r"^[A-Za-z]\w*$", p):
            lower = p.lower()
            if lower in {k.lower() for k in ECO_KEYWORDS}:
                continue
            return p
    return ""


def parse_intent_with_llm(
    llm_callable: Callable[[list], object],
    text: str,
) -> tuple[bool, str]:
    messages = [LLM_SYSTEM_PROMPT, HumanMessage(content=text)]
    response = llm_callable(messages)
    content = getattr(response, "content", str(response))

    m = re.search(r"\{[^}]*\}", content, re.DOTALL)
    if not m:
        return is_eco_task(text), extract_design_name_rule_based(text)

    try:
        data = _json.loads(m.group(0))
        return bool(data.get("is_eco", False)), str(data.get("design_name", "")).strip()
    except (_json.JSONDecodeError, TypeError):
        return is_eco_task(text), extract_design_name_rule_based(text)


def make_agent_entry_node(llm_callable: Callable[[list], object] | None = None):
    def node_agent_entry(state: ECOState) -> dict:
        if state.get("design_name"):
            return {"current_phase": "agent", "current_step": "agent_entry"}

        messages = state.get("messages", [])
        if not messages:
            return {
                "current_phase": "agent",
                "current_step": "agent_entry",
                "interrupt_msg": "请输入指令（例如：'帮我跑 designA 的 ECO'）",
            }

        last_msg = messages[-1]
        content = getattr(last_msg, "content", str(last_msg))

        if llm_callable is not None:
            is_eco, design_name = parse_intent_with_llm(llm_callable, content)
        else:
            is_eco = is_eco_task(content)
            design_name = extract_design_name_rule_based(content)

        if not is_eco:
            return {
                "current_phase": "agent",
                "current_step": "agent_entry",
                "interrupt_msg": (
                    "你好！我是 ECO Agent，可以帮你执行 ECO 时序修复流水线。\n"
                    "请输入包含 ECO 关键词的指令，例如：'帮我跑 designA 的 ECO'"
                ),
            }

        if not design_name:
            return {
                "current_phase": "agent",
                "current_step": "agent_entry",
                "interrupt_msg": (
                    "请告诉我要跑哪个 design 的 ECO，例如：'帮我跑 designA 的 ECO'"
                ),
            }

        return {
            "design_name": design_name,
            "current_phase": "agent",
            "current_step": "agent_entry",
            "interrupt_msg": "",
        }

    return node_agent_entry


def route_from_agent(state: ECOState) -> str:
    design_name = state.get("design_name", "")
    if design_name:
        return "init"
    return "chat_fallback"


def node_chat_fallback(state: ECOState) -> dict:
    msg = state.get("interrupt_msg", "") or (
        "你好！我是 ECO Agent，可以帮你执行 ECO 时序修复流水线。\n"
        "请输入包含 ECO 关键词的指令，例如：'帮我跑 designA 的 ECO'"
    )
    return {"interrupt_msg": msg}
