#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# start_mock.sh  —  Mock 模式启动脚本
# 用途：启动 MCP Server 并使用 MockECOMCPServer 作为后端
#       不依赖任何 EDA 工具，适合开发调试 / MCP Inspector 联调
# 用法：
#   ./scripts/start_mock.sh                       # 默认 happy_path scenario
#   ./scripts/start_mock.sh --scenario convergence # 指定 scenario
#   ./scripts/start_mock.sh --scenario phase2_sta_error
#   ./scripts/start_mock.sh --delay 0.0           # 关闭模拟延迟
#   ./scripts/start_mock.sh --scenario happy_path --delay 0.0
# ---------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

SCENARIO="${ECO_SCENARIO:-happy_path}"
DELAY="${ECO_DELAY:-0.3}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --scenario|-s) SCENARIO="$2"; shift 2 ;;
        --delay|-d)    DELAY="$2";    shift 2 ;;
        --help|-h)
            echo "Usage: $0 [--scenario NAME] [--delay SECONDS]"
            echo ""
            echo "Available scenarios:"
            echo "  happy_path             全流程通过（默认）"
            echo "  convergence            结果收敛到接近 signoff"
            echo "  phase1_ext_error       抽取阶段报错"
            echo "  phase2_sta_error       STA 阶段超时"
            echo "  phase2_pv_error        PV LVS mismatch"
            echo "  phase2_signoff_error   Signoff DRC 失败"
            echo "  phase3_fix_error       ECO Fix 无法进一步收敛"
            exit 0 ;;
        *) echo "Unknown option: $1 (use --help)"; exit 1 ;;
    esac
done

cd "${PROJECT_ROOT}"

export ECO_BACKEND=mock
export ECO_SCENARIO="${SCENARIO}"
export ECO_DELAY="${DELAY}"

echo "=========================================================="
echo "  ECO MCP Server  —  Mock 模式"
echo "----------------------------------------------------------"
echo "  Scenario  : ${ECO_SCENARIO}"
echo "  Sim delay : ${ECO_DELAY}s"
echo "  CWD       : $(pwd)"
echo "  传输层    : stdio"
echo "=========================================================="
echo ""
echo "启动后服务会在 stdin/stdout 上等待 JSON-RPC 请求。"
echo "可配合 MCP Inspector 或 examples/call_mcp_client.py 调用。"
echo "按 Ctrl+C 停止。"
echo ""

exec python -m src.mcp_server.stdio_runner
