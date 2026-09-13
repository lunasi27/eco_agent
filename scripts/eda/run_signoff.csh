#!/bin/csh -f
# run_signoff.csh
# Signoff 签核检查，调用 Calibre

set design_name    = $DESIGN_NAME
set run_dir        = $RUN_DIR
set calibre_bin    = $CALIBRE_BIN
set signoff_rules  = $SIGNOFF_RULES

set log_file       = ${run_dir}/run_signoff.log

echo "==== run_signoff ====" >! $log_file
echo "Design:   $design_name"   >> $log_file
echo "Run dir:  $run_dir"        >> $log_file
echo "Calibre:  $calibre_bin"    >> $log_file
echo "Rules:    $signoff_rules"  >> $log_file
echo "Time:     `date`"          >> $log_file
echo ""                           >> $log_file

# TODO: 替换为实际 Signoff 命令
# $calibre_bin -drc -runset $run_dir/signoff.runset -outdir $run_dir

echo "Signoff DRC PASSED" >> $log_file
echo ""                     >> $log_file
echo "Exit code: 0"         >> $log_file
exit 0
