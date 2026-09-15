# ECO Agent 产品化路线图

> 最后更新：2026-09-15
> 适用版本：eco-agent 0.1.0（原型完成 + Real 模式 + 交互层）
> 总工作量预估：~30 人天（不含真实 EDA 环境等待时间）

---

## 优先级定义

| 标签 | 含义 | 触发条件 |
|---|---|---|
| **P0** | 不解决就不能上线 | 上线前必须完成 |
| **P1** | 上线后一周内必须完成 | 上线后 7 天内 |
| **P2** | 生产运行后 1~2 个月内 | 有真实用户后按需推进 |
| **P3** | 可选增强 | 有明确需求再做 |

---

## 当前状态快照

| 维度 | 原型得分 | 产品级门槛 | 备注 |
|---|---|---|---|
| 功能完备性 | 95/100 | 80 | 10 个 Step + Real 模式 + slash 命令 + /init 全部实现 |
| 架构设计 | 90/100 | 70 | config.yaml 合并 + resolve 简化 + CMDS_FILE 方案 B |
| 可维护性 | 55/100 | 85 | ~300 测试全绿（含 BSD csh 集成测试） |
| 生产就绪性 | 30/100 | 80 | Real 模式代码完成，待真实 EDA 环境验证 |
| 可观测性 | 10/100 | 70 | 待 P0-2 结构化日志 |
| 用户体验 | 55/100 | 80 | 交互模式 + slash 命令 + /init 自动生成配置 |

---

## P0：上线阻塞项（必须在生产之前完成）

### 🔴 P0-0：真实 EDA Smoke Test 预研

- **工作量**：1 天
- **难度**：🟢 低（但需要 EDA 环境）
- **前置条件**：能访问至少一套真实设计（Innovus + Primetime + Calibre 可用）
- **产出**：`docs/p0-eda-smoke-test-report.md`
- **具体动作**：
  - [ ] 用 Python subprocess 手动跑一次 Innovus session（route 一个小设计），记录实际耗时、stdout/stderr 格式、退出码
  - [ ] 手动跑 Primetime STA，确认如何从 stdout 提取 violation 数量
  - [ ] 手动 kill 一个正在运行的 EDA 进程，确认是否会遗留子进程（用 `pkill -P` 验证）
  - [ ] 模拟"run_pt_fix_setup 跑到一半 kill"的场景，观察 EDA 留下的 netlist/session 文件是否会影响下一次重跑
  - [ ] 确认 retry 的正确语义：是 clean run_dir 后重跑？还是整个 Phase3 从 fix 策略选择开始重跑？
- **验收标准**：
  - 拿到三个 EDA 工具的 stdout 样本和解析规则
  - 拿到正确的 retry 语义（为 Error 边界策略提供依据）
  - 拿到 kill -9 后的清理步骤清单
- **风险**：如果 EDA 环境不可用，阻塞整个 P0

---

### ✅ P0-1：真实 MCP Server 实现（已完成）

- **工作量**：3 天 → 实际完成
- **难度**：🔴 高（依赖 P0-0 的产出）
- **前置条件**：P0-0 smoke test 报告 + 真实 EDA 环境
- **产出**：`src/mcp_server/real.py` + `parsers.py` + `scripts/eda/run_step_wrapper.csh` + `config/config.yaml`
- **具体动作**：
  - [x] 实现 `class RealECOMCPServer(ECOMCPServer)`，读 config.yaml → resolve 占位符 → 写临时命令文件 → subprocess 调 wrapper
  - [x] 每个 Step 实现独立 timeout（config.yaml.timeout_s，默认 3600s 兜底）
  - [x] 超时 kill：subprocess.run(timeout=...) + finally 清理临时命令文件
  - [x] 解析 EDA stdout：`parsers.py` 内置 `STEP_PARSE_PATTERNS` 正则提取 violations
  - [x] 日志采集：wrapper 逐行 tee 到 log_dir/{step_name}.log
  - [x] 提供环境切换开关：`ECO_BACKEND=real` 启用真实 MCP，`mock` 用 Mock
  - [x] BSD csh 集成测试验证 wrapper 语法兼容性（修复 `$?X &&` 非短路、`$(pwd)` 非 csh 语法、tee 吞退出码三个问题）
