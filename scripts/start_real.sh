#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# start_real.sh  —  Real 模式启动脚本
# 用途：启动 MCP Server 并使用 RealECOMCPServer 作为后端
#       通过 subprocess 调用 scripts/eda/run_step_wrapper.csh 执行 EDA 命令
# 前提：
#   1. 运行环境已安装 csh（csh/tcsh 任一）
#   2. config/project.yaml 中 step_command / base / wait_flags 已正确配置
#   3. config/run_context.yaml 中本轮输入输出已更新
# 用法：
#   ./scripts/start_real.sh                                            # 默认两个 yaml
#   ./scripts/start_real.sh -p config/project_a.yaml -c config/rc.yaml
# ---------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

PROJECT_CONFIG="${PROJECT_CONFIG:-config/project.yaml}"
RUN_CONTEXT="${RUN_CONTEXT:-config/run_context.yaml}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --project-config|-p) PROJECT_CONFIG="$2"; shift 2 ;;
        --run-context|-c)    RUN_CONTEXT="$2";     shift 2 ;;
        --help|-h)
            echo "Usage: $0 [--project-config PATH] [--run-context PATH]"
            echo ""
            echo "环境变量（可覆盖命令行参数）："
            echo "  PROJECT_CONFIG  项目级配置，默认 config/project.yaml"
            echo "  RUN_CONTEXT     运行级配置，默认 config/run_context.yaml"
            echo "  ECO_BACKEND     固定为 real，无需手动设置"
            exit 0 ;;
        *) echo "Unknown option: $1 (use --help)"; exit 1 ;;
    esac
done

# --- 前置检查 ---
if ! command -v csh >/dev/null 2>&1 && ! command -v tcsh >/dev/null 2>&1; then
    echo "❌ 未检测到 csh/tcsh，请先安装 csh 或 tcsh（Real 模式必需）"
    exit 1
fi

for cfg in "${PROJECT_CONFIG}" "${RUN_CONTEXT}"; do
    if [[ ! -f "${PROJECT_ROOT}/${cfg}" ]]; then
        echo "❌ 配置文件不存在: ${cfg}"
        exit 1
    fi
done

cd "${PROJECT_ROOT}"

export ECO_BACKEND=real
export PROJECT_CONFIG="${PROJECT_CONFIG}"
export RUN_CONTEXT="${RUN_CONTEXT}"

echo "=========================================================="
echo "  ECO MCP Server  —  Real 模式"
echo "----------------------------------------------------------"
echo "  project.yaml   : ${PROJECT_CONFIG}"
echo "  run_context    : ${RUN_CONTEXT}"
echo "  CWD            : $(pwd)"
echo "  Shell          : $(command -v csh || command -v tcsh)"
echo "  传输层         : stdio"
echo "=========================================================="
echo ""
echo "启动后服务会在 stdin/stdout 上等待 JSON-RPC 请求。"
echo "按 Ctrl+C 停止。"
echo ""

exec python -m src.mcp_server.stdio_runner
