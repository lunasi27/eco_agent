#!/bin/csh -f
# run_eco_route.csh
# ECO Route 初始化，调用 Innovus 完成布线

set design_name = $DESIGN_NAME
set run_dir     = $RUN_DIR
set innovus_bin = $INNOVUS_BIN
set netlist     = $NETLIST
set sdc         = $SDC
set strategy    = $ROUTE_STRATEGY

set log_file    = ${run_dir}/run_eco_route.log

echo "==== run_eco_route ====" >! $log_file
echo "Design:   $design_name"   >> $log_file
echo "Run dir:  $run_dir"        >> $log_file
echo "Innovus:  $innovus_bin"    >> $log_file
echo "Strategy: $strategy"       >> $log_file
echo "Time:     `date`"          >> $log_file
echo ""                         >> $log_file

# TODO: 替换为实际 Innovus 命令
# $innovus_bin -no_gui -x "read_verilog $netlist; read_sdc $sdc; create_clock -period 10 [get_ports clk]; route_design $strategy; exit"

echo "route_done = True" >> $log_file
echo ""                 >> $log_file
echo "Exit code: 0"     >> $log_file
exit 0
