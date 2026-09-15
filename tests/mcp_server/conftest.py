"""mcp_server 测试公共夹具。"""

from __future__ import annotations

import pytest

from .helpers import WRAPPER_PATH, write_config


@pytest.fixture
def make_server(tmp_path):
    """工厂：(step_command, **kw) -> RealECOMCPServer（wrapper 指向真实脚本）。"""
    from src.mcp_server.real import RealECOMCPServer

    def _make(step_command: dict, **kwargs) -> RealECOMCPServer:
        config_path = write_config(tmp_path, step_command=step_command, **kwargs)
        return RealECOMCPServer(
            config_path=str(config_path),
            wrapper_path=str(WRAPPER_PATH),
        )

    return _make
