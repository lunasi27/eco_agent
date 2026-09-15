#!/bin/csh -f
# 打印 wrapper 注入的环境变量，供 env 注入集成测试使用
echo "DESIGN_NAME=$DESIGN_NAME"
echo "STEP_NAME=$STEP_NAME"
echo "FIX_STRATEGY=$FIX_STRATEGY"
exit 0
