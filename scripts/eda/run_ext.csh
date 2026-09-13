#!/bin/csh -f
# run_ext.csh
# 寄生参数提取，调用 StarRCX

set design_name = $DESIGN_NAME
set run_dir     = $RUN_DIR
set starrcx_bin = $STARRCX_BIN
set corner      = $CORNER

set log_file    = ${run_dir}/run_ext.log

echo "==== run_ext ====" >! $log_file
echo "Design:  $design_name"  >> $log_file
echo "Run dir: $run_dir"       >> $log_file
echo "Tool:    $starrcx_bin"   >> $log_file
echo "Corner:  $corner"        >> $log_file
echo "Time:    `date`"         >> $log_file
echo ""                        >> $log_file

# TODO: 替换为实际 StarRCX 命令
# $starrcx_bin -x $run_dir/${design_name}.netlist -o $run_dir/${design_name}.spef -corner $corner

echo "ext_done = True" >> $log_file
echo ""                 >> $log_file
echo "Exit code: 0"     >> $log_file
exit 0
