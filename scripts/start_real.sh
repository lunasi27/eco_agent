#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# start_real.sh  —  Real 模式启动脚本
# 用途：启动 MCP Server 并使用 RealECOMCPServer 作为后端
#       通过 subprocess 调用 scripts/eda/run_step_wrapper.csh 执行 EDA 命令
# 前提：
#   1. 运行环境已安装 csh（csh/tcsh 任一）
#   2. config/config.yaml 中 step_command / base / wait_flags / design_name 已正确配置
# 用法：
#   ./scripts/start_real.sh                                     # 默认 config/config.yaml
#   ./scripts/start_real.sh -c /path/to/my/config.yaml
# ---------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

ECO_CONFIG="${ECO_CONFIG:-config/config.yaml}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --config|-c) ECO_CONFIG="$2"; shift 2 ;;
        --help|-h)
            echo "Usage: $0 [--config PATH]"
            echo ""
            echo "环境变量（可覆盖命令行参数）："
            echo "  ECO_CONFIG   配置文件路径，默认 config/config.yaml"
            echo "  ECO_BACKEND  固定为 real，无需手动设置"
            exit 0 ;;
        *) echo "Unknown option: $1 (use --help)"; exit 1 ;;
    esac
done

# --- 前置检查 ---
if ! command -v csh >/dev/null 2>&1 && ! command -v tcsh >/dev/null 2>&1; then
    echo "❌ 未检测到 csh/tcsh，请先安装 csh 或 tcsh（Real 模式必需）"
    exit 1
fi

if [[ ! -f "${PROJECT_ROOT}/${ECO_CONFIG}" ]]; then
    echo "❌ 配置文件不存在: ${ECO_CONFIG}"
    exit 1
fi

cd "${PROJECT_ROOT}"

export ECO_BACKEND=real
export ECO_CONFIG="${ECO_CONFIG}"

echo "=========================================================="
echo "  ECO MCP Server  —  Real 模式"
echo "----------------------------------------------------------"
echo "  config.yaml  : ${ECO_CONFIG}"
echo "  CWD          : $(pwd)"
echo "  Shell        : $(command -v csh || command -v tcsh)"
echo "  传输层       : stdio"
echo "=========================================================="
echo ""
echo "启动后服务会在 stdin/stdout 上等待 JSON-RPC 请求。"
echo "按 Ctrl+C 停止。"
echo ""

exec python -m src.mcp_server.stdio_runner
