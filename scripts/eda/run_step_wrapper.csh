#!/bin/csh -f
# ================================================================================
# 通用 step 执行 wrapper —— 所有 10 个 step 共用
# ================================================================================
#
# Python 侧通过环境变量传入所有配置（必需的缺了就报错）：
#   PROJECT_CSHRC    → source IT 环境（PATH、EDA_HOME、license 等）
#   STEP_NAME        → 当前 step 名（run_sta / run_pt_fix_setup 等）
#   CMDS_FILE        → 命令文件路径（每行一条命令，按行顺序 eval，fail-fast）
#   DESIGN_NAME      → 设计名（SOC_XXX_SUB 等）
#   EXECUTION_DIR    → cd 到这个目录执行命令
#   WAIT_FILE        → pds_wait_files 检查的完成标志文件
#   LOG_DIR          → 日志存储目录（从 project.yaml.logs 来）
#
# 可选环境变量：
#   DEBUG=1          → 打印所有执行细节（set -x + env dump + 命令文件内容）
#   FIX_STRATEGY     → 修复策略（fix 类 step，命令里可直接用 $FIX_STRATEGY）
#   STEP_SCRIPTS_DIR → post_check 钩子目录（默认 scripts/eda）
#
# 命令文件约定：
#   - 每行一条完整命令（支持 source / 管道 / 重定向等任意 shell 语法）
#   - 不支持行尾反斜杠续行（一条命令必须写在同一行）
#   - 空行会被跳过
#   - 任意一条命令非零退出 → 立即终止整个 step，报错带「第 i/N 条」
#
# ================================================================================

# ── 必需环境变量校验（提前报错，比后面直接用挂掉信息清晰）──
if (! $?STEP_NAME || "$STEP_NAME" == "") then
    echo "ERROR: STEP_NAME environment variable not set"
    exit 1
endif
if (! $?CMDS_FILE || "$CMDS_FILE" == "" || ! -r "$CMDS_FILE") then
    echo "ERROR: CMDS_FILE environment variable not set or not readable for step [$STEP_NAME]"
    exit 1
endif

set step_label  = "[$STEP_NAME]"
set status_file = "${CMDS_FILE}.status"
rm -f "$status_file"

# ── 可选变量统一补默认空值，避免后面直接 $引用时 csh 报 Undefined variable ──
# 注意：传统 csh 的 && 不做短路求值，不能写 "$?X && $X == ..."，
#       必须先保证变量存在，再做值比较。
if (! $?PROJECT_CSHRC)   setenv PROJECT_CSHRC ""
if (! $?DESIGN_NAME)     setenv DESIGN_NAME ""
if (! $?EXECUTION_DIR)   setenv EXECUTION_DIR ""
if (! $?WAIT_FILE)       setenv WAIT_FILE ""
if (! $?LOG_DIR)         setenv LOG_DIR ""
if (! $?FIX_STRATEGY)    setenv FIX_STRATEGY ""
if (! $?DEBUG)           setenv DEBUG ""
if (! $?STEP_SCRIPTS_DIR) setenv STEP_SCRIPTS_DIR ""

# ── Debug 模式开关 ──
if ("$DEBUG" == "1") then
    set -x
    echo ""
    echo "========================================"
    echo "  DEBUG MODE ON - Environment Dump"
    echo "========================================"
    echo "  PROJECT_CSHRC = $PROJECT_CSHRC"
    echo "  STEP_NAME     = $STEP_NAME"
    echo "  CMDS_FILE     = $CMDS_FILE"
    echo "  DESIGN_NAME   = $DESIGN_NAME"
    echo "  EXECUTION_DIR = $EXECUTION_DIR"
    echo "  WAIT_FILE     = $WAIT_FILE"
    echo "  LOG_DIR       = $LOG_DIR"
    echo "  FIX_STRATEGY  = $FIX_STRATEGY"
    echo "  ---- command file content ----"
    cat -n "$CMDS_FILE"
    echo "========================================"
    echo ""
endif

# ── 1. Source IT 环境 ──
# IT 托底的 EDA 环境，所有 step 共用同一个 cshrc
if ("$PROJECT_CSHRC" != "" && -f "$PROJECT_CSHRC") then
    source "$PROJECT_CSHRC"
