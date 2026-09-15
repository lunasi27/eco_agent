# MCP Server 详细设计文档

ECO Agent 的 MCP Server 子系统。作为 LangGraph Agent 与 EDA 工具之间的执行层，将 ECO 流水线的 10 个 step 暴露为 MCP Tools，支持 Mock 和 Real 两种后端，通过 stdio Transport 嵌入到 LangGraph 或独立运行。

---

## 1. 设计目标

| # | 目标 | 说明 |
|---|---|---|
| G1 | Mock 也能作为 MCP Server 注册 | MCP Inspector 可直接调 Mock，不依赖 LangGraph 即可调试 |
| G2 | RealECOMCPServer 实现 Protocol 契约即变 MCP Tool | 新增/修改 step 只需改 Protocol + Real + yaml，自动对外暴露 |
| G3 | 与 LangGraph 解耦 | MCP Server 独立 stdio 进程，LangGraph 作为 MCP Client 调用 |
| G4 | 配置驱动 | step_command、超时、目录全部 yaml 配置；EDA 日志解析正则内置在代码（用户不需要了解工具日志格式） |
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
│  │        ├─ config/project.yaml      加载     │                             │
│  │        ├─ config/run_context.yaml  加载     │                             │
│  │        ├─ 多轮 resolve 占位符                │                             │
│  │        ├─ 命令写入临时 CMDS_FILE             │                             │
│  │        ├─ 组 subprocess 环境变量              │                             │
│  │        └─ parsers.py 内置正则提取 violations │                             │
│  └──────────────┬──────────────────────────────┘                             │
│                 │ subprocess.run                                            │
│                 ▼                                                           │
│  ┌─────────────────────────────────────────────┐                             │
│  │  run_step_wrapper.csh (279 行, 唯一脚本)     │                             │
│  │  1. [可选] DEBUG=1 → set -x + env dump      │                             │
│  │  2. source $PROJECT_CSHRC                   │                             │
│  │  3. cd $EXECUTION_DIR                       │                             │
│  │  4. 逐行 eval $CMDS_FILE（fail-fast）        │                             │
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

- **STEP_NAMES 单一注册表**：10 个 step 名集中在 `protocol.py` 的 `STEP_NAMES` tuple，配置校验、wrapper、parser 都以它为准，新增 step 先改这里。
- **step 命名规则**：`run_{引擎}_{功能}`，如 `run_pt_fix_setup`、`run_xtop_fix_hold`。引擎（pt/xtop）编码进 step 名，不是单独配置项，避免 run_context 里配 fix_engine 这种间接层。
- **xtop_fix_setup**：XTOP 理论上也能修 setup，但目前只用它修 hold，所以只注册 `run_xtop_fix_hold`。
- **run_dir 参数保留**：Protocol 接口必须兼容 LangGraph 的调用方式，即使 RealECOMCPServer 不从 run_dir 推断目录（从 project.yaml.base 推断），参数签名仍保持一致。

---

## 4. 配置文件设计（2 层，按生命周期分层）

```
修改频率 ↓     层名            文件                     内容
─────────────────────────────────────────────────────────────────────────
换工程才改  Layer 1  项目级配置  config/project.yaml     project_name + step_command + timeout_s
                                                         + base + logs + wait_flags
每次迭代改  Layer 2  运行级配置  config/run_context.yaml  design_name + execution_dir + 输入输出文件
```

**分层原则**：

- 同一语义字段只存一处，没有覆盖层级。timeout 曾经支持 run_context 覆盖，后来判断「超时随项目走，大 design 调大，不随迭代变」，于是只留 project.yaml 一处。
- 用户配置里不出现 EDA 工具知识：日志长什么样（parse 正则）由 `src/mcp_server/parsers.py` 内置，工具升级改代码不改用户配置；工具 binary / PDK / License 由 IT 的 cshrc 与 PDS 系统托底，不进配置。
- 每个 step 的键集合以 `STEP_NAMES` 为准，启动期校验。

### 4.1 config/project.yaml（Layer 1，换工程改一次）

