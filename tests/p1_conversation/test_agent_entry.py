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
    node_chat_fallback,
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
    def test_eco_message_extracts_design(self):
        node = make_agent_entry_node()
        result = node({
            "messages": [HumanMessage(content="帮我跑 designA 的 ECO")],
        })
        assert result["design_name"] == "designA"
        assert result["current_step"] == "agent_entry"

    def test_non_eco_message_sets_interrupt(self):
        node = make_agent_entry_node()
        result = node({
            "messages": [HumanMessage(content="你好")],
        })
        assert "design_name" not in result
        assert "请输入包含 ECO 关键词的指令" in result["interrupt_msg"]

    def test_design_name_already_passed_skips_parsing(self):
        node = make_agent_entry_node()
        result = node({
            "design_name": "already_set",
            "messages": [HumanMessage(content="跑 ECO")],
        })
        assert result.get("design_name") is None
        assert result["current_step"] == "agent_entry"

    def test_no_messages_prompts_user(self):
        node = make_agent_entry_node()
        result = node({})
        assert "请输入指令" in result["interrupt_msg"]


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

    def test_non_eco_message_stops_at_chat_fallback(self):
        mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver())
        cfg = {"configurable": {"thread_id": "p1_e2e_3"}}

        events = list(g.stream(
            {"messages": [HumanMessage(content="你好，今天怎么样")]},
            cfg,
        ))
        nodes = [list(ev.keys())[0] for ev in events]
        assert nodes == ["agent_entry", "chat_fallback"]