- **验收标准**：
  - ✅ 代码完成，~300 测试全绿（含 20 个 MCP Real 模式测试）
  - ⏸ 待真实 EDA 环境验证（依赖 P0-0 + P0-5）

---

### 🔴 P0-2：结构化日志

- **工作量**：1 天
- **难度**：🟢 低
- **前置条件**：无
- **产出**：`src/utils/logger.py` + 全代码 print() 替换
- **具体动作**：
  - [ ] 引入 `structlog`（或标准 `logging` JSON formatter），统一 Logger
  - [ ] 每条日志携带 trace_id（= LangGraph thread_id）、step_name、phase_name、run_dir
  - [ ] 日志级别：DEBUG（Step 内部细节）、INFO（Step 开始/结束）、WARNING（non-blocking 异常）、ERROR（blocking 异常）
  - [ ] 同时输出到两个目的地：stderr（实时查看）+ `run_dir/pipeline.log`（完整日志）
  - [ ] Step 耗时自动记录：`logger.info("step_done", step="run_sta", elapsed_sec=45.2)`
  - [ ] Error Handler interrupt 时记录：`logger.warning("user_interrupt", step="run_pt_fix_setup", choice="retry")`
  - [ ] 替换 `src/nodes/` / `src/routers/` / `src/main.py` 所有 print()
- **验收标准**：
  - 单次 pipeline run 的完整日志可以从 `pipeline.log` 完整重建执行过程
  - 搜索 `step=run_sta` 可以快速筛出该 Step 的所有日志
  - 日志量与当前 print() 相当，不影响性能

---

### 🟡 P0-3：超时控制 + 资源清理（部分完成）

- **工作量**：1-2 天
- **难度**：🟡 中
- **前置条件**：无（但真实 EDA 接入后需要验证 kill 行为）
- **产出**：`src/utils/timeout.py` + `src/mcp_server/real.py` 完善
- **具体动作**：
  - [x] 超时控制：RealECOMCPServer 从 config.yaml.timeout_s 读取，subprocess.run(timeout=...) 兜底
  - [x] 临时文件清理：finally 块 `_cleanup_cmd_files` 删除 .cmds + .status 文件
  - [ ] 统一 SqliteSaver 连接管理：用 context manager 或 atexit handler 确保进程退出时关闭所有 SQLite 连接
  - [ ] 子进程树清理：超时 kill 后验证 EDA 子进程是否被彻底杀掉（`pkill -P` 双重保险）
  - [ ] run_dir 清理策略：失败的 run_dir 自动保留用于诊断，成功的 run_dir 保留（可通过 config 配置）
  - [ ] SIGTERM 优雅退出：收到 SIGTERM 时 gracefully shutdown（保存 Checkpoint → 清理资源 → 退出）
- **验收标准**：
  - ⏸ Ctrl+C 后：Checkpoint 正常保存、EDA 子进程被 kill、SQLite 连接关闭（待真实环境验证）
  - ⏸ kill -9 后：EDA 子进程被 kill（待真实环境验证）
  - ⏸ 连续跑 10 次 pipeline 无资源泄漏（待真实环境验证）

---

### 🔴 P0-4：Error 边界策略落地

- **工作量**：2 天
- **难度**：🟡 中（依赖 P0-0 的 retry 语义结论）
- **前置条件**：P0-0 smoke test 报告
- **产出**：`src/nodes/node_error_handler.py` 改造
- **具体动作**：
  - [ ] 根据 P0-0 结论确定 retry 语义：clean run_dir 后重跑？还是 Command(goto) 到更高层（phase2_summary）重新开始？
  - [ ] 加 retry 上限计数器（State 加字段 `retry_count: dict[str, int]`），每个 step 最多 retry 3 次
  - [ ] 超过 retry 上限 → 自动 abort → finalize，不阻塞用户
  - [ ] clean run_dir 逻辑：retry 前清理哪些文件（EDA session 文件？netlist？还是整个 step 输出目录）
  - [ ] Error Handler interrupt 时，State 里携带 retry_count 信息，让用户可以看到"这是第 N 次 retry"