**职责**：项目身份 + 目录结构 + step_command 命令 + 每 step 超时 + wait_flags 完成标志 + logs 日志目录。

```yaml
project_name: "SOC_XXX_SUB"
work_dir: "/data/SDXXXX/user/w009999"

# 集群调度命令（list，可多个）
dsub_queue:
  - "dsub -A root.xxxx.class -q lang -R 'cpu=8;mem=40000'"

# IT 环境初始化脚本 — wrapper 执行前先 source
project_cshrc: "/data/SDXXXX/common/0.c_shell/project.cshrc"

# ── base — 物理工作目录（支持 {base.xxx} 链式引用，多轮 resolve）──
base:
  run_eco_route:    "{work_dir}/2.APR.{project_name}"
  run_ext:          "{work_dir}/3.EXT.{project_name}"
  run_sta_bin:      "{base.run_eco_route}/tools/sta"
  run_sta_rpt:      "/tmpdata/SDXXX_PD/user/w009999/pt_rpt"
  run_pv:           "/tmpdata/SDXXX_PD/user/w009999/7.PV"
  run_signoff:      "{base.run_eco_route}"          # 复用 run_eco_route 目录
  run_pt_fix:       "{work_dir}/4.PTECO.{project_name}"
  run_xtop_fix:     "{work_dir}/4.xtop.{project_name}"
  dataout:          "{work_dir}/0.outgoing/{project_name}"

# ── logs — 日志目录（PT/XTOP fix 是引擎级目录，多 step 共用，靠文件名区分）──
logs:
  run_eco_route:    "{base.run_eco_route}/log"
  run_ext:          "{base.run_ext}/run/LOGS"
  run_sta:          "{base.run_sta_rpt}/{project_name}"
  run_pv:           "{base.run_pv}/log"
  run_signoff:      "{base.run_signoff}/log/signoff_check_*"
  run_pt_fix:       "{base.run_pt_fix}/log"         # setup/hold/leakage/drv 共用
  run_xtop_fix:     "{base.run_xtop_fix}/log"

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

# ── step_command — 每个 step 的实际调用命令（str 或 list 两种写法）──
step_command:
  # 单条命令：字符串
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

  # 多条命令：list，逐行执行、fail-fast（详见第 6/7 节）
  # run_sta:
  #   - "mkdir -p {base.run_sta_rpt}/{project_name}"
  #   - "run_sta.csh {project_name} all CTS ptpx -input {base.dataout} -output {base.run_sta_rpt}"

# ── timeout_s — 每个 step 的超时秒数（唯一存放处，未配则代码兜底 3600s）──
timeout_s:
  run_eco_route:      3600
  run_ext:            3600
  run_sta:            7200
  run_pv:             3600
  run_signoff:        3600
  run_pt_fix_setup:   3600
  run_pt_fix_hold:    1800
  run_pt_fix_leakage: 1800
  run_pt_fix_drv:     1800
  run_xtop_fix_hold:  1800
```

**启动期校验**（`_validate_step_commands`，fail fast 在启动而非第一次调用）：

- `step_command` / `timeout_s` 出现 `STEP_NAMES` 之外的未知 step → 报错；
- step_command 为空字符串、空 list、list 里有空 item → 报错；
- timeout 不是正整数 → 报错。

### 4.2 config/run_context.yaml（Layer 2，每次迭代改）

**职责**：本轮迭代的 design 身份 + 执行目录 + 输入输出文件名。只有文件名随迭代变，目录一律引用 `{base.xxx}`。

```yaml
# Design 身份（默认等于 project_name，sub-design 时覆盖）
design_name: "{project_name}"

# wrapper 执行时 cd 到这里
execution_dir: "{base.run_eco_route}"

# 本轮 ECO 迭代的输入/输出文件
preco_db:      "{base.run_eco_route}/DB/{design_name}_fixhold.enc"
preco_db_next: "{base.run_eco_route}/DB/{design_name}_fixhold_fixsetup.enc"
eco_scripts:   "{base.run_pt_fix}/output/setup.tcl"
```