else
    echo "WARNING: $step_label PROJECT_CSHRC not found or not set: $PROJECT_CSHRC"
endif

# ── 2. Cd 到执行目录 ──
# 每个 step 可能不同，Python 从 project.yaml.base / run_context.execution_dir 推断
if ("$EXECUTION_DIR" != "" && -d "$EXECUTION_DIR") then
    cd "$EXECUTION_DIR"
    if ("$DEBUG" == "1") then
        echo "[DEBUG] cd to: `pwd`"
    endif
else
    # 允许 EXECUTION_DIR 不存在时 fallback 到当前目录
    echo "WARNING: $step_label EXECUTION_DIR not found or not set: $EXECUTION_DIR"
    echo "  Falling back to current directory: `pwd`"
endif

# ── 3. 日志准备 ──
set timestamp = `date +%Y%m%d_%H%M%S`
set total     = `wc -l < "$CMDS_FILE"`

set log_enabled = 0
set log_file    = ""
if ("$LOG_DIR" != "" && -d "$LOG_DIR") then
    set log_enabled = 1
    set log_file    = "${LOG_DIR}/${STEP_NAME}_${timestamp}.log"
endif

# ── 4. 开始日志头 ──
echo ""
echo "========================================"
echo "  $step_label STARTING  ($timestamp)"
echo "  Design: $DESIGN_NAME"
echo "  Dir:    `pwd`"
echo "  Total commands: $total"
echo "========================================"

if ($log_enabled == 1) then
    echo "$step_label LOG FILE: $log_file"
    echo "========================================" | tee "$log_file"
    echo "  $step_label STARTING  ($timestamp)"    | tee -a "$log_file"
    echo "  Design: $DESIGN_NAME"                  | tee -a "$log_file"
    echo "  Dir:    `pwd`"                         | tee -a "$log_file"
    echo "  Total commands: $total"                | tee -a "$log_file"
    echo "========================================" | tee -a "$log_file"
endif

# ── 5. 逐行执行命令（顺序执行 + fail-fast）──
# 用 sed -n 按行号取命令，保留行内空格；空行跳过。
# tee 管道会吞掉 eval 的真实退出码（$status 变成 tee 的），
# 所以在子 shell 里执行并把 eval 的 $status 写入 status 文件，父 shell 再读回。
set i = 1
while ($i <= $total)
    set cur_cmd = "`sed -n "${i}p" "$CMDS_FILE"`"

    if ("$cur_cmd" == "") then
        @ i++
        continue
    endif

    echo ""
    echo "-------- $step_label command $i/$total --------"
    echo "$cur_cmd"

    if ($log_enabled == 1) then
        echo ""                                              | tee -a "$log_file"
        echo "-------- $step_label command $i/$total --------" | tee -a "$log_file"
        echo "$cur_cmd"                                      | tee -a "$log_file"
        ( eval "$cur_cmd" ; echo "$status" > "$status_file" ) |& tee -a "$log_file"
        if (-f "$status_file") then
            set cmd_status = `cat "$status_file"`
            rm -f "$status_file"
        else
            # 子 shell 异常到没写出 status（如被 kill），按失败处理
            set cmd_status = 1
        endif
    else
        eval "$cur_cmd"
        set cmd_status = $status
    endif

    if ($cmd_status != 0) then
        echo ""
        echo "========================================"
        echo "  ERROR: $step_label command $i/$total FAILED"
        echo "  Exit code: $cmd_status"
        echo "  Command:   $cur_cmd"
        if ($log_enabled == 1) then
            echo ""                                          | tee -a "$log_file"
            echo "========================================" | tee -a "$log_file"
            echo "  ERROR: $step_label command $i/$total FAILED" | tee -a "$log_file"
            echo "  Exit code: $cmd_status"                  | tee -a "$log_file"
            echo "  Command:   $cur_cmd"                     | tee -a "$log_file"
            echo "  Full log:  $log_file"                    | tee -a "$log_file"
            echo "========================================" | tee -a "$log_file"
        else if ("$log_file" != "") then
            echo "  Full log: $log_file"
        endif
        echo "========================================"
        exit $cmd_status
    endif

    @ i++
