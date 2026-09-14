# MCP Server 详细设计文档

ECO Agent 的 MCP Server 子系统。作为 LangGraph Agent 与 EDA 工具之间的执行层，将 ECO 流水线的 10 个 step 暴露为 MCP Tools，支持 Mock 和 Real 两种后端，通过 stdio Transport 嵌入到 LangGraph 或独立运行。

---

## 1. 设计目标

| # | 目标 | 说明 |
|---|---|---|
| G1 | Mock 也能作为 MCP Server 注册 | MCP Inspector 可直接调 Mock，不依赖 LangGraph 即可调试 |
| G2 | RealECOMCPServer 实现 Protocol 契约即变 MCP Tool | 新增/修改 step 只需改 Protocol + Real + yaml，自动对外暴露 |
| G3 | 与 LangGraph 解耦 | MCP Server 独立 stdio 进程，LangGraph 作为 MCP Client 调用 |
| G4 | 配置驱动 | EDA 工具路径、step_command、超时、日志解析全部 yaml 配置 |
| G5 | 纯粹的 wrapper 方案 | 所有 10 个 step 共用一个通用 cshell wrapper，无独立脚本维护成本 |

非目标（当前不做）：HTTP Transport / Streamable HTTP / 多 MCP Server 进程。

---

## 2. 架构总览

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           MCP Server 架构                                   │
│                                                                             │
│  MCP Client (LangGraph / MCP Inspector / 其他)                              │
│      │                                                                      │
│      │  JSON-RPC over stdio                                                 │
│      ▼                                                                      │
│  ┌──────────────────────────────────┐                                       │
│  │  FastMCP (mcp_app.py)            │                                       │
│  │  ┌────────────────────────────┐  │                                       │
│  │  │  @tool() run_eco_route     │  │                                       │
│  │  │  @tool() run_ext           │  │                                       │
│  │  │  @tool() run_sta           │  │  10 个 Tool 方法                      │
│  │  │  @tool() run_pv            │  │  内部只做委托                          │
│  │  │  @tool() run_signoff       │  │  → eco_server.run_xxx()              │
│  │  │  @tool() run_pt_fix_setup  │  │                                       │
│  │  │  @tool() run_pt_fix_hold   │  │                                       │
│  │  │  @tool() run_pt_fix_leakage│  │                                       │
│  │  │  @tool() run_pt_fix_drv    │  │                                       │
│  │  │  @tool() run_xtop_fix_hold │  │                                       │
│  │  └────────────────────────────┘  │                                       │
│  └──────────────┬───────────────────┘                                       │
│                 │ Protocol 接口调用                                         │
│                 ▼                                                           │
│  ┌─────────────────────────────────────────────┐                             │
│  │  ECOMCPServer (protocol.py)                 │                             │
│  │  ├── MockECOMCPServer                       │                             │
│  │  │     scenario 预设数据，零依赖可调试       │                             │
│  │  └── RealECOMCPServer                       │                             │
│  │        ├─ config/eda_tools.yaml    加载     │                             │
│  │        ├─ config/project.yaml      加载     │                             │
│  │        ├─ config/run_context.yaml  加载     │                             │
│  │        ├─ 多轮 resolve 占位符                │                             │
│  │        ├─ 组 subprocess 环境变量              │                             │
│  │        └─ parse_patterns 提取 violations     │                             │
│  └──────────────┬──────────────────────────────┘                             │
│                 │ subprocess.run                                            │
│                 ▼                                                           │
│  ┌─────────────────────────────────────────────┐                             │
│  │  run_step_wrapper.csh (135 行, 唯一脚本)     │                             │
│  │  1. [可选] DEBUG=1 → set -x + env dump      │                             │
│  │  2. source $PROJECT_CSHRC                   │                             │
│  │  3. cd $EXECUTION_DIR                       │                             │
│  │  4. eval "$CMD"  (step_command 完整命令)     │                             │
│  │  5. tee → 终端 + $LOG_DIR/${step}_${ts}.log │                             │
│  │  6. pds_wait_files $WAIT_FILE               │                             │
│  │  7. [可选] source ${STEP_NAME}_post_check.csh│                             │
│  └─────────────────────────────────────────────┘                             │
│                                                                             │
│  IT + PDS 托底：EDA 工具 PATH、License、关键输入自动处理                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Protocol 契约

`src/mcp_server/protocol.py`：