### 4.3 两个 yaml 与 STEP_NAMES 的对齐

`STEP_NAMES`（protocol.py）是 step 的唯一注册表，project.yaml 的 step 键必须与它对齐：

```python
unknown = (cmd_steps | timeout_steps) - set(STEP_NAMES)   # 不允许出现未知 step
missing = set(STEP_NAMES) - cmd_steps                     # step_command 不允许缺 step
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
| `{design_name}` | run_context.yaml 顶层 | project.yaml（含 step_command）+ run_context |
| `{fix_strategy}` | MCP Tool 调用时传入 | step_command（运行时替换） |

### 5.2 Resolve 顺序（design_name 先行，多轮迭代直到稳定）

```
① 加载两个 yaml，先算 design_name
     design_name = run_context.design_name，其中 {project_name} 立即展开
     （旧实现先 resolve project 再算 design_name，导致 step_command 里的
      {design_name} 解析为空；v2 修正为 design_name 先行）

② project.yaml
     flat = {project_name, work_dir, project_cshrc, design_name}  ← design_name 已就位
     flat["base"] / flat["logs"] / flat["wait_flags"] / flat["step_command"] = {} 空壳
     每个字段 resolve 完立即写回 flat，多轮迭代（最多 10 轮）：
       第 1 轮：base.run_eco_route = "{work_dir}/2.APR.{project_name}"
       第 2 轮：base.run_signoff  = "{base.run_eco_route}"   ← 引用第 1 轮结果
       第 3 轮：logs / wait_flags / step_command，含 {design_name}
     fix_strategy 是「自引用占位」：flat["fix_strategy"] = "{fix_strategy}"，
     resolve 时识别到引用自身则原样保留，等 run_step(fix_strategy=...) 运行时再替换

③ run_context.yaml
     ctx_flat 含 project_name / work_dir / design_name / base / logs / ...
     resolve execution_dir、preco_db 等（漏 work_dir 会让 {work_dir} 展开为空，
     进而错误触发 base fallback —— 这是踩过的坑）
```

**Python 关键实现**（`RealECOMCPServer._resolve_project` / `_resolve_one`）：

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

每 resolve 完一个字段立即更新 `flat`，下一轮其他字段就能引用到它——这是处理 `base.run_signoff → {base.run_eco_route}` 这类嵌套引用的核心。`_resolve_one` 对引用自身的占位符（`{fix_strategy}`）原样保留，不在启动期报 unresolved。

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

`step_command` 里 `{design_name}` 在第②步即可展开（如 `source 100P_PV.csh SOC_XXX_SUB`），有专门的回归测试守护。

---

## 6. 执行模型

### 6.1 RealECOMCPServer.run_step 流程

```
RealECOMCPServer.run_step("run_sta")
    │
    ├─ 1. 校验 step 注册（构造函数里已对配置做启动期校验，这里再防直接调用）
    │     assert step in STEP_NAMES
    │
    ├─ 2. resolve step_command 占位符，归一化为 list
    │     str  → [str]；list 原样
    │     {fix_strategy} 此时用入参替换；残留任何 {...} 占位符 → 直接报错
    │     commands = [
    │       "run_sta.csh SOC_XXX_SUB all CTS ptpx -input /data/.../0.outgoing/... -output /tmpdata/.../pt_rpt"
    │     ]
    │
    ├─ 3. 写临时命令文件（方案 B，见 6.4）
    │     tempfile.NamedTemporaryFile(prefix="eco_run_sta_", suffix=".cmds")
    │     每条命令一行；env["CMDS_FILE"] 指向它
    │
    ├─ 4. 组 subprocess 环境变量
    │     env = os.environ.copy()         ← IT 托底的 PATH, EDA_HOME 等
    │     env["PROJECT_CSHRC"] = ...      ← project_cshrc
    │     env["STEP_NAME"] = "run_sta"
    │     env["CMDS_FILE"] = /tmp/eco_run_sta_xxxx.cmds
    │     env["DESIGN_NAME"] = "SOC_XXX_SUB"
    │     env["EXECUTION_DIR"] = ...      ← 从 base 推断
    │     env["WAIT_FILE"] = ...          ← wait_flags.run_sta
    │     env["LOG_DIR"] = ...            ← logs.run_sta
    │     env["FIX_STRATEGY"] = ...        ← 如果是 fix 类 step
    │     env["DEBUG"] = "1"              ← 如果 debug=True
    │
    ├─ 5. dry-run 分支
    │     if dry_run: 打印 COMMANDS (N 条) + 全部环境变量 + timeout，返回（不执行、不落临时文件）
    │
    ├─ 6. subprocess.run
    │     timeout = project.timeout_s[step]，未配兜底 DEFAULT_TIMEOUT_S=3600
    │     csh_bin = os.environ.get("ECO_CSH_BIN", "/bin/csh")   ← 便于无 /bin/csh 的环境测试
    │     result = subprocess.run(
    │         [csh_bin, "-f", "scripts/eda/run_step_wrapper.csh"],
    │         env=env, capture_output=True, text=True, timeout=timeout
    │     )
    │
    ├─ 7. 错误处理 + 清理
    │     finally: 删除临时 .cmds 和 .cmds.status 文件
    │     TimeoutExpired → 抛明确错误（EDA 工具卡住/license/网络慢）
    │     returncode != 0 → 抛明确错误（COMMANDS 全文/EXECUTION_DIR/WAIT_FILE
    │                        + STDOUT tail + STDERR tail 各 2000 字）
    │
    └─ 8. 内置 parser 提取
          result = parse_output("run_sta", stdout + "\n" + stderr)
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
run_pt_fix_* → logs.run_pt_fix     （PT fix 共用一个日志目录）
run_xtop_fix_* → logs.run_xtop_fix （XTOP fix 共用一个日志目录）
  ↓ 都没匹配到
