# ECO Agent

基于 LangGraph 的 ECO（Engineering Change Order）固定流水线 Agent 原型。

将传统 EDA ECO 流程（Route → Extract → STA/PV/Signoff 并行 → 修复 → 迭代收敛）编排为一张 LangGraph 状态图，支持并行执行、断点续跑、错误重试和多轮迭代。

---

## 架构概览

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        ECO Agent Graph                                  │
│                                                                         │
│  [entry]                                                                │
│     │                                                                   │
│     ▼                                                                   │
│  agent_entry  ←─ LLM 对话入口，识别 "ECO 任务" vs "普通对话"             │
│     │                                                                   │
│     ▼                                                                   │
│  init         ←─ 校验 design_name / design_dir / EDA 环境变量          │
│     │ done                                                              │
│     ▼                                                                   │
│  run_eco_route  ──→ run_ext      (Phase1 串行)                          │
│                       │                                                 │
│                       ▼ route_after_run_ext                             │
│              ┌────────┼────────┐                                        │
│              ▼        ▼        ▼                                       │
│          run_sta   run_pv  run_signoff  (Phase2 Send 并行)              │
│              │        │        │                                        │
│              └────────┼────────┘                                        │
│                       ▼                                                 │
│                 phase2_gate  ←─ 汇聚并行结果，单一条件路由               │
│                       │                                                 │
│         ┌─────────────┼─────────────┐                                   │
│         ▼             ▼             ▼                                   │
│   phase2_summary  error_handler  (run_sta error)                        │
│         │                                                               │
│   interrupt(修复策略?)                                                  │
│         │                                                               │
│    ┌────┼────┐                                                          │
│    ▼    ▼    ▼                                                          │
│ fix_  fix_ fix_leakage  (Phase3 分支, leakage 占位)                     │
│ setup hold                                                              │
│    │    │                                                               │
│    └────┼────┘                                                          │
│         ▼                                                               │
│   phase3_summary                                                        │
│         │                                                               │
│   interrupt(迭代决策?)                                                  │
│         │                                                               │
│    continue ────→ init (iteration_cnt++ 重新开始)                        │
│    stop ────→ finalize ──→ [END]                                        │
│                                                                         │
│  ★ 任意节点 step_status="error" 都走 → error_handler                    │
│     error_handler 用 Command(goto) 直接路由，无残留 checkpoint race       │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 安装

### 环境要求

- Python ≥ 3.11
- pip

### 安装步骤

```bash
# 1. 克隆仓库
git clone <repo-url> eco_agent && cd eco_agent

# 2. 创建虚拟环境（推荐）
python -m venv .venv
source .venv/bin/activate

# 3. 安装依赖
pip install -e ".[dev]"
```

安装完成后运行 `python -m pytest` 验证环境是否正确。

---

## 快速开始

### 方式一：Python API（推荐，可嵌入到你的应用中）

```python
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from langchain_core.messages import HumanMessage

from src.graph_builder import build_graph
from src.mcp_server.mock import MockECOMCPServer

# 1. 构建图（用 Mock Server，无需真实 EDA）
g = build_graph(
    mcp_server=MockECOMCPServer(scenario="happy_path", simulate_delay=0),
    checkpointer=MemorySaver(),
)

# 2. 启动执行
cfg = {"configurable": {"thread_id": "my_first_demo"}}
for ev in g.stream(
    {"messages": [HumanMessage(content="帮我跑 MyDesign 的 ECO")],
     "design_name": "MyDesign"},
    cfg,
):
    print(list(ev.keys())[0])  # 打印每个执行的节点名

# 3. 图停在 phase2_summary interrupt（等待用户选择修复策略）
state = g.get_state(cfg)
print(state.values["interrupt_msg"])   # STA/PV/Signoff 报告

# 4. 用户选择：setup / hold / leakage / abort
g.stream(Command(resume="setup"), cfg)

# 5. 图停在 phase3_summary interrupt（等待迭代决策）
g.stream(Command(resume="stop"), cfg)  # 或 "continue"

# 6. 最终结果
print(g.get_state(cfg).values)
# {'design_name': 'MyDesign', 'setup_vio': 30, 'hold_vio': 47,
#  'pv_pass': True, 'signoff_pass': True, ...}
```

### 方式二：CLI

