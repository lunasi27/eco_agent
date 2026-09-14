from __future__ import annotations

import json as _json
import re
from typing import Callable

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import interrupt

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


GREETING_PROMPT = "你好！我是 ECO Agent，可以帮你执行 ECO 时序修复流水线。\n请输入指令，例如：'帮我跑 designA 的 ECO'"
FOLLOWUP_PROMPT = "你好！我是 ECO Agent，可以帮你执行 ECO 时序修复流水线。\n请输入包含 ECO 关键词的指令，例如：'帮我跑 designA 的 ECO'"
ASK_DESIGN_PROMPT = "请告诉我要跑哪个 design 的 ECO，例如：'帮我跑 designA 的 ECO'"

CHAT_SYSTEM_PROMPT = SystemMessage(content="""你是 ECO Agent，一个 EDA 芯片时序修复助手，运行在终端里。
你唯一的功能是引导用户执行 ECO 时序修复流水线：用户说'帮我跑 xxx 的 ECO'即可启动，
之后可以多轮迭代选择修复策略（setup/hold/leakage）。

用户现在说了一些闲聊或与 ECO 无关的话。请：
1. 用 1~3 句话简短、友好地回应用户（可以回答关于你能力的问题）；
2. 自然地把话题引导回 ECO 功能；
3. 不要编造你不具备的能力，不要输出多余格式。""")


def _parse_user_text(llm_callable, user_text: str) -> tuple[bool, str]:
    # LLM 解析失败（网络/认证等）时降级到规则引擎，不让单轮 API 故障崩掉整个会话
    if llm_callable is not None:
        try:
            return parse_intent_with_llm(llm_callable, user_text)
        except Exception:
            pass
    return is_eco_task(user_text), extract_design_name_rule_based(user_text)


def _llm_chat_reply(llm_callable, history_texts: list[str]) -> str:
    """用 LLM 生成闲聊回复；失败时返回空串（调用方回退到固定提示）。"""
    try:
        recent = [HumanMessage(content=t) for t in history_texts[-6:]]
        response = llm_callable([CHAT_SYSTEM_PROMPT, *recent])
        return getattr(response, "content", "").strip()
    except Exception:
        return ""


def make_agent_entry_node(llm_callable: Callable[[list], object] | None = None):
    def node_agent_entry(state: ECOState) -> dict:
        if state.get("design_name"):
            return {"current_phase": "agent", "current_step": "agent_entry"}

        messages = state.get("messages", [])

        if not messages:
            user_text = interrupt(GREETING_PROMPT)
        else:
            last_msg = messages[-1]
            user_text = getattr(last_msg, "content", str(last_msg))

        user_text = user_text.strip()

        # 注意：一个节点任务内只允许一次 interrupt()。LangGraph 恢复（resume）时任务
        # 从头重放，若同一任务内再次 interrupt，该任务已带有写入记录，会被排除出
        # state.next，事件循环会误判「图已结束」。非 ECO 输入必须走路由进
        # chat_fallback（全新任务）再 interrupt，而不是在本节点内递归再 interrupt。
        is_eco, design_name = _parse_user_text(llm_callable, user_text)
        if is_eco and design_name:
            return {
                "design_name": design_name,
                "current_phase": "agent",
                "current_step": "agent_entry",
                "interrupt_msg": "",
                "awaiting_design": False,
            }

        if is_eco and not design_name:
            # 是 ECO 任务但没说 design：明确追问（无需 LLM 生成）
            return {
                "current_phase": "agent",
                "current_step": "agent_entry",
                "interrupt_msg": ASK_DESIGN_PROMPT,
                "awaiting_design": True,
                "messages": [HumanMessage(content=user_text)],
            }

        # 闲聊：interrupt_msg 留空，由 chat_fallback 用 LLM 生成回复；
        # 无 LLM 时回退到固定提示
        return {
            "current_phase": "agent",
            "current_step": "agent_entry",
            "interrupt_msg": "" if llm_callable is not None else FOLLOWUP_PROMPT,
            "awaiting_design": False,
            "messages": [HumanMessage(content=user_text)],
        }

    return node_agent_entry


def route_from_agent(state: ECOState) -> str:
    design_name = state.get("design_name", "")
    if design_name:
        return "init"
    return "chat_fallback"


def route_from_chat(state: ECOState) -> str:
    """chat_fallback 的自环路由：解析出 design → 进流水线；否则继续对话。"""
    if state.get("design_name", ""):
        return "init"
    return "chat_fallback"


def make_chat_fallback_node(llm_callable: Callable[[list], object] | None = None):
    def node_chat_fallback(state: ECOState) -> dict:
        # 这一轮展示给用户的回复：优先用上一轮留下的 interrupt_msg；
        # 留空说明是闲聊，用 LLM 结合对话历史生成
        msg = state.get("interrupt_msg", "")
        if not msg and llm_callable is not None:
            history = [
                getattr(m, "content", str(m)) for m in state.get("messages", [])
            ]
            if history:
                generated = _llm_chat_reply(llm_callable, history)
                if generated:
                    msg = (
                        f"{generated}\n"
                        "（如需跑 ECO 流水线，请说：'帮我跑 xxx 的 ECO'）"
                    )
        if not msg:
            msg = FOLLOWUP_PROMPT

        user_text = interrupt(msg).strip()

        if not user_text:
            return {
                "current_step": "chat_fallback",
                "interrupt_msg": FOLLOWUP_PROMPT,
                "awaiting_design": False,
            }

        awaiting = state.get("awaiting_design", False)
        is_eco, design_name = _parse_user_text(llm_callable, user_text)
        if awaiting and not design_name:
            # agent 刚追问了 design 名，用户可能直接回一个裸 design 名
            design_name = extract_design_name_rule_based(user_text)

        if design_name and (is_eco or awaiting):
            return {
                "design_name": design_name,
                "current_phase": "agent",
                "current_step": "chat_fallback",
                "interrupt_msg": "",
                "awaiting_design": False,
                "messages": [HumanMessage(content=user_text)],
            }

        if is_eco and not design_name:
            return {
                "current_phase": "agent",
                "current_step": "chat_fallback",
                "interrupt_msg": ASK_DESIGN_PROMPT,
                "awaiting_design": True,
                "messages": [HumanMessage(content=user_text)],
            }

        # 普通闲聊：有 LLM 则下一轮由 LLM 生成回复，无 LLM 用固定提示
        return {
            "current_phase": "agent",
            "current_step": "chat_fallback",
            "interrupt_msg": "" if llm_callable is not None else FOLLOWUP_PROMPT,
            "awaiting_design": False,
            "messages": [HumanMessage(content=user_text)],
        }

    return node_chat_fallback