"" （wrapper 不写日志文件，只输出终端）
```

### 6.4 多命令传递：为什么是临时文件（方案 B）而不是环境变量

step_command 支持 str（单条）和 list（多条）。多条命令最初考虑过拼成一条用 `;` / `&&` 串接塞进 `$CMD`，最终选择**逐行写入临时命令文件**：

| 关注点 | 环境变量塞整条串 | 临时文件 CMDS_FILE（采用） |
|---|---|---|
| fail-fast | 依赖 csh 的 `&&`，且每条命令的 shell 语法容易和连接符打架 | wrapper 逐行 `eval`，取 `$status`，非零立即停，报 `command i/N FAILED` |
| 引号/特殊字符 | Python 拼串 + csh 再 eval，两层转义 | 每行原样落盘，wrapper `sed -n "${i}p"` 取出，只有一层 eval |
| 可观测 | dry-run / 报错里是一长串 | 天然带编号 `1. 2. 3.`，日志和异常里明确第几条失败 |
| 长度限制 | 受 env 长度限制 | 只传一个文件路径 |

约定：每行一条命令，不支持反斜杠续行，空行跳过。临时文件 `tempfile.NamedTemporaryFile(prefix=f"eco_{step}_", suffix=".cmds")`，执行完在 finally 里连同 `.status` 文件一起删除。

---

## 7. Wrapper 脚本设计

`scripts/eda/run_step_wrapper.csh`，279 行，所有 10 个 step 共用。已用传统 BSD csh（比 tcsh 语法更严格）跑过集成测试。

### 7.1 内部执行流程

```
1. 必需变量校验 + 可选变量补默认空值
   STEP_NAME、CMDS_FILE（必须存在且可读）缺失 → 提前报错
   PROJECT_CSHRC / DESIGN_NAME / EXECUTION_DIR / WAIT_FILE / LOG_DIR /
   FIX_STRATEGY / DEBUG / STEP_SCRIPTS_DIR 全部 setenv "" 兜底
   ⚠ 传统 csh 的 && 不做短路求值，不能写 "$?X && $X == ..."，
     必须先保证变量存在再比较值（踩过的坑，tcsh 上能过、bsd-csh 上报 Undefined variable）