- **验收标准**：
  - retry 正确执行，不会因为 EDA 残留文件导致二次失败
  - retry 3 次后自动 abort，不卡住
  - Error Handler 的 retry_count 在 Checkpoint 里正确持久化

---

### 🔴 P0-5：真实环境冒烟测试

- **工作量**：2-3 天
- **难度**：🟢 低（但需要等待 EDA 环境排队）
- **前置条件**：P0-1 / P0-2 / P0-3 / P0-4 全部完成 + 真实 EDA 环境 + 一套真实设计
- **产出**：`docs/p0-smoke-test-report.md`（附截图和产出物文件列表）
- **具体动作**：
  - [ ] 在真实设计上跑完整 happy path（Phase1 → Phase2 → Phase3 → 1 轮迭代 → finalize）
  - [ ] 记录每个 Step 的实际耗时、产出物文件路径、Checkpoint 完整性
  - [ ] 测试 retry：手动让某个 Step 超时，验证 Error Handler 能正确 retry
  - [ ] 测试进程 kill：中途 kill 进程，验证重启后能从 Checkpoint 恢复
  - [ ] 测试 abort：Error Handler 选 abort，验证 finalize 正常产出报告
  - [ ] 记录与 Mock 场景的差异点（如果有）
- **验收标准**：
  - 5 个冒烟测试场景全部通过
  - 最终产出物（finalize 报告 + fix 后 netlist）真实可验证
  - 没有因为真实 EDA 而需要修改架构层面的代码（如果需要，阻塞 P0）

---

### P0 工作量汇总

| 子项 | 工作量 | 前置依赖 | 状态 |
|---|---|---|---|
| P0-0 Smoke Test 预研 | 1 天 | 无（但需要 EDA 环境） | ⏸ 待 EDA 环境 |
| P0-1 真实 MCP | 3 天 | P0-0 | ✅ 代码完成 |
| P0-2 结构化日志 | 1 天 | 无（可并行） | ⏸ 待开发 |
| P0-3 超时 + 资源清理 | 1-2 天 | P0-0（kill 行为验证） | 🟡 部分完成 |
| P0-4 Error 边界策略 | 2 天 | P0-0 | ⏸ 待 P0-0 结论 |
| P0-5 冒烟测试 | 2-3 天 | P0-1 ~ P0-4 | ⏸ 待真实环境 |
| **合计** | **9~13 人天** | P0-0 是关键路径 | **P0-1 已完成，剩余 ~6~10 天** |

---

## P1：上线后一周内完成

### 🟡 P1-1：代码质量门禁

- **工作量**：0.5 天
- **难度**：🟢 低
- **前置条件**：无
- **具体动作**：
  - [ ] 引入 `ruff`（lint + format），配置到 `pyproject.toml`
  - [ ] 引入 `mypy`（类型检查），strict 模式
  - [ ] 配置 `pre-commit` hook：commit 时自动跑 `ruff check --fix + ruff format + mypy`
  - [ ] 所有现有代码跑一遍 ruff + mypy，修完所有 warning
  - [ ] 在 README 里加开发指南：`pip install pre-commit && pre-commit install`
- **验收标准**：
  - `ruff check && ruff format --check && mypy src/` 零 warning
  - pre-commit hook 生效

---

### 🟡 P1-2：CI 流水线

- **工作量**：0.5 天
- **难度**：🟢 低
- **前置条件**：P1-1
- **具体动作**：
  - [ ] `.github/workflows/ci.yml`：push 到 main/develop 分支自动触发
  - [ ] CI 步骤：`ruff check → mypy → pytest --cov → coverage ≥ 85% gate`
  - [ ] PR 时自动跑 CI，PR 页面显示测试结果
  - [ ] 可选：Nightly build 跑完整 E2E 测试
- **验收标准**：
  - 一次 push 触发 CI，2 分钟内完成
  - CI badge 在 README 里显示 passing

---

### 🟡 P1-3：Docker 化

- **工作量**：1 天
- **难度**：🟢 低
- **前置条件**：P0-1（真实 MCP）
- **具体动作**：
  - [ ] 写 `Dockerfile`：Python 3.13 slim + 系统依赖 + pip install
  - [ ] EDA 工具通过 volume mount 挂入（不打进镜像）
  - [ ] run_dir 和 checkpoint sqlite 通过 volume mount 持久化
  - [ ] 提供 `docker-compose.yml`：声明 EDA 路径、设计文件路径、checkpoint 路径
  - [ ] 写一份最小的 Docker 使用说明（在 README 里）