```bash
# 使用默认配置（SqliteSaver + happy_path）
python -m src.main --config config/default.yaml

# 指定 scenario
python -m src.main --scenario phase2_sta_error

# 完全非交互模式（用于脚本或 CI）
python -m src.main --no-interactive
```

---

## 核心概念

### 节点类型

| 类型 | 节点 | 说明 |
|---|---|---|
| **入口** | `agent_entry` | LLM 识别用户意图是否为 ECO 任务 |
| **校验** | `init` | 初始化工作目录 + 检查 EDA 环境 |
| **串行 Step** | `run_eco_route` → `run_ext` | Phase1，先后执行 |
| **并行 Step** | `run_sta` ‖ `run_pv` ‖ `run_signoff` | Phase2，Send API 并行 |
| **分支 Step** | `run_fix_setup` / `run_fix_hold` / `run_fix_leakage` | Phase3，根据策略选一条 |
| **汇聚 Gate** | `phase2_gate` / `phase3_gate` | 统一检查并行结果，单一条件路由点 |
| **汇总 + Interrupt** | `phase2_summary` / `phase3_summary` | 生成报告 → `interrupt()` 暂停等用户输入 |
| **错误处理** | `error_handler` | 任意 Step 出错时进入，用户选 retry 或 abort |
| **结束** | `finalize` | 写入 FINISHED/REPORT.json + 收敛对比 → END |

### 三种 Interrupt 机制

| 位置 | 触发时机 | resume 取值 | 恢复后 |
|---|---|---|---|
| **phase2_summary** | Phase2 并行全部完成后 | `"setup"` / `"hold"` / `"leakage"` / `"abort"` | 进入 Phase3 分支 |
| **phase3_summary** | Phase3 修复完成后 | `"continue"` / `"stop"` | continue → init 重新开始；stop → finalize |
| **error_handler** | 任意 Step 返回 error 时 | `"retry"` / `"abort"`（或快捷键 `"r"` / `"a"`、中文 `"重试"` / `"终止"`） | retry → Command(goto=出错节点)；abort → Command(goto=finalize) |

### 持久化 Checkpoint

支持两种 checkpointer，通过 YAML 配置切换：

| 后端 | 适用场景 | 切换方式 |
|---|---|---|
| `MemorySaver` | 开发/调试/单元测试 | `checkpoint.backend: "memory"` |
| `SqliteSaver` | 生产/跨进程断点续跑 | `checkpoint.backend: "sqlite"` |

**断点续跑**：只要 `thread_id` 相同，重启程序后自动从上次 interrupt 位置恢复：

```python
# 进程 A：跑到 interrupt
g1 = build_graph(checkpointer=SqliteSaver(sqlite3.connect("runs/checkpoints.sqlite")))
g1.stream(initial_input, {"configurable": {"thread_id": "session_001"}})
del g1

# 进程 B：同一 thread_id，直接 Command(resume=...) 续跑
g2 = build_graph(checkpointer=SqliteSaver(sqlite3.connect("runs/checkpoints.sqlite")))
g2.stream(Command(resume="setup"), {"configurable": {"thread_id": "session_001"}})
# → 从 phase2_summary interrupt 处恢复，继续执行
```

### Mock Server Scenarios

MockECOMCPServer 内置多个 scenario，模拟不同的流水线状态：

| Scenario | 说明 |
|---|---|
| `happy_path` | 全链路正常，setup_vio 初始 125 → fix 后 30 |
| `convergence` | 多轮迭代收敛数据（第 1 轮 125→30，第 2 轮 30→12） |
| `phase1_ext_error` | Phase1 的 run_ext 抛异常 → error_handler |
| `phase2_sta_error` | Phase2 的 run_sta 抛异常 → error_handler → retry 后恢复 |
| `phase2_pv_error` | Phase2 的 run_pv 抛异常 → phase2_summary 警告级不阻断 |
| `phase2_signoff_error` | Phase2 的 run_signoff 抛异常 → 警告级不阻断 |
| `phase3_fix_error` | Phase3 的 fix_setup 抛异常 → error_handler |

切换到真实 EDA 只需将 `build_graph(mcp_server=RealECOMCPServer(...), ...)` 替换 Mock 即可，图代码不需要改动。

---

## 配置

编辑 `config/default.yaml`：