2. [可选] DEBUG=1 → set -x + env dump

3. Source IT 环境：PROJECT_CSHRC 存在才 source，否则 WARNING 继续

4. Cd 到执行目录：EXECUTION_DIR 存在才 cd，否则 WARNING，fallback 当前目录

5. 逐行执行命令（while + sed 取行，顺序 + fail-fast）
   total = `wc -l < $CMDS_FILE`；set cur_cmd = "`sed -n "${i}p" "$CMDS_FILE"`"
   空行跳过；每条打印 command i/N 分隔头
   退出码用子 shell + status 文件取回（见 7.3）
   cmd_status != 0 → 打印 command i/N FAILED / Exit code / Command / log 路径 → exit

6. PDS wait 检查（所有命令成功之后）
   WAIT_FILE 非空 → pds_wait_files "$WAIT_FILE"，失败 exit 1；空 → 跳过

7. Post-check 钩子（可选）
   ${STEP_SCRIPTS_DIR:-scripts/eda}/${STEP_NAME}_post_check.csh 存在才 source

8. 成功结束：START/END 时间戳 + Design + log 路径 → exit 0
```

### 7.2 环境变量定义

| 变量 | 必填 | 来源 | 说明 |
|---|---|---|---|
| `STEP_NAME` | ✅ | step 名（如 run_sta） | 日志标签、post_check 钩子命名 |
| `CMDS_FILE` | ✅ | Python 写的临时命令文件路径 | 每行一条命令，wrapper 逐行 eval |
| `PROJECT_CSHRC` | 建议 | project.yaml.project_cshrc | IT 环境初始化脚本，wrapper source |
| `DESIGN_NAME` | ❌ | run_context.design_name | 传递给 PDS 命令（空则兜底） |
| `EXECUTION_DIR` | ❌ | base 推断 / run_context.execution_dir | wrapper cd 到这里 |
| `WAIT_FILE` | ❌ | project.yaml.wait_flags | pds_wait_files 检查的完成标志 |
| `LOG_DIR` | ❌ | logs 推断 | 同时输出到终端和文件 |
| `FIX_STRATEGY` | ❌ | MCP Tool 调用时传入 | fix 类 step 专用 |
| `DEBUG` | ❌ | RealECOMCPServer(debug=True) | set -x + env dump |
| `STEP_SCRIPTS_DIR` | ❌ | 默认 `scripts/eda` | post_check 钩子目录覆盖 |

### 7.3 tee 吞退出码：子 shell + status 文件

`eval "$cmd" |& tee log` 之后父 shell 的 `$status` 是 **tee 的退出码（恒 0）**，拿不到命令真实退出码。解法是在子 shell 里执行并把 `$status` 落盘，父 shell 读回：

```csh
( eval "$cur_cmd" ; echo "$status" > "$status_file" ) |& tee -a "$log_file"
if (-f "$status_file") then
    set cmd_status = `cat "$status_file"`
    rm -f "$status_file"
else
    set cmd_status = 1      # 子 shell 被 kill 等极端情况，按失败处理