- **验收标准**：
  - `docker build -t eco-agent .` 成功
  - `docker compose run eco-agent pipeline run --design demo` 能跑通 Mock 场景

---

### 🟡 P1-4：CLI 完善（用户体验）

- **工作量**：1 天
- **难度**：🟢 低
- **前置条件**：无
- **具体动作**：
  - [ ] 错误消息不再是 traceback：友好文案 + exit code 分类
  - [ ] 支持 `--verbose`（显示 DEBUG 日志）/ `--quiet`（只显示 ERROR）
  - [ ] 进度指示：每个 Step 开始/结束时打印进度条（"Phase 2/3 并行中... 3/3 done"）
  - [ ] `pipeline list`：列出所有可用设计（扫描 config/ 目录）
  - [ ] `pipeline resume --thread-id xxx`：断点续跑更直观
  - [ ] `pipeline status --thread-id xxx`：查看某个 checkpoint 的当前状态
- **验收标准**：
  - 一个新用户只看 `pipeline --help` 就能完成一次完整的 happy path

---

### 🟡 P1-5：Config 校验（部分完成）

- **工作量**：0.5 天
- **难度**：🟢 低
- **前置条件**：无
- **具体动作**：
  - [x] MCP Server Real 模式启动期校验：`_validate_step_commands()` 校验未知 step / 空命令 / timeout 非正整数
  - [x] config_loader 构图前校验：`STEP_TO_PHASE` 注册表防跨 phase 错配
  - [ ] YAML schema 校验：用 `pydantic` 定义 PipelineConfig schema（当前是手动 if-else）
  - [ ] 校验失败时，错误消息精确到具体配置项
- **验收标准**：
  - ✅ MCP config 写错 step 名 / timeout 非正整数，启动 1 秒内报错退出
  - ⏸ 待 pydantic schema 统一校验

---

### 🟡 P1-6：PIPELINE_REGISTRY 注册机制

- **工作量**：0.5 天
- **难度**：🟢 低
- **前置条件**：无
- **具体动作**：
  - [ ] 把 `src/graph_builder.py` 改名为 `src/graph_builders.py`（复数，为多流程做准备）
  - [ ] 引入 `PIPELINE_REGISTRY: dict[str, Callable]`，当前只有 `"default"` 一个注册
  - [ ] CLI 加 `--pipeline {default}` 参数（当前只有一个选项，但为 P2-14 多流程铺路）
  - [ ] 保持功能完全一致——这是纯重构，不改变任何行为
- **验收标准**：
  - 重构后所有 187 测试零改动全绿
  - CLI 可以 `--pipeline default` 显式指定

---

### 🟡 P1-7：run_dir 隔离

- **工作量**：1 天
- **难度**：🟡 中
- **前置条件**：无
- **具体动作**：
  - [ ] run_dir 改为时间戳命名：`runs/{design_name}/run_{YYYYMMDD_HHMMSS}/`
  - [ ] 每次迭代内部再分：`runs/{design_name}/run_{ts}/iter_{n}/`
  - [ ] Checkpoint sqlite 放在 `runs/{design_name}/run_{ts}/checkpoint.sqlite`
  - [ ] Error Handler 不中断时自动保留完整 run_dir
  - [ ] finalize 报告放在 `runs/{design_name}/run_{ts}/finalize_report.md`
  - [ ] 启动时 run_dir 自动创建，失败不影响主流程
- **验收标准**：
  - 连续跑 3 次不同设计，各自 run_dir 独立，互不干扰
  - 从 run_dir 路径可以一眼看出是哪次 run、第几轮迭代

---

### P1 工作量汇总

