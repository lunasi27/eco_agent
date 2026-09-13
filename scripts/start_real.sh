#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# start_real.sh  —  Real 模式启动脚本
# 用途：启动 MCP Server 并使用 RealECOMCPServer 作为后端
#       通过 subprocess 调用 scripts/eda/*.csh 脚本，注入环境变量
# 前提：
#   1. 运行环境已安装 csh（csh/tcsh 任一）
#   2. config/eda_tools.yaml 中 EDA 路径/工具链已正确配置
#   3. scripts/eda/*.csh 脚本已对应真实 EDA 命令
# 用法：
#   ./scripts/start_real.sh                                 # 默认 config/eda_tools.yaml
#   ./scripts/start_real.sh --config config/production.yaml # 指定配置
#   ./scripts/start_real.sh --timeout 1200                 # 覆盖默认超时
# ---------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

CONFIG="${ECO_CONFIG:-config/eda_tools.yaml}"
TIMEOUT="${ECO_TIMEOUT:-}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --config|-c) CONFIG="$2"; shift 2 ;;
        --timeout|-t) TIMEOUT="$2"; shift 2 ;;
        --help|-h)
            echo "Usage: $0 [--config PATH] [--timeout SECONDS]"
            echo ""
            echo "环境变量（可覆盖 --config / --timeout）："
            echo "  ECO_CONFIG    配置文件路径，默认 config/eda_tools.yaml"
            echo "  ECO_TIMEOUT   subprocess 全局超时（秒），仅当设置时生效"
            echo "  ECO_BACKEND   固定为 real，无需手动设置"
            exit 0 ;;
        *) echo "Unknown option: $1 (use --help)"; exit 1 ;;
    esac
done

# --- 前置检查 ---
if ! command -v csh >/dev/null 2>&1 && ! command -v tcsh >/dev/null 2>&1; then
    echo "❌ 未检测到 csh/tcsh，请先安装 csh 或 tcsh（Real 模式必需）"
    exit 1
fi

CONFIG_PATH="${PROJECT_ROOT}/${CONFIG}"
if [[ ! -f "${CONFIG_PATH}" ]]; then
    echo "❌ 配置文件不存在: ${CONFIG_PATH}"
    exit 1
fi

cd "${PROJECT_ROOT}"

export ECO_BACKEND=real
export ECO_CONFIG="${CONFIG}"
[[ -n "${TIMEOUT}" ]] && export ECO_TIMEOUT="${TIMEOUT}"

echo "=========================================================="
echo "  ECO MCP Server  —  Real 模式"
echo "----------------------------------------------------------"
echo "  配置文件  : ${CONFIG}"
echo "  解析路径  : ${CONFIG_PATH}"
echo "  CWD       : $(pwd)"
echo "  Shell     : $(command -v csh || command -v tcsh)"
echo "  传输层    : stdio"
[[ -n "${TIMEOUT}" ]] && echo "  全局超时  : ${TIMEOUT}s（覆盖配置）"
echo "=========================================================="
echo ""
echo "启动后服务会在 stdin/stdout 上等待 JSON-RPC 请求。"
echo "按 Ctrl+C 停止。"
echo ""

exec python -m src.mcp_server.stdio_runner