```yaml
eco_agent:
  default_scenario: "happy_path"         # CLI 默认 scenario
  default_thread_id: "demo_session"

checkpoint:
  backend: "sqlite"                       # "memory" | "sqlite"
  db_path: "runs/checkpoints.sqlite"      # sqlite 时生效
  check_same_thread: false

mock_server:
  simulate_delay: 0.3                    # 模拟 EDA 耗时（秒）
  write_logs: true                        # 写 .log/.rpt 到 run_dir

init_validation:
  require_design_dir: false              # 原型阶段关闭严格校验
  require_netlist: false
  require_sdc: false
  required_env_vars: []
  required_tools: []

finalize:
  write_json_report: true
  write_finished_marker: true
  log_glob: "*.log"
  rpt_glob: "*.rpt"
```

生产部署建议复制为 `config/production.yaml` 并开启校验。

---

## 🧪 怎么用（MCP Server）

ECO Agent 内置一个完整的 MCP (Model Context Protocol) Server，将 ECO 流水线的 10 个 step 暴露为 MCP Tools。支持 Mock 和 Real 两种后端，通过 `ECO_BACKEND` 环境变量切换，LangGraph 和 MCP Inspector 都能直接调用。

> 完整架构、配置文件设计、占位符 Resolve、Wrapper 脚本机制 → 详见 [docs/MCP Server 详细设计.md](docs/MCP%20Server%20详细设计.md)

### 启动方式

```bash
# Mock 模式（默认）— 零依赖，本地调试
ECO_BACKEND=mock python -m src.mcp_server.stdio_runner

# 指定 scenario
ECO_BACKEND=mock ECO_SCENARIO=phase2_sta_error python -m src.mcp_server.stdio_runner

# Real 模式 — 接入真实 EDA，2 个配置文件路径可独立指定
#   config/project.yaml     项目级：step 命令 / timeout / 路径（换工程改一次）
#   config/run_context.yaml 运行级：design / 迭代目录 / db（每次迭代改）
ECO_BACKEND=real \
  PROJECT_CONFIG=config/project.yaml \
  RUN_CONTEXT=config/run_context.yaml \
  python -m src.mcp_server.stdio_runner

# 也可以直接用启动脚本（等价参数：-p / -c）
scripts/start_real.sh

# 用 MCP Inspector 交互式调试（浏览器里能看到所有 10 个 Tool + 参数说明 + 返回值）
npx @modelcontextprotocol/inspector
# Transport 选 stdio，Command 填：python -m src.mcp_server.stdio_runner
```

### Dry-run 模式（快速验证配置）

在调用 RealECOMCPServer.run_step 时加 `dry_run=True`，只打印 resolve 后的完整环境变量和命令，不真正执行。`step_command` 支持字符串（一条命令）或列表（多条命令逐条执行、失败即停）：

```bash
python -c "
from src.mcp_server.real import RealECOMCPServer
srv = RealECOMCPServer()
srv.run_step('run_sta', dry_run=True)
"
```

输出示例：

```
[DRY-RUN] Step: run_sta
  COMMANDS (1 条):
    1. run_sta.csh SOC_XXX_SUB all CTS ptpx -input /data/.../0.outgoing/SOC_XXX_SUB -output /tmpdata/.../pt_rpt
  EXECUTION_DIR = /data/.../2.APR.SOC_XXX_SUB
  WAIT_FILE     = /tmpdata/.../pt_rpt/SOC_XXX_SUB/SOC_XXX_SUB.sta.ok
  LOG_DIR       = /tmpdata/.../pt_rpt/SOC_XXX_SUB
  DESIGN_NAME   = SOC_XXX_SUB
  TIMEOUT       = 7200s
  FIX_STRATEGY  = (none)
```

### RealECOMCPServer 核心 API

```python
from src.mcp_server.real import RealECOMCPServer

srv = RealECOMCPServer()                              # 用默认 2 个 yaml 路径
srv = RealECOMCPServer(
    project_path="config/project.yaml",
    run_context_path="config/run_context.yaml",
)

# dry-run（只看 resolve 结果，不真正执行）
srv.run_step("run_sta", dry_run=True)

# 真执行
result = srv.run_step("run_sta")                      # 返回内置 parser 提取的 dict
result = srv.run_step("run_pt_fix_setup", fix_strategy="setup_via_repair")

# debug 模式（wrapper 会 set -x + 打印所有环境变量）
srv.run_step("run_sta", debug=True)
```