```python
class ECOMCPServer(Protocol):
    def run_eco_route(self, design_name: str, run_dir: str) -> dict: ...
    def run_ext(self, design_name: str, run_dir: str) -> dict: ...
    def run_sta(self, design_name: str, run_dir: str) -> dict: ...
    def run_pv(self, design_name: str, run_dir: str) -> dict: ...
    def run_signoff(self, design_name: str, run_dir: str) -> dict: ...
    def run_pt_fix_setup(self, design_name: str, run_dir: str, fix_strategy: str) -> dict: ...
    def run_pt_fix_hold(self, design_name: str, run_dir: str, fix_strategy: str) -> dict: ...
    def run_pt_fix_leakage(self, design_name: str, run_dir: str, fix_strategy: str) -> dict: ...
    def run_pt_fix_drv(self, design_name: str, run_dir: str, fix_strategy: str) -> dict: ...
    def run_xtop_fix_hold(self, design_name: str, run_dir: str, fix_strategy: str) -> dict: ...
```

**关键设计决策**：

- **step 命名规则**：`run_{引擎}_{功能}`，如 `run_pt_fix_setup`、`run_xtop_fix_hold`。引擎（pt/xtop）编码进 step 名，不是单独配置项，避免 run_context 里配 fix_engine 这种间接层。
- **xtop_fix_setup**：XTOP 理论上也能修 setup，但目前只用它修 hold，所以只注册 `run_xtop_fix_hold`。
- **run_dir 参数保留**：Protocol 接口必须兼容 LangGraph 的调用方式，即使 RealECOMCPServer 不从 run_dir 推断目录（从 project.yaml.base 推断），参数签名仍保持一致。

---

## 4. 配置文件设计（3 层，按生命周期分层）

```
修改频率 ↓     层名              文件                     内容
─────────────────────────────────────────────────────────────────────────
几乎不改    Layer 1  Step 注册表  config/eda_tools.yaml    timeout_s + parse_patterns
换工程才改  Layer 2  项目级配置   config/project.yaml       project_name + step_command + base + logs + wait_flags
每次迭代改  Layer 3  运行级配置   config/run_context.yaml   design_name + 输入输出 + timeout 覆盖
```

### 4.1 config/eda_tools.yaml（Layer 1，几乎不改）

**职责**：Step 注册表 + 默认 timeout + 日志解析正则。

**不再负责**：工具 binary（IT 托底 PATH）、PDK/License（IT 托底）、step env（PDS 系统自动处理关键输入）、step_command（移到 project.yaml）。

**每个 step 最多 3 个字段**：

| 字段 | 必填 | 说明 |
|---|---|---|
| `timeout_s` | ✅ | 默认超时值，run_context 可覆盖 |
| `parse_patterns` | ❌ | EDA 工具 stdout/stderr 的正则提取规则，PDS 不统计 violations 所以需要我们自己 parse |

```yaml
steps:
  run_eco_route:
    timeout_s: 3600

  run_ext:
    timeout_s: 3600

  run_sta:
    timeout_s: 1800
    parse_patterns:
      setup_vio: "Setup Violations:\\s*(\\d+)"
      hold_vio:  "Hold Violations:\\s*(\\d+)"

  run_pv:
    timeout_s: 3600
    parse_patterns:
      pv_pass: "LVS check.*PASSED"

  run_signoff:
    timeout_s: 3600
    parse_patterns:
      signoff_pass: "Signoff DRC.*PASSED"

  run_pt_fix_setup:
    timeout_s: 1800
    parse_patterns:
      setup_vio: "Setup Violations After Fix:\\s*(\\d+)"

  run_pt_fix_hold:
    timeout_s: 1800
    parse_patterns:
      hold_vio: "Hold Violations After Fix:\\s*(\\d+)"

  run_pt_fix_leakage:
    timeout_s: 1800

  run_pt_fix_drv:
    timeout_s: 1800

  run_xtop_fix_hold:
    timeout_s: 1800
    parse_patterns:
      hold_vio: "Hold Violations After Fix:\\s*(\\d+)"
```

### 4.2 config/project.yaml（Layer 2，换工程改一次）

**职责**：项目身份 + 目录结构 + step_command 命令 + wait_flags 完成标志 + logs 日志目录。

