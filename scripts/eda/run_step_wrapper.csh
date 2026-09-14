#!/bin/csh -f
# ================================================================================
# 通用 step 执行 wrapper —— 所有 10 个 step 共用
# ================================================================================
#
# Python 侧通过环境变量传入所有配置（必需的缺了就报错）：
#   PROJECT_CSHRC    → source IT 环境（PATH、EDA_HOME、license 等）
#   STEP_NAME        → 当前 step 名（run_sta / run_pt_fix_setup 等）
#   DESIGN_NAME      → 设计名（SOC_XXX_SUB 等）
#   EXECUTION_DIR    → cd 到这个目录执行命令
#   WAIT_FILE        → pds_wait_files 检查的完成标志文件
#   CMD              → 要执行的完整命令（Python resolve 占位符后传入）
#   LOG_DIR          → 日志存储目录（从 project.yaml.logs 来）
#
# 可选环境变量：
#   DEBUG=1          → 打印所有执行细节（set -x + env dump）
#   STEP_SCRIPTS_DIR → post_check 钩子目录（默认 scripts/eda）
#
# ================================================================================

# ── Debug 模式开关 ──
if ($?DEBUG && "$DEBUG" == "1") then
    set -x
    echo ""
    echo "========================================"
    echo "  DEBUG MODE ON - Environment Dump"
    echo "========================================"
    echo "  PROJECT_CSHRC = $PROJECT_CSHRC"
    echo "  STEP_NAME     = $STEP_NAME"
    echo "  DESIGN_NAME   = $DESIGN_NAME"
    echo "  EXECUTION_DIR = $EXECUTION_DIR"
    echo "  WAIT_FILE     = $WAIT_FILE"
    echo "  CMD           = $CMD"
    echo "  LOG_DIR       = $LOG_DIR"
    echo "========================================"
    echo ""
endif

# ── 必需环境变量校验 ──
# （CMD 缺失时后面 eval 会挂，但提前报错信息更清晰）
if (! $?STEP_NAME || "$STEP_NAME" == "") then
    echo "ERROR: STEP_NAME environment variable not set"
    exit 1
endif
if (! $?CMD || "$CMD" == "") then
    echo "ERROR: CMD environment variable not set for step [$STEP_NAME]"
    exit 1
endif

# ── 1. Source IT 环境 ──
# IT 托底的 EDA 环境，所有脚本共用同一个 cshrc
if ($?PROJECT_CSHRC && -f "$PROJECT_CSHRC") then
    source "$PROJECT_CSHRC"
else
    echo "WARNING: [$STEP_NAME] PROJECT_CSHRC not found or not set: $PROJECT_CSHRC"
endif

# ── 2. Cd 到执行目录 ──
# 每个 step 可能不同，Python 从 project.yaml.base 取了 EXECUTION_DIR 传入
if ($?EXECUTION_DIR && -d "$EXECUTION_DIR") then
    cd "$EXECUTION_DIR"
    if ($?DEBUG && "$DEBUG" == "1") then
        echo "[DEBUG] cd to: $(pwd)"
    endif
else
    # 允许 EXECUTION_DIR 不存在时 fallback 到当前目录
    echo "WARNING: [$STEP_NAME] EXECUTION_DIR not found or not set: $EXECUTION_DIR"
    echo "  Falling back to current directory: $(pwd)"
endif

# ── 3. 开始执行日志 ──
set timestamp = `date +%Y%m%d_%H%M%S`
set step_label = "[$STEP_NAME]"
echo ""
echo "========================================"
echo "  $step_label STARTING  ($timestamp)"
echo "  Design:   $DESIGN_NAME"
echo "  CMD:      $CMD"
echo "  Dir:      $(pwd)"
echo "========================================"

# ── 4. 执行命令 ──
# 用 eval 因为 CMD 可能包含 shell 语法（source、管道、重定向、变量展开等）
# 同时输出到终端（tee -a）和日志文件（如果 LOG_DIR 设置了）
set cmd_status = 0