| 子项 | 工作量 | 前置依赖 |
|---|---|---|
| P1-1 代码质量门禁 | 0.5 天 | 无 |
| P1-2 CI 流水线 | 0.5 天 | P1-1 |
| P1-3 Docker 化 | 1 天 | P0-1 |
| P1-4 CLI 完善 | 1 天 | 无 |
| P1-5 Config 校验 | 0.5 天 | 无 |
| P1-6 PIPELINE_REGISTRY | 0.5 天 | 无 |
| P1-7 run_dir 隔离 | 1 天 | 无 |
| **合计** | **5 人天** | 可大量并行 |

---

## P2：生产运行后 1~2 个月内完成

### 🟢 P2-1：指标采集 + 监控

- **工作量**：2 天
- **难度**：🟡 中
- **前置条件**：P0-2（结构化日志）
- **具体动作**：
  - [ ] 引入 `prometheus-client`，每个 Step 上报耗时 histogram + error counter
  - [ ] 上报指标：`step_duration_seconds{step="run_sta"}`、`step_errors_total{step="run_pt_fix_setup", type="timeout"}`、`pipeline_iterations_total`
  - [ ] 提供 `/metrics` endpoint（可以用 FastAPI 或独立脚本 push 到 Pushgateway）
  - [ ] 提供一份 Grafana 看板 JSON（Step 耗时趋势 / Error 率 / 迭代次数分布）
- **验收标准**：
  - 一个 Grafana 看板可以看到过去 7 天所有 pipeline run 的执行情况

---

### 🟢 P2-2：Web 管理 UI

- **工作量**：3-5 天
- **难度**：🟡 中
- **前置条件**：P2-1（指标基础设施）
- **具体动作**：
  - [ ] 最小版用 Streamlit（1 天够）：列出 checkpoint、查看 State、手动触发 retry/abort
  - [ ] 如果需要漂亮的前端，用 FastAPI + React（3-5 天）
  - [ ] 功能：Checkpoint 列表 + 实时日志流 + 手动中断恢复 + finalize 报告查看
- **验收标准**：
  - EDA 工程师不用命令行就能完成一次完整的 pipeline run

---

### 🟢 P2-3：多流程支持（Layer 1）

- **工作量**：3 天
- **难度**：🟡 中
- **前置条件**：P1-6（PIPELINE_REGISTRY）+ P1-1（代码质量门禁）
- **具体动作**：
  - [ ] 抽公共辅助函数：`_wire_core_components()` / `_wire_parallel_phase()` / `_wire_branch_phase()`
  - [ ] 实现 `build_hold_only`：Phase2 只跑 STA + PV，Phase3 只跑 fix_hold，硬路由不中断
  - [ ] 实现 `build_regression_only`：Phase2 全部跑，跳过 Phase3，Phase2_summary → finalize
  - [ ] 实现 `build_lvs_eco`：Phase2 加 run_lvs，Phase3 加 fix_lvs
  - [ ] 每个流程独立测试
- **验收标准**：
  - CLI 可以 `pipeline run --pipeline hold_only --design demo`
  - 3 个流程 builder 每个 ≤ 80 行
  - 公共辅助函数每个 ≤ 50 行

---

### 🟢 P2-4：YAML Config 热重载

- **工作量**：1 天
- **难度**：🟢 低
- **前置条件**：无
- **具体动作**：
  - [ ] 支持 `pipeline run --config config/session_001.yaml`
  - [ ] Config 可以携带 per-session 覆盖：指定 thread_id / checkpoint 路径 / scenario / timeout 覆盖
  - [ ] 每个 session 的 Config 快照保存在 run_dir 里
- **验收标准**：
  - 两个用户用不同 Config 跑同一设计，互不干扰

---

### 🟢 P2-5：Checkpoint 清理策略

- **工作量**：1 天
- **难度**：🟢 低
- **前置条件**：无
- **具体动作**：
  - [ ] 超过 7 天的 Checkpoint 自动归档到 `runs/archive/`
  - [ ] 超过 30 天的 Checkpoint 删除
  - [ ] 提供 `pipeline clean` 命令手动触发
  - [ ] 提供 `pipeline clean --dry-run` 预览
- **验收标准**：
  - `runs/` 目录不会无限增长

---

### 🟢 P2-6：并发会话验证

