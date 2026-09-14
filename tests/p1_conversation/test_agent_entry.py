from __future__ import annotations

import json as _json
import re

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from src.conversation.agent import (
    extract_design_name_rule_based,
    is_eco_task,
    make_agent_entry_node,
    make_chat_fallback_node,
    parse_intent_with_llm,
    route_from_agent,
)
from src.graph_builder import build_graph
from src.mcp_server.mock import MockECOMCPServer


class TestRuleBasedIntent:
    def test_eco_keyword_detection(self):
        assert is_eco_task("帮我跑 ECO")
        assert is_eco_task("修复时序违例")
        assert is_eco_task("setup 收敛")
        assert is_eco_task("做 ECO 迭代")
        assert not is_eco_task("你好")
        assert not is_eco_task("今天天气怎么样")
        assert not is_eco_task("")

    def test_design_name_extraction_patterns(self):
        assert extract_design_name_rule_based("帮我跑 designA 的 ECO") == "designA"
        assert extract_design_name_rule_based("对 design_top 做时序修复") == "design_top"
        assert extract_design_name_rule_based("跑 DMA_TOP ECO") == "DMA_TOP"
        assert extract_design_name_rule_based("对 my_block 做 setup 修复") == "my_block"

    def test_no_design_name_in_generic_query(self):
        assert extract_design_name_rule_based("你好") == ""
        assert extract_design_name_rule_based("执行 ECO 迭代") == ""
        assert extract_design_name_rule_based("") == ""


class TestLLMIntentParser:
    def test_llm_json_success(self):
        def fake_llm(msgs):
            class FakeResp:
                content = '{"is_eco": true, "design_name": "designA"}'
            return FakeResp()

        is_eco, design = parse_intent_with_llm(fake_llm, "帮我跑 designA 的 ECO")
        assert is_eco is True
        assert design == "designA"

    def test_llm_non_eco(self):
        def fake_llm(msgs):
            class FakeResp:
                content = '{"is_eco": false, "design_name": ""}'
            return FakeResp()

        is_eco, design = parse_intent_with_llm(fake_llm, "你好")
        assert is_eco is False

    def test_llm_garbage_falls_back_to_rule_based(self):
        def fake_llm(msgs):
            class FakeResp:
                content = "我是个无法解析的回复"
            return FakeResp()

        is_eco, design = parse_intent_with_llm(fake_llm, "帮我跑 designA 的 ECO")
        assert is_eco is True
        assert design == "designA"


class TestAgentEntryNode:
    def test_design_name_already_passed_skips_parsing(self):
        node = make_agent_entry_node()
        result = node({
            "design_name": "already_set",
            "messages": [HumanMessage(content="跑 ECO")],
        })
        assert result.get("design_name") is None
        assert result["current_step"] == "agent_entry"

    def test_eco_message_with_design_extracts_it(self):
        node = make_agent_entry_node()
        result = node({
            "messages": [HumanMessage(content="帮我跑 designA 的 ECO")],
        })
        assert result["design_name"] == "designA"
        assert result["current_step"] == "agent_entry"


class TestRouteFromAgent:
    def test_with_design_routes_to_init(self):
        assert route_from_agent({"design_name": "d"}) == "init"

    def test_without_design_routes_to_chat(self):
        assert route_from_agent({}) == "chat_fallback"
        assert route_from_agent({"interrupt_msg": "hi"}) == "chat_fallback"