if ($?LOG_DIR && -d "$LOG_DIR") then
    # LOG_DIR 存在 → 同时写终端 + 日志文件
    set log_file = "${LOG_DIR}/${STEP_NAME}_${timestamp}.log"
    echo "$step_label LOG FILE: $log_file"
    echo "========================================" | tee "$log_file"
    echo "  $step_label STARTING  ($timestamp)" | tee -a "$log_file"
    echo "  Design:   $DESIGN_NAME" | tee -a "$log_file"
    echo "  CMD:      $CMD" | tee -a "$log_file"
    echo "  Dir:      $(pwd)" | tee -a "$log_file"
    echo "========================================" | tee -a "$log_file"
    echo "" | tee -a "$log_file"

    eval "$CMD" 2>&1 | tee -a "$log_file"
    set cmd_status = $status
else
    # LOG_DIR 不存在 → 只输出到终端
    eval "$CMD"
    set cmd_status = $status
endif

# ── 5. 命令失败直接退出 ──
if ($cmd_status != 0) then
    echo ""
    echo "========================================"
    echo "  ERROR: $step_label CMD FAILED"
    echo "  Exit code: $cmd_status"
    echo "  CMD: $CMD"
    if ($?LOG_DIR && -d "$LOG_DIR") then
        echo "  Full log: $log_file"
    endif
    echo "========================================"
    exit $cmd_status
endif

# ── 6. PDS wait 检查 ──
# WAIT_FILE 是完成标志文件，PDS 系统提供的 pds_wait_files 函数检查
# 没配 WAIT_FILE 的 step 跳过（比如纯 pds_innovus 命令内部已经托底了）
if ($?WAIT_FILE && "$WAIT_FILE" != "") then
    echo ""
    echo "$step_label Checking wait file: $WAIT_FILE"
    pds_wait_files "$WAIT_FILE"
    if ($status != 0) then
        echo "========================================"
        echo "  ERROR: $step_label pds_wait_files FAILED"
        echo "  Wait file: $WAIT_FILE"
        echo "  可能原因：脚本没跑完、输出目录不存在、或者工具内部异常"
        if ($?LOG_DIR && -d "$LOG_DIR") then
            echo "  Full log: $log_file"
        endif
        echo "========================================"
        exit 1
    endif
    echo "$step_label Wait file OK: $WAIT_FILE"
else
    echo "$step_label No WAIT_FILE configured, skipping pds_wait_files"
endif

# ── 7. Post-check 钩子（可选，只有需要定制检查的 step 才加）──
# Python 可以把 STEP_SCRIPTS_DIR 设成别的路径；默认 scripts/eda
# 钩子脚本命名规则：${STEP_SCRIPTS_DIR}/${STEP_NAME}_post_check.csh
# 例：scripts/eda/run_sta_post_check.csh
if ($?STEP_SCRIPTS_DIR && "$STEP_SCRIPTS_DIR" != "") then
    set scripts_dir = "$STEP_SCRIPTS_DIR"
else
    set scripts_dir = "scripts/eda"
endif
set post_check = "${scripts_dir}/${STEP_NAME}_post_check.csh"

if (-f "$post_check") then
    echo ""
    echo "$step_label Running post-check hook: $post_check"
    # source 让钩子脚本能访问 wrapper 的所有环境变量
    source "$post_check"
    if ($status != 0) then
        echo "========================================"
        echo "  ERROR: $step_label Post-check hook FAILED"
        echo "  Hook: $post_check"
        echo "========================================"
        exit 1
    endif
    echo "$step_label Post-check OK"
else
    # 没配钩子脚本 → 跳过，完全正常，不输出 warning
endif

# ── 8. 成功结束 ──
set end_time = `date +%Y%m%d_%H%M%S`
echo ""
echo "========================================"
echo "  $step_label SUCCESS"
echo "  Start:  $timestamp"
echo "  End:    $end_time"
echo "  Design: $DESIGN_NAME"
if ($?LOG_DIR && -d "$LOG_DIR") then
    echo "  Log:    $log_file"
endif
echo "========================================"

exit 0