```yaml
project_name: "SOC_XXX_SUB"
work_dir: "/data/SDXXXX/user/w009999"

# 集群调度命令（list，可多个）
dsub_queue:
  - "dsub -A root.xxxx.class -q lang -R 'cpu=8;mem=40000'"

# IT 环境初始化脚本 — wrapper 执行前先 source
project_cshrc: "/data/SDXXXX/common/0.c_shell/project.cshrc"

# ── base — 物理工作目录 ──
# 命名规则：run_{step_name}，引擎级目录（run_pt_fix）承载多 step
# sta 拆成 bin（执行入口）和 rpt（报告输出）
base:
  run_eco_route: "{work_dir}/2.APR.{project_name}"
  run_ext:       "{work_dir}/3.EXT.{project_name}"
  run_sta_bin:   "{base.run_eco_route}/tools/sta"
  run_sta_rpt:   "/tmpdata/SDXXX_PD/user/w009999/pt_rpt"
  run_pv:        "/tmpdata/SDXXX_PD/user/w009999/7.PV"
  run_signoff:   "{base.run_eco_route}"          # 复用 run_eco_route 目录
  run_pt_fix:    "{work_dir}/4.PTECO.{project_name}"
  run_xtop_fix:  "{work_dir}/4.xtop.{project_name}"
  dataout:       "{work_dir}/0.outgoing/{project_name}"

# ── logs — 日志目录 ──
# PT 3 个 fix step 共用 fix_pt/log，靠文件名里的 .setup. .hold. .leakage. 区分
logs:
  run_eco_route:  "{base.run_eco_route}/log"
  run_ext:        "{base.run_ext}/run/LOGS"
  run_sta:        "{base.run_sta_rpt}/{project_name}"
  run_pv:         "{base.run_pv}/log"
  run_signoff:    "{base.run_signoff}/log/signoff_check_*"
  run_pt_fix:     "{base.run_pt_fix}/log"        # setup/hold/leakage/drv 共用
  run_xtop_fix:   "{base.run_xtop_fix}/log"      # xtop fix 共用

# ── wait_flags — 每个 step 的完成标志文件 ──
wait_flags:
  run_eco_route:     "{base.dataout}/{project_name}.def.ok"
  run_ext:           "{base.run_ext}/run/LOGS/{project_name}.spef.smc/LOG/ok.flag"
  run_sta:           "{base.run_sta_rpt}/{project_name}/{project_name}.sta.ok"
  run_pv:            "{base.run_pv}/log/{project_name}.pv.ok"
  run_signoff:       "{base.run_signoff}/log/signoff_check.ok"
  run_pt_fix_setup:  "{base.run_pt_fix}/output/fix_setup/fix_setup.tcl"
  run_pt_fix_hold:   "{base.run_pt_fix}/output/fix_hold/fix_hold.tcl"
  run_pt_fix_leakage:"{base.run_pt_fix}/output/fix_leakage/fix_leakage.tcl"
  run_pt_fix_drv:    "{base.run_pt_fix}/output/fix_drv/fix_drv.tcl"
  run_xtop_fix_hold: "{base.run_xtop_fix}/output/xtop_fix_hold.tcl"

# ── step_command — 每个 step 的实际调用命令 ──
# Python resolve 占位符后 → $CMD 环境变量 → wrapper eval "$CMD"
# 支持 shell 语法（source、管道、重定向）
step_command:
  run_eco_route:        "pds_innovus 6.ecoRt"
  run_ext:              "pds_starrc DEF_SMC"
  run_sta:              "run_sta.csh {project_name} all CTS ptpx -input {base.dataout} -output {base.run_sta_rpt}"
  run_pv:               "source 100P_PV.csh {design_name}"
  run_signoff:          "pds_innovus 6-2.signoff_check"
  run_pt_fix_setup:     "pds_pteco setup"
  run_pt_fix_hold:      "pds_pteco hold"
  run_pt_fix_leakage:   "pds_pteco leakage"
  run_pt_fix_drv:       "pds_pteco ldrc"
  run_xtop_fix_hold:    "source run_fix_hold.csh {design_name}"
```

### 4.3 config/run_context.yaml（Layer 3，每次迭代改）

**职责**：本轮迭代的 design 身份 + 输入输出文件名 + timeout 覆盖。

```yaml
design_name: "{project_name}"           # 默认等于 project_name，sub-design 时覆盖
execution_dir: "{base.run_eco_route}"   # wrapper 执行时 cd 到这里

# 本轮输入/输出（每次迭代文件名变，目录引用 project.yaml.base）
preco_db:      "{base.run_eco_route}/DB/{design_name}_fixhold.enc"
preco_db_next: "{base.run_eco_route}/DB/{design_name}_fixhold_fixsetup.enc"
eco_scripts:   "{base.run_pt_fix}/output/setup.tcl"

# timeout 覆盖（只配需要覆盖的 step，不配的自动继承 eda_tools 默认值）
execution:
  run_sta:
    timeout_s: 7200
  run_pt_fix_setup:
    timeout_s: 3600
```

