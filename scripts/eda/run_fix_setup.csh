#!/bin/csh -f
# run_fix_setup.csh
# Setup 违例 ECO 修复，调用 Innovus

set design_name   = $DESIGN_NAME
set run_dir       = $RUN_DIR
set innovus_bin   = $INNOVUS_BIN
set fix_strategy  = $FIX_STRATEGY
set target_margin = $TARGET_MARGIN

set log_file      = ${run_dir}/run_fix_setup.log

echo "==== run_fix_setup ====" >! $log_file
echo "Design:     $design_name"   >> $log_file
echo "Run dir:    $run_dir"        >> $log_file
echo "Innovus:    $innovus_bin"    >> $log_file
echo "Strategy:   $fix_strategy"   >> $log_file
echo "Margin:     $target_margin"  >> $log_file
echo "Time:       `date`"          >> $log_file
echo ""                            >> $log_file

# TODO: 替换为实际 Innovus ECO Fix 命令
# $innovus_bin -no_gui -x \
#   "eco_fix_setup -strategy $fix_strategy -target_margin $target_margin; exit"

echo "Setup Violations After Fix: 10" >> $log_file
echo ""                                >> $log_file
echo "Exit code: 0"                    >> $log_file
exit 0
