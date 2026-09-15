from __future__ import annotations

import uuid

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from src.graph_builder import build_graph
from src.mcp_server.mock import MockECOMCPServer
from src.nodes.node_error_handler import get_retry_step


@pytest.fixture
def config():
    def _cfg(name: str) -> dict:
        return {"configurable": {"thread_id": f"L4_{name}_{uuid.uuid4().hex[:8]}"}}
    return _cfg


def _initial_input():
    return {
        "messages": [HumanMessage(content="帮我跑 designL4 的 ECO")],
        "design_name": "designL4",
    }


def _consume(graph, input_val, cfg):
    list(graph.stream(input_val, cfg))


def _trigger_error(graph, cfg):
    _consume(graph, _initial_input(), cfg)


# ============================================================
# L4-1: Command(resume=...) 正常中断恢复
# ============================================================


class TestCommandResumeRecovery:
    def test_phase2_summary_resume_setup_strategy(self, config):
        mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver(), skip_agent_entry=True)
        cfg = config("resume_setup")

        _consume(g, _initial_input(), cfg)
        _consume(g, Command(resume="setup"), cfg)

        s = g.get_state(cfg)
        assert s.next, "phase3_summary 应再触发 interrupt"
        assert s.values.get("user_fix_strategy") == "setup"
        assert s.values.get("setup_vio") == 30

    def test_phase2_summary_resume_hold_strategy(self, config):
        mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver(), skip_agent_entry=True)
        cfg = config("resume_hold")

        _consume(g, _initial_input(), cfg)
        _consume(g, Command(resume="hold"), cfg)

        s = g.get_state(cfg)
        assert s.next
        assert s.values.get("user_fix_strategy") == "hold"
        assert s.values.get("hold_vio") == 10

    def test_phase3_summary_resume_stop(self, config):
        mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver(), skip_agent_entry=True)
        cfg = config("resume_stop")

        _consume(g, _initial_input(), cfg)
        _consume(g, Command(resume="setup"), cfg)
        _consume(g, Command(resume="stop"), cfg)

        s = g.get_state(cfg)
        assert not s.next, "stop 后应无后续中断"

    def test_phase3_summary_resume_continue_advances_iteration(self, config):
        mcp = MockECOMCPServer(scenario="convergence", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver(), skip_agent_entry=True)
        cfg = config("resume_continue")

        _consume(g, _initial_input(), cfg)
        _consume(g, Command(resume="setup"), cfg)
        _consume(g, Command(resume="continue"), cfg)

        s = g.get_state(cfg).values
        assert s.get("iteration_cnt") == 2


# ============================================================
# L4-2: 断点续跑 —— 同一 thread_id + checkpoint 继续执行
# ============================================================


class TestCheckpointResume:
    def test_memory_saver_separate_threads_isolated(self):
        """同一个 graph fixture，不同 thread_id 应完全独立。"""
        mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver(), skip_agent_entry=True)

        cfg_a = {"configurable": {"thread_id": f"iso_A_{uuid.uuid4().hex[:8]}"}}
        cfg_b = {"configurable": {"thread_id": f"iso_B_{uuid.uuid4().hex[:8]}"}}

        _consume(g, _initial_input(), cfg_a)
        state_a_before_resume = g.get_state(cfg_a).values

        _consume(g, _initial_input(), cfg_b)
        state_b_before_resume = g.get_state(cfg_b).values

        _consume(g, Command(resume="setup"), cfg_a)

        state_a_after = g.get_state(cfg_a).values
        state_b_after = g.get_state(cfg_b).values

        assert state_a_after.get("user_fix_strategy") == "setup"
        assert state_b_after.get("user_fix_strategy") != "setup", (
            f"thread B 不应被 thread A 的 resume 影响: {state_b_after.get('user_fix_strategy')}"
        )
        assert state_a_before_resume.get("design_name") == state_b_before_resume.get("design_name")

    def test_resume_after_checkpoint_continues_from_correct_phase(self, config):
        mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver(), skip_agent_entry=True)
        cfg = config("checkpoint_phase3")

        _consume(g, _initial_input(), cfg)
        _consume(g, Command(resume="setup"), cfg)

        checkpoint_phase3 = g.get_state(cfg).values.get("current_phase")
        assert checkpoint_phase3 == "phase3"

        _consume(g, Command(resume="stop"), cfg)
        final = g.get_state(cfg)
        assert not final.next