### 10 个 MCP Tools

| Tool | 引擎 | 说明 |
|---|---|---|
| `run_eco_route` | Innovus | ECO 路由初始化 |
| `run_ext` | StarRCX | 寄生参数抽取 |
| `run_sta` | PrimeTime | 静态时序分析，返回 setup/hold violations |
| `run_pv` | Calibre | 物理验证（LVS+DRC） |
| `run_signoff` | Innovus | Signoff 签核检查 |
| `run_pt_fix_setup` | PT (Innovus) | PT 引擎 fix setup |
| `run_pt_fix_hold` | PT (Innovus) | PT 引擎 fix hold |
| `run_pt_fix_leakage` | PT (Innovus) | PT 引擎 fix leakage |
| `run_pt_fix_drv` | PT (Innovus) | PT 引擎 fix DRV |
| `run_xtop_fix_hold` | XTOP | XTOP 引擎 fix hold（PT 的备选/补充） |

Fix 类 Tool 额外接收 `fix_strategy` 参数，MCP Inspector 或 LangGraph 调用时传入。

---

## State 字段

LangGraph ECOState（TypedDict with Annotated reducers）：

| 字段 | 类型 | 含义 | Reducer |
|---|---|---|---|
| `design_name` | str | 当前 Design 名 | `_last_writer_reducer` |
| `run_dir` | str | 当前迭代运行目录 | `_last_writer_reducer` |
| `iteration_cnt` | int | 当前迭代轮次（从 1 开始） | `_last_writer_reducer` |
| `setup_vio` | int | 当前 Setup 违例数 | `_last_writer_reducer` |
| `hold_vio` | int | 当前 Hold 违例数 | `_last_writer_reducer` |
| `pv_pass` | bool | PV 电气校验是否通过 | `_last_writer_reducer` |
| `signoff_pass` | bool | Signoff 签核是否通过 | `_last_writer_reducer` |
| `prev_setup_vio` | int | 上一轮 Setup 违例数（用于收敛对比） | `_last_writer_reducer` |
| `prev_hold_vio` | int | 上一轮 Hold 违例数 | `_last_writer_reducer` |
| `phase_status` | dict[str, str] | 各 Phase 状态（running/done/error） | `_dict_merge_reducer` |
| `step_status` | dict[str, str] | 各 Step 状态（pending/running/done/error） | `_dict_merge_reducer` |
| `current_phase` | str | 当前所在 Phase | `_last_writer_reducer` |
| `current_step` | str | 当前所在 Step | `_last_writer_reducer` |
| `interrupt_msg` | str | interrupt() 输出的暂停提示 | `_last_writer_reducer` |
| `user_fix_strategy` | str | phase2_summary resume 时用户选择的修复策略 | `_last_writer_reducer` |
| `user_iter_choice` | str | phase3_summary resume 时用户选择的迭代决策 | `_last_writer_reducer` |
| `error_msg` | str | error_handler 时的错误描述 | `_last_writer_reducer` |
| `step_elapsed` | dict[str, float] | 各 Step 执行耗时（秒） | `_dict_merge_reducer` |
| `iteration_history` | list[dict] | 各轮迭代违例收敛历史 | `_dict_merge_reducer` |

**Reducer 说明**：Send 并行节点同时写入 state 时，`_dict_merge_reducer` 做字典字段级合并，`_last_writer_reducer` 取最后一个写入者的值。Phase2 并行时 Send arg 只传 shared dict（design_name/run_dir/prev_vio 等共享字段），避免并行节点互相覆盖各自独立更新的字段。

---

## 目录结构

