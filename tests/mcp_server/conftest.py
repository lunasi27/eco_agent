"""mcp_server 测试公共夹具。"""

from __future__ import annotations

import pytest

from .helpers import WRAPPER_PATH, write_real_configs


@pytest.fixture
def make_server(tmp_path):
    """工厂：(step_command, **kw) -> RealECOMCPServer（wrapper 指向真实脚本）。"""
    from src.mcp_server.real import RealECOMCPServer

    def _make(step_command: dict, **kwargs) -> RealECOMCPServer:
        project_path, run_context_path = write_real_configs(
            tmp_path, step_command=step_command, **kwargs,
        )
        return RealECOMCPServer(
            project_path=str(project_path),
            run_context_path=str(run_context_path),
            wrapper_path=str(WRAPPER_PATH),
        )

    return _make