- **工作量**：0.5 天
- **难度**：🟢 低
- **前置条件**：P1-7（run_dir 隔离）
- **具体动作**：
  - [ ] 手动开 3 个终端同时跑不同设计的 pipeline
  - [ ] 验证每个 session 独立 thread_id + 独立 run_dir，互不干扰
  - [ ] 验证 SqliteSaver 多线程安全（check_same_thread=False 已在 checkpoint_config 里设置）
- **验收标准**：
  - 3 个并发 session 全部跑完，无状态污染

---

### P2 工作量汇总

| 子项 | 工作量 | 前置依赖 |
|---|---|---|
| P2-1 指标 + 监控 | 2 天 | P0-2 |
| P2-2 Web UI | 3-5 天 | P2-1 |
| P2-3 多流程 Layer 1 | 3 天 | P1-6 |
| P2-4 Config 热重载 | 1 天 | 无 |
| P2-5 Checkpoint 清理 | 1 天 | 无 |
| P2-6 并发验证 | 0.5 天 | P1-7 |
| **合计** | **10~13 人天** | 部分可并行 |

---

## P3：可选增强（有明确需求再做）

| # | 项目 | 什么时候考虑 |
|---|---|---|
| P3-1 | YAML 声明拓扑（Layer 2） | 流程数 ≥ 15 且 70% 能用标准 schema 覆盖 |
| P3-2 | LLM 动态编排（Layer 3） | 业务场景不再是固定流水线 |
| P3-3 | Token Usage 统计 + 成本控制 | LLM 用量成为显著成本 |
| P3-4 | 分布式执行（多机跑并行 Step） | Phase2 串行瓶颈 |
| P3-5 | API 文档生成（pdoc / Sphinx） | 外部团队需要 import 这个包 |
| P3-6 | OpenTelemetry 全链路追踪 | 多服务联动时 |
| P3-7 | 单元测试覆盖率提升到 95% | 长期维护需要 |
| P3-8 | 真实 EDA 接入的三个额外验证点 | P0-5 冒烟测试通过后，按需补充 |

---

## 关键依赖路径

```
P0-0 Smoke Test ──┬──→ P0-1 真实 MCP ──→ P0-5 冒烟测试（上线门槛）
                  ├──→ P0-4 Error 边界 ──→ P0-5 冒烟测试
                  └──→ P0-3 超时 kill 验证 ──→ P0-1 真实 MCP

P0-2 结构化日志 ──→ P2-1 指标监控
P1-6 PIPELINE_REGISTRY ──→ P2-3 多流程 Layer 1

P1-1 代码质量门禁 ──→ P1-2 CI ──→ 所有后续开发
```

**P0-0 是关键路径的钥匙**——如果 EDA 环境可用，整个 P0 预计 9~13 人天；如果 EDA 环境不可用，阻塞 P0-1 / P0-3 / P0-4 / P0-5。

---

## 风险清单

| # | 风险 | 概率 | 影响 | 缓解措施 |
|---|---|---|---|---|
| R1 | 真实 EDA retry 语义与预期不同 | 中 | 重写 error_handler | P0-0 先搞清楚，不要到 P0-4 才发现 |
| R2 | EDA 环境不可用 / 排队时间长 | 高 | P0 延期 | P0-0 1 天 smoke test 先确认，阻塞则调整优先级 |
| R3 | 真实 EDA stdout 格式不稳定，解析逻辑要频繁改 | 中 | P0-1 工作量膨胀 | 设计解析逻辑时留 fallback（解析失败就用 Mock 默认值） |
| R4 | 超时 kill 无法彻底杀掉 EDA 子进程 | 低 | 进程泄漏 | `kill -9` + `pkill -P` 双重保险；P0-3 验证 |
| R5 | run_dir 清理策略导致有用数据丢失 | 低 | 影响诊断 | 默认保留 30 天；手动 clean 带 `--dry-run` |

---

## 附录：工作量估算说明

所有天数为**专注工作的有效时间**，不含开会、等待 EDA 排队、写文档。

估算基准：单人开发者，熟悉本项目架构。如果由不熟悉的人接手，每项 × 1.5 倍。

---

**下一步**：从 P0-0 Smoke Test 开始。如果 EDA 环境不可用，先推进 P0-2（结构化日志）和 P1-1 / P1-2（代码质量门禁 + CI），这三项完全不依赖 EDA。