endif
```

`status_file="${CMDS_FILE}.status"`。不开日志（无 tee）时直接 `eval "$cur_cmd"; set cmd_status = $status`。pds_wait_files 和 post_check 钩子同样使用这套模式。

### 7.4 为什么逐行 `eval`？

step_command 里可能包含：
- `source 100P_PV.csh {design_name}` （shell source 语法）
- `pds_innovus 6.ecoRt -flag1 -flag2 -opt` （多个参数）
- 管道、重定向、复杂引号组合

csh 里变量展开不会解析这些 shell 语法，必须 `eval "$cur_cmd"`。Python 侧已经把占位符全部 resolve 完，wrapper 只做一层 eval，不存在二次转义。注意 csh 命令替换只认反引号（`` `pwd` ``），不认 bash 的 `$(pwd)`。

---

## 8. 日志解析设计（src/mcp_server/parsers.py）

### 8.1 为什么需要

EDA 工具（PrimeTime、Calibre、StarRCX）输出的 stdout/stderr 是自由文本格式（人眼可读，程序不可读）。需要用正则提取成结构化 dict 返回给 LangGraph。

**PDS 系统目前不能自动统计 violations**，所以解析必须保留。

### 8.2 为什么内置在代码而不是 yaml

parse 正则描述的是「某个 EDA 工具版本的日志长什么样」，随工具版本走，**不随项目、不随迭代变**——它是工具知识，不是用户配置。v1 把它放在 `eda_tools.yaml` 里，要求每个使用者理解正则，v2 收进 `parsers.py`：

- 用户 yaml 里不再出现任何正则；
- 工具升级日志格式变化时，改代码 + 加测试即可，用户配置零感知；
- 后续正则不够用时，可把 dict 的值从正则字符串换成 parser 函数；PDS 将来能直接给结构化输出时，改为读 PDS 输出。

`STEP_PARSE_PATTERNS` 当前覆盖 6 个 step：run_sta、run_pv、run_signoff、run_pt_fix_setup、run_pt_fix_hold、run_xtop_fix_hold（其余 step 返回 `{}`）。

### 8.3 parse_output 与类型转换

```python
parse_output(step_name, text) -> dict
```

- 正则带捕获组：取 group(1)，经 `coerce_value` 转换：
  - `"42"` → `42`（int），`"0.837"` → `0.837`（float）
  - `true/yes/pass/passed/success/ok` → `True`；`false/no/fail/failed/error` → `False`（忽略大小写）
  - 其他 → 保留字符串
- 正则不带捕获组（如 `LVS check.*PASSED`）：匹配到即 `True`；
- 匹配不到：该字段不进 dict（缺省值由 Protocol 方法兜底，见 8.4）。

### 8.4 返回值结构

MCP Tool 返回给 LangGraph 的 dict（RealECOMCPServer 的 Protocol 方法里做了二次包装 + 缺省兜底）：

```python
def run_sta(self, design_name: str, run_dir: str) -> dict:
    res = self.run_step("run_sta")
    return {
        "setup_vio": res.get("setup_vio", 0),   # 没 parse 到也要给 0，保证 LangGraph 拿到 int
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

```bash
ECO_BACKEND=mock python -m src.mcp_server.stdio_runner              # 默认 Mock

ECO_BACKEND=real \
  PROJECT_CONFIG=config/project.yaml \
  RUN_CONTEXT=config/run_context.yaml \
  python -m src.mcp_server.stdio_runner
```

2 个环境变量覆盖默认 yaml 路径，方便不同项目复用同一套代码。Shell 启动脚本 `scripts/start_real.sh` 等价封装（`-p/--project-config`、`-c/--run-context`），启动前对两个文件做存在性检查。

另有 `ECO_CSH_BIN` 环境变量可覆盖 wrapper 使用的 csh 解释器（默认 `/bin/csh`），主要给没有 `/bin/csh` 的开发/CI 环境跑集成测试用。

---

## 11. 版本历史

| 版本 | 日期 | 改动 |
|---|---|---|
| v1 | 2026-09-14 | 初始版本。3 yaml 分层 + wrapper 方案，10 个 step 注册完成 |
| v2 | 2026-09-15 | 配置收敛为 2 yaml：删除 `config/eda_tools.yaml`，timeout_s 并入 `project.yaml`（唯一存放处，取消 run_context 覆盖），parse_patterns 收进内置 `src/mcp_server/parsers.py`。step_command 支持 str/list，多命令经临时 `CMDS_FILE` 逐行 eval + fail-fast（替换单 `$CMD` 环境变量）。修复 resolve 顺序：design_name 先于 project.yaml 解析（旧实现 step_command 中 `{design_name}` 为空）。wrapper 经传统 BSD csh 集成测试验证（修复 `$?X &&` 非短路、`$(pwd)` 非 csh 语法、tee 吞退出码三个问题）。 |