class TestAgentEntryEndToEnd:
    def test_full_pipeline_from_natural_language(self):
        mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
        cfg = {"configurable": {"thread_id": "p1_e2e_1"}}

        events = list(g.stream(
            {"messages": [HumanMessage(content="帮我跑 designA 的 ECO")]},
            cfg,
        ))
        nodes = [list(ev.keys())[0] for ev in events]
        assert nodes[0] == "agent_entry"
        assert "init" in nodes
        assert "run_eco_route" in nodes

        s = g.get_state(cfg)
        assert s.values["design_name"] == "designA"
        assert s.next, "应该停在 phase2_summary interrupt"

    def test_explicit_design_name_bypasses_agent_parsing(self):
        mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
        cfg = {"configurable": {"thread_id": "p1_e2e_2"}}

        events = list(g.stream(
            {"design_name": "my_design", "messages": [HumanMessage(content="跑 ECO")]},
            cfg,
        ))
        nodes = [list(ev.keys())[0] for ev in events]
        assert nodes[0] == "agent_entry"
        s = g.get_state(cfg)
        assert s.values["design_name"] == "my_design"

    def test_non_eco_message_interrupts_in_agent_entry(self):
        mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
        cfg = {"configurable": {"thread_id": "p1_e2e_3"}}

        events = list(g.stream(
            {"messages": [HumanMessage(content="你好，今天怎么样")]},
            cfg,
        ))
        keys = [list(ev.keys())[0] for ev in events]
        assert "__interrupt__" in keys

        s = g.get_state(cfg)
        assert s.next, "agent_entry 内 interrupt 了非 ECO 消息"

        events2 = list(g.stream(Command(resume="帮我跑 designA 的 ECO"), cfg))
        keys2 = [list(ev.keys())[0] for ev in events2]
        assert "init" in keys2

        s2 = g.get_state(cfg)
        assert s2.values["design_name"] == "designA"

    def test_multi_turn_conversation_loops_until_eco_task(self):
        """回归：闲聊后图必须继续等输入（chat_fallback 自环），不能误判会话结束。

        背景：旧实现非 ECO 时在同一节点任务内递归再 interrupt，LangGraph 恢复
        重放后该任务带写入记录被排除出 state.next，事件循环误判图已结束。
        """
        mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
        cfg = {"configurable": {"thread_id": "p1_e2e_multi"}}

        # 第1轮：打招呼 interrupt（agent_entry 全新任务）
        list(g.stream({"messages": []}, cfg))
        s1 = g.get_state(cfg)
        assert s1.next == ("agent_entry",)

        # 第2轮：闲聊 → 必须落到 chat_fallback 的全新任务上继续等输入
        list(g.stream(Command(resume="你好"), cfg))
        s2 = g.get_state(cfg)
        assert s2.next == ("chat_fallback",), "闲聊后必须继续等输入，不能结束会话"

        # 第3轮：继续闲聊 → chat_fallback 自环，仍然是全新任务
        list(g.stream(Command(resume="今天天气怎么样"), cfg))
        s3 = g.get_state(cfg)
        assert s3.next == ("chat_fallback",), "自环后仍应继续等输入"

        # 第4轮：给出 ECO 指令 → 进流水线
        list(g.stream(Command(resume="帮我跑 designA 的 ECO"), cfg))
        s4 = g.get_state(cfg)
        assert s4.values["design_name"] == "designA"
        assert s4.next, "流水线应继续运行或停在流水线中断点"


def _collect_interrupt_values(events) -> list[str]:
    return [
        ev["__interrupt__"][0].value
        for ev in events
        if "__interrupt__" in ev
    ]


class TestLLMChitchatReply:
    def test_chitchat_gets_llm_generated_reply(self):
        """闲聊时 chat_fallback 应调用 LLM 生成回复，而不是固定文案"""

        def fake_llm(msgs):
            class FakeResp:
                content = "我是 ECO 助手，负责时序修复流水线，暂时聊不了别的~"
            return FakeResp()

        mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver(), llm_callable=fake_llm)
        cfg = {"configurable": {"thread_id": "p1_llm_chat"}}

        list(g.stream({"messages": []}, cfg))  # 打招呼
        events = list(g.stream(Command(resume="你好"), cfg))

        values = _collect_interrupt_values(events)
        assert values, "闲聊后应有下一轮提示"
        assert "我是 ECO 助手" in values[0], "回复应来自 LLM 生成，而非固定文案"

        # LLM 闲聊回复后仍应继续等待输入（自环）
        s = g.get_state(cfg)
        assert s.next == ("chat_fallback",)

    def test_llm_chat_failure_falls_back_to_canned_prompt(self):
        """LLM 生成闲聊回复失败时回退到固定提示，会话不中断"""

        def broken_llm(msgs):
            raise RuntimeError("api down")

        mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver(), llm_callable=broken_llm)
        cfg = {"configurable": {"thread_id": "p1_llm_fail"}}

        list(g.stream({"messages": []}, cfg))
        events = list(g.stream(Command(resume="你好"), cfg))

        values = _collect_interrupt_values(events)
        assert values
        assert "请输入包含 ECO 关键词的指令" in values[0], "应回退到固定提示"


class TestAwaitingDesignFlow:
    def test_bare_design_name_accepted_after_ask(self):
        """agent 追问 design 名后，用户直接回裸 design 名也应被接受"""
        mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
        cfg = {"configurable": {"thread_id": "p1_await_design"}}

        # '跑 ECO' 有 ECO 关键词但无 design 名 → 追问 design
        list(g.stream({"messages": [HumanMessage(content="跑 ECO")]}, cfg))
        s = g.get_state(cfg)
        assert s.values.get("awaiting_design") is True

        # 直接回答裸 design 名 → 进流水线
        events = list(g.stream(Command(resume="designA"), cfg))
        keys = [list(ev.keys())[0] for ev in events]
        assert "init" in keys
        s2 = g.get_state(cfg)
        assert s2.values["design_name"] == "designA"