end

# ── 6. PDS wait 检查（所有命令成功之后）──
# WAIT_FILE 是整个 step 的最终完成标志，PDS 系统提供 pds_wait_files 检查
# 没配 WAIT_FILE 的 step 跳过（纯 pds 命令内部可能已经托底）
if ("$WAIT_FILE" != "") then
    echo ""
    echo "$step_label Checking wait file: $WAIT_FILE"

    if ($log_enabled == 1) then
        echo ""                                      | tee -a "$log_file"
        echo "$step_label Checking wait file: $WAIT_FILE" | tee -a "$log_file"
        ( pds_wait_files "$WAIT_FILE" ; echo "$status" > "$status_file" ) |& tee -a "$log_file"
        set wait_status = `cat "$status_file"`
        rm -f "$status_file"
    else
        pds_wait_files "$WAIT_FILE"
        set wait_status = $status
    endif

    if ($wait_status != 0) then
        echo "========================================"
        echo "  ERROR: $step_label pds_wait_files FAILED"
        echo "  Wait file: $WAIT_FILE"
        echo "  可能原因：脚本没跑完、输出目录不存在、或者工具内部异常"
        if ($log_enabled == 1) then
            echo "========================================" | tee -a "$log_file"
            echo "  ERROR: $step_label pds_wait_files FAILED" | tee -a "$log_file"
            echo "  Wait file: $WAIT_FILE" | tee -a "$log_file"
            echo "  Full log:  $log_file"    | tee -a "$log_file"
            echo "========================================" | tee -a "$log_file"
        else if ("$log_file" != "") then
            echo "  Full log: $log_file"
        endif
        exit 1
    endif
    echo "$step_label Wait file OK: $WAIT_FILE"
else
    echo "$step_label No WAIT_FILE configured, skipping pds_wait_files"
endif

# ── 7. Post-check 钩子（可选，只有需要定制检查的 step 才加）──
# 钩子脚本命名规则：${STEP_SCRIPTS_DIR}/${STEP_NAME}_post_check.csh
# 例：scripts/eda/run_sta_post_check.csh
if ("$STEP_SCRIPTS_DIR" != "") then
    set scripts_dir = "$STEP_SCRIPTS_DIR"
else
    set scripts_dir = "scripts/eda"
endif
set post_check = "${scripts_dir}/${STEP_NAME}_post_check.csh"

if (-f "$post_check") then
    echo ""
    echo "$step_label Running post-check hook: $post_check"

    if ($log_enabled == 1) then
        echo ""                                              | tee -a "$log_file"
        echo "$step_label Running post-check hook: $post_check" | tee -a "$log_file"
        ( source "$post_check" ; echo "$status" > "$status_file" ) |& tee -a "$log_file"
        set hook_status = `cat "$status_file"`
        rm -f "$status_file"
    else
        source "$post_check"
        set hook_status = $status
    endif

    if ($hook_status != 0) then
        echo "========================================"
        echo "  ERROR: $step_label Post-check hook FAILED"
        echo "  Hook: $post_check"
        echo "========================================"
        exit 1
    endif
    echo "$step_label Post-check OK"
endif

# ── 8. 成功结束 ──
set end_time = `date +%Y%m%d_%H%M%S`
echo ""
echo "========================================"
echo "  $step_label SUCCESS"
echo "  Start:  $timestamp"
echo "  End:    $end_time"
echo "  Design: $DESIGN_NAME"
if ($log_enabled == 1) then
    echo "  Log:    $log_file"
    echo ""                                      | tee -a "$log_file"
    echo "========================================" | tee -a "$log_file"
    echo "  $step_label SUCCESS"                     | tee -a "$log_file"
    echo "  Start:  $timestamp"                      | tee -a "$log_file"
    echo "  End:    $end_time"                       | tee -a "$log_file"
    echo "  Design: $DESIGN_NAME"                    | tee -a "$log_file"
    echo "  Log:    $log_file"                       | tee -a "$log_file"
    echo "========================================" | tee -a "$log_file"
endif
echo "========================================"

exit 0