### 4.4 三个 yaml 的 step 必须对齐

所有三个 yaml 的 step 键集合必须完全一致，Python 启动时会检查：

```python
tools_steps = set(eda_tools["steps"].keys())
cmd_steps = set(project["step_command"].keys())
wait_steps = set(project["wait_flags"].keys())
# 三者的 symmetric_difference 必须为空集
```

---

## 5. 占位符 Resolve 机制

### 5.1 支持的占位符

| 占位符 | 来源 | 可用范围 |
|---|---|---|
| `{project_name}` | project.yaml 顶层 | 两个文件 |
| `{work_dir}` | project.yaml 顶层 | 两个文件 |
| `{base.xxx}` | project.yaml.base | 两个文件 |
| `{logs.xxx}` | project.yaml.logs | 两个文件 |
| `{wait_flags.xxx}` | project.yaml.wait_flags | 两个文件 |
| `{step_command.xxx}` | project.yaml.step_command | 两个文件 |
| `{design_name}` | run_context.yaml | run_context + step_command（resolve 时自动注入） |
| `{fix_strategy}` | MCP Tool 调用时传入 | step_command（运行时替换） |

### 5.2 Resolve 顺序（多轮迭代直到稳定）

```
加载顺序 + resolve 顺序：

① eda_tools.yaml              → step 注册表，无占位符
② project.yaml                → 内部多轮迭代 resolve
     flat = {project_name, work_dir, project_cshrc}   ← 先塞顶层
     flat["base"] = {}                                    ← 空壳，增量更新
     flat["logs"] = {}
     flat["wait_flags"] = {}
     flat["step_command"] = {}

     第 1 轮：resolve base.run_eco_route = "{work_dir}/2.APR.{project_name}"
             → flat["base"]["run_eco_route"] = "/data/.../2.APR.SOC_XXX_SUB"
     第 2 轮：resolve base.run_signoff = "{base.run_eco_route}"
             → 用第 1 轮已 resolve 的值展开
     第 3 轮：resolve logs / wait_flags / step_command
     ... 最多 10 轮，直到没有 {} 占位符残留

③ run_context.yaml            → 引用 project resolve 后的值
     ctx_flat = {project_name, base, design_name}
     resolve preco_db, execution_dir 等
```

**Python 关键实现**（`RealECOMCPServer._resolve_project`）：

```python
for _ in range(10):
    changed = False
    for k, v in list(base.items()):
        new_val = self._resolve_one(str(v), flat)   # 每 resolve 完一个，立即更新 flat["base"][k]
        flat["base"][k] = new_val
        if new_val != str(v):
            changed = True
    # logs / wait_flags / step_command 同理
    if not changed:
        break
```

每 resolve 完一个字段立即更新 `flat`，下一轮其他字段就能引用到它——这是处理 `base.run_signoff → {base.run_eco_route}` 这类嵌套引用的核心。

### 5.3 Resolve 示例

```yaml
# project.yaml
base:
  run_eco_route: "{work_dir}/2.APR.{project_name}"       # 第 1 轮 resolve
  run_signoff:   "{base.run_eco_route}"                  # 第 2 轮引用第 1 轮结果

# resolve 后（两轮迭代）
base.run_eco_route = /data/SDXXXX/user/w009999/2.APR.SOC_XXX_SUB
base.run_signoff   = /data/SDXXXX/user/w009999/2.APR.SOC_XXX_SUB   ✅ 完整展开
```

---

## 6. 执行模型

### 6.1 RealECOMCPServer.run_step 流程

