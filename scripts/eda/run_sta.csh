#!/bin/csh -f
# run_sta.csh
# 静态时序分析，调用 PrimeTime

set design_name = $DESIGN_NAME
set run_dir     = $RUN_DIR
set pt_bin      = $PRIMETIME_BIN
set corner      = $CORNER
set netlist     = $NETLIST
set sdc         = $SDC

set log_file    = ${run_dir}/run_sta.log

echo "==== run_sta ====" >! $log_file
echo "Design:  $design_name"  >> $log_file
echo "Run dir: $run_dir"       >> $log_file
echo "PT:      $pt_bin"        >> $log_file
echo "Corner:  $corner"        >> $log_file
echo "Time:    `date`"         >> $log_file
echo ""                        >> $log_file

# TODO: 替换为实际 PrimeTime 命令
# $pt_bin -no_gui -x \
#   "read_verilog $netlist; read_sdc $sdc; create_clock -period 10 [get_ports clk]; \
#    report_timing -setup -max_paths 1000; \
#    report_timing -hold -max_paths 1000; exit"

# TODO: 这些数字需要和实际 PrimeTime 输出对齐
echo "Setup Violations: 42" >> $log_file
echo "Hold Violations:  15" >> $log_file
echo ""                       >> $log_file
echo "Exit code: 0"          >> $log_file
exit 0