# ============================================================
# L4-3: Command(goto=...) retry 路径
# ============================================================


class TestRetryGotoRecovery:
    def test_retry_goto_syntax_executes_target_node(self, config):
        """Command(goto=...) 应跳过 error_handler 的正常路由，直接跳到指定节点执行。"""
        mcp = MockECOMCPServer(scenario="phase1_ext_error", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver(), skip_agent_entry=True)
        cfg = config("goto_ext")

        _consume(g, _initial_input(), cfg)
        state = g.get_state(cfg)
        assert state.next, "Error Handler 应触发 interrupt"
        assert "StarRCX license" in state.values.get("error_msg", "")

        new_step_status = dict(state.values.get("step_status", {}))
        new_step_status["run_ext"] = "pending"
        new_phase_status = dict(state.values.get("phase_status", {}))
        for p, status in new_phase_status.items():
            if status == "error":
                new_phase_status[p] = "pending"

        g.update_state(cfg, {
            "step_status": new_step_status,
            "phase_status": new_phase_status,
            "error_msg": "",
            "current_phase": "",
            "current_step": "",
        })

        retry_events = [
            list(ev.keys())[0]
            for ev in g.stream(Command(goto="run_ext"), cfg)
        ]
        assert "run_ext" in retry_events, f"goto='run_ext' 应执行 run_ext，实际事件: {retry_events}"

        s = g.get_state(cfg)
        assert s.next, "retry 后 Mock Server 仍返回错误 → error_handler 再次触发 interrupt（正确行为）"

    def test_get_retry_step_returns_actual_failed_step(self, config):
        """get_retry_step 辅助函数应返回当前出错 step。"""
        mcp = MockECOMCPServer(scenario="phase2_pv_error", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver(), skip_agent_entry=True)
        cfg = config("goto_pv")

        _consume(g, _initial_input(), cfg)
        state = g.get_state(cfg)

        retry_step = get_retry_step(state.values)
        assert retry_step == "run_pv", f"应返回 run_pv，实际 {retry_step}"

    def test_abort_via_goto_finalize_executes_finalize_node(self, config):
        """Command(goto='finalize') 应直接执行 finalize 节点。"""
        mcp = MockECOMCPServer(scenario="phase2_sta_error", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver(), skip_agent_entry=True)
        cfg = config("abort_finalize")

        _consume(g, _initial_input(), cfg)
        abort_events = [
            list(ev.keys())[0]
            for ev in g.stream(Command(goto="finalize"), cfg)
        ]
        assert "finalize" in abort_events, (
            f"goto='finalize' 应执行 finalize 节点，实际事件: {abort_events}"
        )


# ============================================================
# L4-4: stream(None) 断点续跑 —— LangGraph resume 语义
# ============================================================


class TestStreamNoneResume:
    def test_stream_none_resumes_after_command_resume(self, config):
        """stream(None) 等同于 Command() 的快捷方式，能继续执行下一个 step。"""
        mcp = MockECOMCPServer(scenario="convergence", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver(), skip_agent_entry=True)
        cfg = config("stream_none")

        _consume(g, _initial_input(), cfg)
        _consume(g, Command(resume="setup"), cfg)
        _consume(g, Command(resume="continue"), cfg)

        s = g.get_state(cfg)
        assert s.values.get("iteration_cnt") == 2
        assert s.next, "phase2_summary 应再 interrupt"


# ============================================================
# L4-5: 断点非法输入 → 图内自环重问（不进 error_handler / 不被当 stop）
# ============================================================


def _interrupt_value(state) -> str:
    interrupts = getattr(state, "interrupts", None) or ()
    return str(interrupts[0].value) if interrupts else ""