```
RealECOMCPServer.run_step("run_sta")
    │
    ├─ 1. 校验 step 注册
    │     assert step in eda_tools["steps"]
    │     assert step in project["step_command"]
    │
    ├─ 2. resolve step_command 占位符
    │     cmd_template = project_resolved["step_command"]["run_sta"]
    │                    → "run_sta.csh SOC_XXX_SUB all CTS ptpx -input /data/.../0.outgoing/... -output /tmpdata/.../pt_rpt"
    │
    ├─ 3. 组 subprocess 环境变量
    │     env = os.environ.copy()         ← IT 托底的 PATH, EDA_HOME 等
    │     env["PROJECT_CSHRC"] = ...      ← project_cshrc
    │     env["STEP_NAME"] = "run_sta"
    │     env["DESIGN_NAME"] = "SOC_XXX_SUB"
    │     env["EXECUTION_DIR"] = ...      ← 从 base 推断
    │     env["WAIT_FILE"] = ...          ← wait_flags.run_sta
    │     env["CMD"] = cmd_template       ← 完整命令
    │     env["LOG_DIR"] = ...            ← logs.run_sta
    │     env["FIX_STRATEGY"] = ...        ← 如果是 fix 类 step
    │     env["DEBUG"] = "1"              ← 如果 debug=True
    │
    ├─ 4. dry-run 分支
    │     if dry_run: 打印所有环境变量 + timeout + parse_cfg，返回（不执行）
    │
    ├─ 5. subprocess.run
    │     timeout = run_context 覆盖 or eda_tools 默认
    │     result = subprocess.run(
    │         ["/bin/csh", "-f", "scripts/eda/run_step_wrapper.csh"],
    │         env=env, capture_output=True, timeout=timeout
    │     )
    │
    ├─ 6. 错误处理
    │     TimeoutExpired → 抛明确错误（EDA 工具卡住/license/网络慢）
    │     returncode != 0 → 抛明确错误（CMD/EXECUTION_DIR/WAIT_FILE + STDERR tail 2000 字）
    │
    └─ 7. parse_patterns 提取
          patterns = eda_tools["steps"]["run_sta"]["parse_patterns"]
          result = _parse_output(stdout + stderr, patterns)
          → {"setup_vio": 42, "hold_vio": 7}
```

### 6.2 Execution_dir 推断优先级

```
run_context.execution_dir  >  project.base.{step_name}_bin  >  project.base.{step_name}  >  ""
```

例：run_sta 优先 run_context.execution_dir，没配则用 base.run_sta_bin（脚本入口），再没则用 base.run_sta（报告目录）。

### 6.3 Log_dir 推断（引擎级 fallback）

```
直接匹配 logs.{step_name}
  ↓ 没匹配到
run_pt_fix_* → logs.fix_pt          （PT fix 共用一个日志目录）
run_xtop_fix_* → logs.fix_xtop      （XTOP fix 共用一个日志目录）
  ↓ 都没匹配到
"" （wrapper 不写日志文件，只输出终端）
```

---

## 7. Wrapper 脚本设计

`scripts/eda/run_step_wrapper.csh`，135 行，所有 10 个 step 共用。

### 7.1 内部执行流程

```
1. [可选] Debug 模式开关
   if DEBUG=1 → set -x + 打印所有环境变量

2. 必需环境变量校验
   STEP_NAME 和 CMD 缺失 → 提前报错，信息比 eval 挂了清晰

3. Source IT 环境
   if PROJECT_CSHRC 存在 → source 它
   else → WARNING（允许继续，开发调试用）

4. Cd 到执行目录
   if EXECUTION_DIR 存在 → cd
   else → WARNING，fallback 当前目录

5. 开始日志（带时间戳）
   LOG_DIR 存在 → tee 同时输出终端 + ${LOG_DIR}/${STEP_NAME}_${ts}.log
   LOG_DIR 不存在 → 只输出终端

6. 执行命令
   eval "$CMD"
   set cmd_status = $status

7. 命令失败直接退出
   if cmd_status != 0 → 打印 CMD/EXECUTION_DIR/WAIT_FILE + log 文件路径 → exit $cmd_status

8. PDS wait 检查
   if WAIT_FILE 存在且不为空 → pds_wait_files "$WAIT_FILE"
   失败 → exit 1
   没配 WAIT_FILE → 跳过（纯 pds 命令内部可能托底了）

9. Post-check 钩子（可选）
   if scripts/eda/${STEP_NAME}_post_check.csh 存在 → source 它
   失败 → exit 1
   不存在 → 跳过（绝大多数 step 不会有这个钩子）

10. 成功结束
    打印 START/END 时间戳 + log 文件路径 → exit 0
```

### 7.2 环境变量定义

