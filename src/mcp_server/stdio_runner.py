from __future__ import annotations

import asyncio
import os

from src.mcp_server import create_mcp_app


async def main():
    backend = os.environ.get("ECO_BACKEND", "mock")

    if backend == "real":
        from src.mcp_server.real import RealECOMCPServer
        eco_server = RealECOMCPServer(
            project_path=os.environ.get("PROJECT_CONFIG", "config/project.yaml"),
            run_context_path=os.environ.get("RUN_CONTEXT", "config/run_context.yaml"),
        )
    else:
        from src.mcp_server.mock import MockECOMCPServer
        scenario = os.environ.get("ECO_SCENARIO", "happy_path")
        delay = float(os.environ.get("ECO_DELAY", "0.3"))
        eco_server = MockECOMCPServer(scenario=scenario, simulate_delay=delay)

    mcp = create_mcp_app(eco_server)
    await mcp.run_stdio()


if __name__ == "__main__":
    asyncio.run(main())
