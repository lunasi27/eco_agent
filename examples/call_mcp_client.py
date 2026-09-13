#!/usr/bin/env python3
"""
call_mcp_client.py  —  通过 stdio 调用 MCP Server 的 Python 示例

本脚本演示如何用 MCP Python SDK 的 stdio_client 连接本地启动的 MCP Server，
列出 8 个 Tool 并调用 run_sta / run_pv / run_fix_setup。

使用方式：
    1. 先在另一个终端启动 Mock Server：
         ./scripts/start_mock.sh
       或 Real Server（需 csh）：
         ./scripts/start_real.sh

    2. 直接调用本脚本（默认 Mock 配置）：
         python examples/call_mcp_client.py
       或指定 Real（前提 EDA 环境就绪）：
         python examples/call_mcp_client.py --backend real

    3. 也可以通过 subprocess 同时拉起 server + client（无需手动起服务）：
         python examples/call_mcp_client.py --auto
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parent.parent


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="ECO MCP Client 调用示例")
    p.add_argument("--backend", choices=["mock", "real"], default="mock",
                   help="选择后端类型（默认 mock）")
    p.add_argument("--scenario", default="happy_path",
                   help="Mock 模式下的 scenario（默认 happy_path）")
    p.add_argument("--auto", action="store_true",
                   help="由本脚本自动拉起 stdio_runner，无需手动起服务")
    p.add_argument("--design", default="MyDesign",
                   help="design_name 参数值")
    p.add_argument("--run-dir", default="/tmp/eco_runs",
                   help="run_dir 参数值")
    return p


def _build_cmd(args) -> list[str]:
    if args.backend == "real":
        return [sys.executable, "-m", "src.mcp_server.stdio_runner"]
    return [sys.executable, "-m", "src.mcp_server.stdio_runner"]


def _build_env(args) -> dict[str, str]:
    import os
    env = os.environ.copy()
    env["ECO_BACKEND"] = args.backend
    if args.backend == "mock":
        env["ECO_SCENARIO"] = args.scenario
    return env


async def run_inline(args):
    """不启 server，直接在进程内 import 并调用 create_mcp_app + call_tool。"""
    sys.path.insert(0, str(SERVER_ROOT))
    from src.mcp_server.mcp_app import create_mcp_app

    if args.backend == "real":
        from src.mcp_server.real import RealECOMCPServer
        server = RealECOMCPServer()
    else:
        from src.mcp_server.mock import MockECOMCPServer
        server = MockECOMCPServer(scenario=args.scenario)

    mcp = create_mcp_app(server)

    print(f"{'='*60}")
    print(f"  进程内直连模式 — backend={args.backend}  scenario={args.scenario}")
    print(f"{'='*60}\n")

    await _demo_calls(mcp, args)


async def run_stdio(args):
    """子进程拉起 stdio_runner，通过 MCP stdio_client 连接。"""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    cmd = _build_cmd(args)
    env = _build_env(args)

    print(f"{'='*60}")
    print(f"  stdio 子进程模式 — backend={args.backend}  scenario={args.scenario}")
    print(f"  命令: {' '.join(cmd)}")
    print(f"{'='*60}\n")

    params = StdioServerParameters(command=cmd[0], args=cmd[1:], env=env)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print(f"✅ 已连接，共 {len(tools.tools)} 个 tool：")
            for t in tools.tools:
                print(f"   - {t.name}")
            print()

            await _demo_session_calls(session, args)


async def _demo_calls(mcp, args):
    """直接对 FastMCP 对象调 call_tool（async）。"""
    examples = [
        ("run_eco_route",  {"design_name": args.design, "run_dir": args.run_dir}),
        ("run_ext",        {"design_name": args.design, "run_dir": args.run_dir}),
        ("run_sta",        {"design_name": args.design, "run_dir": args.run_dir}),
        ("run_pv",         {"design_name": args.design, "run_dir": args.run_dir}),
        ("run_signoff",    {"design_name": args.design, "run_dir": args.run_dir}),
        ("run_fix_setup",  {"design_name": args.design, "run_dir": args.run_dir,
                            "fix_strategy": "setup_via_repair"}),
        ("run_fix_hold",   {"design_name": args.design, "run_dir": args.run_dir,
                            "fix_strategy": "hold_via_buffer"}),
        ("run_fix_leakage", {"design_name": args.design, "run_dir": args.run_dir,
                             "fix_strategy": "swap_low_vth"}),
    ]
    for name, params in examples:
        print(f"▶ {name}(params={params})")
        try:
            result = await mcp.call_tool(name, params)
            text = result[0].text if hasattr(result, '__iter__') and result else str(result)
            data = json.loads(text)
            print(f"  ✅ {json.dumps(data, indent=2, ensure_ascii=False)}")
        except Exception as exc:
            print(f"  ❌ {exc}")
        print()


async def _demo_session_calls(session, args):
    """通过 MCP ClientSession 调 call_tool（async）。"""
    examples = [
        ("run_sta",       {"design_name": args.design, "run_dir": args.run_dir}),
        ("run_pv",        {"design_name": args.design, "run_dir": args.run_dir}),
        ("run_fix_setup", {"design_name": args.design, "run_dir": args.run_dir,
                           "fix_strategy": "setup_via_repair"}),
    ]
    for name, params in examples:
        print(f"▶ {name}(params={params})")
        try:
            result = await session.call_tool(name, params)
            text = result.content[0].text if result.content else ""
            data = json.loads(text)
            print(f"  ✅ {json.dumps(data, indent=2, ensure_ascii=False)}")
        except Exception as exc:
            print(f"  ❌ {exc}")
        print()


async def main():
    args = _parser().parse_args()
    if args.auto:
        await run_stdio(args)
    else:
        await run_inline(args)


if __name__ == "__main__":
    asyncio.run(main())