```
eco_agent/
├── config/
│   ├── default.yaml              # 默认配置（LangGraph + 框架级）
│   ├── production.yaml           # 生产环境配置（严格校验）
│   ├── project.yaml              # MCP Server 项目级配置（step 命令/timeout/路径，换工程改）
│   └── run_context.yaml          # MCP Server 运行级配置（design/迭代目录/db，每次迭代改）
├── src/
│   ├── main.py                   # CLI 入口
│   ├── graph_builder.py          # LangGraph 图构建（注册所有节点和边）
│   ├── state.py                  # ECOState TypedDict + Reducer 定义
│   │
│   ├── nodes/                    # 图节点实现
│   │   ├── node_init.py          # 初始化校验节点
│   │   ├── node_phase1.py        # Phase1 串行节点（eco_route / ext）
│   │   ├── node_phase2.py        # Phase2 并行节点（sta / pv / signoff + summary）
│   │   ├── node_phase3.py        # Phase3 分支节点（fix_setup / fix_hold）
│   │   ├── node_gates.py         # 并行结果汇聚节点（phase2_gate / phase3_gate）
│   │   ├── node_error_handler.py # 统一错误处理节点
│   │   ├── node_finalize.py      # 结束收尾节点
│   │   └── step_runner.py        # _run_step 统一辅助函数
│   │
│   ├── routers/
│   │   └── phase_routes.py       # 条件路由函数（route_after_phase2_summary 等）
│   │
│   ├── mcp_server/
│   │   ├── protocol.py           # ECOMCPServer Protocol 接口契约 + STEP_NAMES 注册表
│   │   ├── mock.py               # MockECOMCPServer 实现（7 个 scenario）
│   │   ├── real.py               # RealECOMCPServer 实现（读 2 个 yaml + wrapper）
│   │   ├── parsers.py            # 内置 EDA 日志 parse patterns（用户无需配置）
│   │   ├── mcp_app.py            # FastMCP 注册 10 个 @tool()
│   │   └── stdio_runner.py       # stdio Transport 启动入口
│   │
│   ├── conversation/
│   │   └── agent.py              # agent_entry LLM 对话节点
│   │
│   ├── calling_layer/
│   │   ├── event_loop.py         # 交互式 event_loop（stream → get_state → input）
│   │   └── formatters.py         # 事件/中断/终态格式化输出
│   │
│   └── utils/
│       ├── config_loader.py      # YAML 加载 + deep merge
│       ├── file_utils.py         # 文件系统工具（ensure_dir / validate_design_dir）
│       ├── checkpoint_config.py  # SqliteSaver / MemorySaver 工厂
│       └── constants.py          # STEP_TO_PHASE / VALID_STEP_TARGETS 等常量
│
├── tests/
│   ├── l1_mcp_api/               # Mock Server API 单元测试
│   ├── l2_nodes/                 # 节点 + 路由函数单元测试
│   ├── l3_graph_topology/        # 图拓扑 + Send 并行 + gate 边界
│   ├── l4_interrupt_recovery/    # MemorySaver + SqliteSaver 中断恢复
│   ├── l5_error_handling/        # error_handler + Command(goto)
│   ├── l6_idempotency/           # interrupt rerun + retry 幂等性
│   ├── l7_e2e/                   # 端到端 happy_path + error 场景
│   ├── p1_conversation/          # agent_entry 对话意图解析
│   ├── p2_init_validation/       # init 校验 + file_utils 边界
│   ├── p5_mock_logs/             # Mock Server 日志磁盘写入
│   └── config/                   # YAML 配置加载 + deep merge
│
├── scripts/
│   ├── smoke_benchmark.py        # 快速 smoke test + 耗时统计
│   └── eda/
│       └── run_step_wrapper.csh  # 通用 EDA step 执行 wrapper（所有 10 个 step 共用）
├── docs/
│   ├── MCP Server 详细设计.md     # MCP Server 子系统完整设计（架构/配置/resolve/wrapper）
│   ├── 项目架构设计.md            # LangGraph + Agent 整体架构
│   └── 功能清单.md               # 173 项功能完备性清单
├── pyproject.toml
└── README.md
```

---

## 错误处理

任意节点的 `step_status[step_name]` 被标记为 `"error"` 后，图自动路由到 `error_handler`。

```
正常失败流程：
  run_sta (抛异常) → _run_step 标记 step_status.run_sta=error + phase_status.phase2=error
    → phase2_gate 检测到 error
    → route_after_phase2_gate 返回 "error_handler"
    → error_handler.interrupt(询问 retry/abort)

用户 retry 后：
  error_handler.Command(goto="run_sta", update={重置 step_status/phase_status/error_msg})
    → LangGraph 直接跳到 run_sta（绕过 checkpoint metadata 残留）
    → run_sta 重新执行

用户 abort 后：
  error_handler.Command(goto="finalize", update={重置 step_status/phase_status})
    → 直接跳到 finalize
```