class TestInvalidInputReprompt:
    def test_phase2_invalid_strategy_reprompts_then_accepts_valid(self, config):
        mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver(), skip_agent_entry=True)
        cfg = config("p2_invalid")

        _consume(g, _initial_input(), cfg)
        assert g.get_state(cfg).next == ("phase2_summary",)

        # 乱码：必须原地重问，不能进 error_handler，也不能走任何 fix 分支
        _consume(g, Command(resume="asdf"), cfg)
        s = g.get_state(cfg)
        assert s.next == ("phase2_summary",)
        assert s.values.get("user_fix_strategy", "") == ""
        assert s.values.get("invalid_input") == "asdf"
        assert "无效输入" in _interrupt_value(s)

        # 空字符串：继续重问
        _consume(g, Command(resume="   "), cfg)
        s = g.get_state(cfg)
        assert s.next == ("phase2_summary",)
        assert s.values.get("invalid_input", "").strip() == ""

        # 合法输入后正常进入 phase3
        _consume(g, Command(resume="setup"), cfg)
        s = g.get_state(cfg)
        assert s.next == ("phase3_summary",)
        assert s.values.get("user_fix_strategy") == "setup"
        assert s.values.get("invalid_input") == ""

    def test_phase2_keyword_case_and_space_tolerant(self, config):
        """只认规范关键词 setup/hold/leakage，大小写与首尾空白不敏感。"""
        for raw, vio_key, expected in [
            ("SETUP", "setup_vio", 30),
            (" hold ", "hold_vio", 10),
        ]:
            mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
            g = build_graph(mcp_server=mcp, checkpointer=MemorySaver(), skip_agent_entry=True)
            cfg = config(f"p2_kw_{raw.strip()}")

            _consume(g, _initial_input(), cfg)
            _consume(g, Command(resume=raw), cfg)
            s = g.get_state(cfg)
            assert s.next == ("phase3_summary",), f"关键词 {raw} 应直接路由到对应修复分支"
            assert s.values.get(vio_key) == expected

    def test_phase2_former_aliases_are_rejected(self, config):
        """数字/中文/缩写不再被接受，必须原地重问。"""
        for raw in ["1", "设置", "s"]:
            mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
            g = build_graph(mcp_server=mcp, checkpointer=MemorySaver(), skip_agent_entry=True)
            cfg = config(f"p2_reject_{raw}")

            _consume(g, _initial_input(), cfg)
            _consume(g, Command(resume=raw), cfg)
            assert g.get_state(cfg).next == ("phase2_summary",), (
                f"{raw} 不应被接受，应停在 phase2_summary 重问"
            )

    def test_phase3_invalid_choice_reprompts_without_finalize(self, config):
        mcp = MockECOMCPServer(scenario="happy_path", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver(), skip_agent_entry=True)
        cfg = config("p3_invalid")

        _consume(g, _initial_input(), cfg)
        _consume(g, Command(resume="setup"), cfg)
        assert g.get_state(cfg).next == ("phase3_summary",)

        # 乱码绝不能被当成 stop：继续挂在 phase3_summary
        _consume(g, Command(resume="asdf"), cfg)
        s = g.get_state(cfg)
        assert s.next == ("phase3_summary",)
        assert s.values.get("user_iter_choice", "") == ""
        assert "无效输入" in _interrupt_value(s)
        # 非法期间不允许写入迭代历史
        assert s.values.get("iteration_history", []) == []

        # 显式 stop 才终结
        _consume(g, Command(resume="stop"), cfg)
        s = g.get_state(cfg)
        assert not s.next
        assert len(s.values.get("iteration_history", [])) == 1

    def test_phase3_invalid_twice_then_continue_keyword(self, config):
        """两次无效输入（含旧别名 c/继续）后，只有 continue 能进入第 2 轮，
        且历史只有 1 条记录。"""
        mcp = MockECOMCPServer(scenario="convergence", simulate_delay=0)
        g = build_graph(mcp_server=mcp, checkpointer=MemorySaver(), skip_agent_entry=True)
        cfg = config("p3_kw_continue")

        _consume(g, _initial_input(), cfg)
        _consume(g, Command(resume="setup"), cfg)

        _consume(g, Command(resume="嗯嗯"), cfg)
        assert g.get_state(cfg).next == ("phase3_summary",)
        _consume(g, Command(resume="继续"), cfg)  # 旧别名，现在应被拒绝
        assert g.get_state(cfg).next == ("phase3_summary",)
        _consume(g, Command(resume="c"), cfg)  # 旧缩写，现在应被拒绝
        assert g.get_state(cfg).next == ("phase3_summary",)

        _consume(g, Command(resume="continue"), cfg)
        s = g.get_state(cfg)
        assert s.values.get("iteration_cnt") == 2
        assert s.next == ("phase2_summary",)
        # 只有校验通过的那一次写入了第 1 轮记录
        history = s.values.get("iteration_history", [])
        assert len(history) == 1
        assert history[0]["iter"] == 1