| 变量 | 必填 | 来源 | 说明 |
|---|---|---|---|
| `PROJECT_CSHRC` | ✅ | project.yaml.project_cshrc | IT 环境初始化脚本，wrapper source |
| `STEP_NAME` | ✅ | step 名（如 run_sta） | 日志标签、post_check 钩子命名 |
| `DESIGN_NAME` | ✅ | run_context.design_name | 传递给 PDS 命令 |
| `EXECUTION_DIR` | ✅ | base 推断 / run_context.execution_dir | wrapper cd 到这里 |
| `WAIT_FILE` | ✅ | project.yaml.wait_flags | pds_wait_files 检查的完成标志 |
| `CMD` | ✅ | project.yaml.step_command resolve 后 | wrapper eval 执行的完整命令 |
| `LOG_DIR` | ❌ | logs 推断 | 同时输出到终端和文件 |
| `FIX_STRATEGY` | ❌ | MCP Tool 调用时传入 | fix 类 step 专用 |
| `DEBUG` | ❌ | RealECOMCPServer(debug=True) | set -x + env dump |
| `STEP_SCRIPTS_DIR` | ❌ | 默认 `scripts/eda` | post_check 钩子目录覆盖 |

### 7.3 三个 Debug 增强（零成本）

1. **DEBUG=1 → set -x + env dump**：shell 打印每一行执行的命令，类似 bash -x
2. **自动日志存储**：wrapper 用 tee 同时输出终端和 `$LOG_DIR/${STEP_NAME}_${ts}.log`
3. **Post-check 钩子**：如果 `scripts/eda/${STEP_NAME}_post_check.csh` 存在就 source，不存在跳过（绝大多数 step 不需要，只有需要定制错误检查的 step 才加）

### 7.4 为什么用 `eval`？

step_command 里可能包含：
- `source 100P_PV.csh {design_name}` （shell source 语法）
- `pds_innovus 6.ecoRt -flag1 -flag2 -opt` （多个参数）
- 管道、重定向、复杂引号组合

csh 里 `$CMD` 作为变量不会解析这些语法，必须 `eval "$CMD"` 才能正确展开。

---

## 8. parse_patterns 设计

### 8.1 为什么需要

EDA 工具（PrimeTime、Calibre、StarRCX）输出的 stdout/stderr 是自由文本格式（人眼可读，程序不可读）。需要用正则提取成结构化 dict 返回给 LangGraph。

**PDS 系统目前不能自动统计 violations**，所以 parse_patterns 必须保留。

### 8.2 设计决策

- **正则写在 yaml 里**：EDA 工具升级时日志格式可能变，改 yaml 不改代码即可适配
- **Python 侧 _coerce 自动类型转换**：
  - `"42"` → `42`（int）
  - `"0.837"` → `0.837`（float）
  - `"PASSED"` → `True`（bool，不区分大小写）
  - 其他 → 保留字符串原值
- **只取第一个捕获组**：正则里 `(\\d+)` 或 `(.*?)` 的 group(1)

### 8.3 返回值结构

MCP Tool 返回给 LangGraph 的 dict（RealECOMCPServer 的 Protocol 方法里做了二次包装）：

```python
def run_sta(self, design_name: str, run_dir: str) -> dict:
    res = self.run_step("run_sta")
    return {
        "setup_vio": res.get("setup_vio", 0),
        "hold_vio": res.get("hold_vio", 0),
    }
```

---

## 9. Transport 选择

**仅支持 stdio**（本地嵌入模式）：

```python
# 直接启动 stdio 模式服务
async def main():
    mcp = create_mcp_app(RealECOMCPServer())
    await mcp.run_stdio()
```

选择 stdio 的原因：
- ECO Agent 与 LangGraph 内部嵌入通信，不需要跨进程网络
- stdio 无端口占用、无防火墙配置、最简单可靠
- HTTP/Streamable HTTP 暂不需要（G2 暂时不做）

---

## 10. 启动入口

`src/mcp_server/stdio_runner.py`：

```python
ECO_BACKEND=mock python -m src.mcp_server.stdio_runner              # 默认 Mock

ECO_BACKEND=real \
  EDA_TOOLS_PATH=config/eda_tools.yaml \
  PROJECT_CONFIG=config/project.yaml \
  RUN_CONTEXT=config/run_context.yaml \
  python -m src.mcp_server.stdio_runner
```

3 个环境变量覆盖默认 yaml 路径，方便不同项目复用同一套代码。

---

## 11. 版本历史

| 版本 | 日期 | 改动 |
|---|---|---|
| v1 | 2026-09-14 | 初始版本。3 yaml 分层 + wrapper 方案，10 个 step 注册完成 |