关键设计决策：
- **Gate 节点**统一汇聚 Send 并行结果，消除多目标条件边 race condition
- **Command(goto)** 替代条件边 `route_after_error_handler`，避免 checkpoint metadata 残留导致的额外 interrupt
- **Send arg 限缩**为 shared dict，避免并行节点互相覆盖独立字段

---

## 测试

```bash
# 全量测试 + 覆盖率
python -m pytest tests/ --cov=src --cov-report=term-missing

# 单测覆盖率 XML（CI 用）
python -m pytest tests/ --cov=src --cov-report=xml

# 只跑某一层
python -m pytest tests/l4_interrupt_recovery/ -v

# ResourceWarning 严格模式（CI 推荐）
python -W error::ResourceWarning -m pytest tests/
```

### 当前覆盖情况

| 指标 | 数值 |
|---|---|
| **测试用例总数** | 187 |
| **通过** | 187 (100%) |
| **覆盖率** | **89%** |
| **ResourceWarning** | 0 |

### 分层覆盖目标

| 层级 | 覆盖重点 | 用例数 |
|---|---|---|
| L1 MCP API | 7 个 Step API 的签名/返回/异常 | 19 |
| L2 Nodes | `_run_step` + 路由函数 + 汇总节点 | 16 |
| L3 Topology | 串行/并行/分支/循环图拓扑 | 33 |
| L4 Recovery | MemorySaver + SqliteSaver 中断恢复 | 15 |
| L5 Error | 各 Phase 错误 + retry/abort 完整路径 | 31 |
| L6 Idempotency | interrupt rerun + retry 幂等性 | 5 |
| L7 E2E | happy_path + error + 多轮迭代完整链路 | 9 |
| P1 Conversation | LLM 对话意图识别 | 16 |
| P2 Init | init 校验 + relaxed mode | 26 |
| P5 Logs | Mock Server 日志落磁盘 | 9 |

---

## 设计红线（编码纪律）

| # | 规则 | 原因 |
|---|---|---|
| 1 | `interrupt()` 必须放在节点逻辑**最末端** | LangGraph interrupt 会 rerun 节点前半段，前半段不能有副作用 |
| 2 | `_run_step` 只管**执行 + 标记 state**，不做**路由决策** | 控制流（条件边）和执行流（step 函数）分离 |
| 3 | State 更新用 `{**old, new}` 展开式，不 mutate | LangGraph 并行节点合并 state 依赖不可变 dict |
| 4 | Send arg 只传 **shared dict**，不传 full state | 避免并行节点互相覆盖独立写入的字段 |
| 5 | Gate 节点**必须**在 Send 并行之后 | 确保单一条件路由点，消除多目标 race |
| 6 | error_handler 用 `Command(goto=...)` 自行路由，**不**注册条件边 | 绕开 checkpoint metadata 残留导致的额外 interrupt |
| 7 | LLM 不参与流程决策（不触发条件边跳转） | 流程确定性，可测试可复现 |

---

## 后续扩展

| 方向 | 当前状态 | 说明 |
|---|---|---|
| `run_fix_leakage` | ✅ 已接入 | RealECOMCPServer + wrapper + project.yaml.step_command，与其他 fix 类 step 统一 |
| `run_pt_fix_drv` | ✅ 已接入 | PT 引擎 DRV 修复，新增的第 9 个 step |
| `run_xtop_fix_hold` | ✅ 已接入 | XTOP 引擎 hold 修复，第 10 个 step，与 PT fix 并行的引擎选择 |
| 真实 EDA 接入 | ✅ 已就绪 | RealECOMCPServer 读 2 个 yaml → wrapper 执行 cshell/PDS 命令，只需填充真实路径和 step_command |
| CLI 重构 | 📍 原型 | 当前 argparse 实现，后续可升级为 Typer 增加 `config list` / `thread resume` 等子命令 |
| LLM Gateway | 📍 agent_entry 已集成 | 可扩展为多 LLM 路由（不同意图用不同模型） |
| 指标/监控 | 📍 step_elapsed 已采集 | 接入 Prometheus / Grafana 展示流水线耗时 |

---

## 技术栈

- **LangGraph** ≥ 0.2.0 — 状态图编排框架
- **LangChain** ≥ 0.3.0 — LLM 应用框架
- **SqliteSaver** — checkpoint 持久化
- **Python Protocol** — MCP Server 接口契约
- **pytest** + **pytest-cov** — 测试与覆盖率

---

## License

MIT
