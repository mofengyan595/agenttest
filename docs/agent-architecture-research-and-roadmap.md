# 国内外先进 Agent 架构研究与 AgentTest 自主测试能力升级方案

- 日期：2026-11-11
- 核查对象：`mofengyan595/agenttest`，`master`，HEAD `795310c`（核查时工作区干净）
- 上游源码提交 `2d8fac9`，文档提交 `795310c`
- 本轮性质：**只读技术研究与开发规划**。未修改生产逻辑，未新增框架，未调用付费模型，
  未向真实业务环境发请求，未重跑冻结的 M2/F1。

---

## 0. 结论先行

### 0.1 一句话结论

AgentTest 已经不是一个"按脚本发 HTTP 的测试工具"，但它**也不是 Anthropic 定义的 Agent**。
用官方术语精确地说（[building-effective-agents](https://www.anthropic.com/engineering/building-effective-agents)）：

> **Workflow** are systems where LLMs and tools are orchestrated through predefined code paths.
> **Agents** are systems where LLMs dynamically direct their own processes and tool usage.

AgentTest 的产品主链是**一个由确定性代码编排的 Workflow，在少数固定节点内嵌模型判断**。
它已经具备"模型理解自然语言、确定性程序推导请求实例、确定性 Executor 执行、证据化报告"
这条很强的工作流骨架；缺的是**由模型驱动"下一步做什么"的那一层**。

因此本轮最重要的判断不是"要引入什么新技术"，而是：

> **AgentTest 缺的不是能力，缺的是把已有能力放进一个决策环里。**

### 0.2 五个必须记住的核查发现

| # | 发现 | 证据 | 影响 |
|---|---|---|---|
| 1 | LangGraph 产品主链是**无环线性图**，`max_model_calls` 被硬编码为上限 1 | `workflow.py:30-37`、`workflow.py:312-325` | 现有 LangGraph 结构上不可能重规划 |
| 2 | 产品路径里 LangGraph 退化为**执行壳**：传入 `prepared_plan` + `FakeLLM("")` + `max_model_calls=0` | `product_tasks.py:885-899` | 真正的编排在 `ProductTasks.run()` 的命令式 Python 循环里 |
| 3 | 代码库中**唯一真正的 Agent 循环**是失败后的只读调查，且产品路径上限是 2 次 | `product_probe.py:111-188`、`feedback_runtime.py:247-256` | 存在可复用的 ReAct 骨架，但只用于"善后" |
| 4 | MCP 是**出站能力（AgentTest 作为 Server）**，且只跑一次性 mini target；本机 Agent 并不自主调用 MCP 工具 | `mcp_server.py:65-76`、`mcp_service.py:68` | 不能把 MCP 当作"已具备自主工具调用"的证据 |
| 5 | 无长期记忆、无检索、无 Skills、无 checkpointer；任务持久化有，但**启动即标记 INTERRUPTED、明确不恢复** | `task_store.py:55-59`、`task_service.py:1` | 上下文工程与经验积累几乎为零，是最大空白 |

### 0.3 最终推荐的最小里程碑（详见第 8 节）

**把"一轮测试结束后由人再点一次"改成"由模型在预算内决定是否继续一轮"，即 `Round Supervisor`。**

它只需在 `ProductTasks.run()` 尾部增加**一个受控决策节点**，复用已有的
`previous_result / untested_operations / coverage / questions`，不引入新框架、新存储、
新规划表示，即可把"Workflow"变成"有限轮 Agent 循环"，并且可以用"同一冻结请求集上
的自主收敛率"客观度量。

---

## 1. 核查方法与证据分级

### 1.1 方法

1. `git log` 确认 HEAD、分支、工作区状态；`git remote -v` 确认仓库身份。
2. 通读 `src/agenttest/` 全部 156 个 Python 模块中的入口、编排、规划、执行、报告模块
   （重点 `product_tasks.py` 1253 行、`workflow.py` 368 行、`product_focus.py` 707 行、
   `product_design.py` 930 行、`executor.py` 652 行、`planning_ir.py` 1009 行）。
3. 用 `grep` 反向验证"某模块是否被生产入口 import"，而不是相信 README。
4. 交叉核对项目自身的进度文档（`docs/autonomous-testing-agent-l1-l3-20261110.md`、
   `docs/planning-ir-production-activation.md`），**只采信与源码一致的表述**。
5. 实跑 `ruff check src tests`（全绿）与
   `pytest tests/test_planning_ir.py tests/test_task_store.py tests/test_task_service.py`
   （41 passed），确认核查基线的可运行性。

### 1.2 本报告使用的证据标签

| 标签 | 含义 |
|---|---|
| **【已接入】** | 代码存在，且在产品主链（`TaskService` → `ProductTasks`，或 CLI `agent`）真实调用 |
| **【未接入】** | 代码存在且可运行，但只有离线脚本 / CLI 旁路 / 评测脚本调用 |
| **【部分】** | 机制存在但被预算、范围或权限门禁裁剪到只覆盖一小部分场景 |
| **【仅设计】** | 只有 Prompt、文档或类型定义，没有可执行实现 |
| **【缺失】** | 不存在 |

所有 `文件:行号` 引用均对应当前 HEAD `795310c`。

---

## 2. AgentTest 当前真实架构

### 2.1 先纠正一个常见误解：存在三条真实入口，不是一条

```
入口 A：CLI `agent` / `agent-demo`            __main__.py:240-321
        └─ Workflow(...)  ← LangGraph，模型只被调用一次

入口 B：Web 控制台 / 开发脚本（产品主链）      task_service.py:114-115
        └─ 当 project.target.business_scenario == "web_contract_v2"
           └─ ProductTasks.submit() → ProductTasks.run()   ← 本文重点
              └─ 每个 case 内部再调用一次 Workflow（作为执行壳）

入口 C：预注册项目（goal / suite）             task_service.py:264-286
        └─ derive_goal() → run_goal() / run_suite()
           └─ GoalPlanner → Workflow(...)  ← 单次规划，无 Focus/Design 两阶段
```

判断走哪条入口的**唯一开关**是 `business_scenario`：

- `task_service.py:114-115`：`if project.target.business_scenario == "web_contract_v2": return await self.product.submit(...)`
- `generic_intake.py:319`：`scenario = "web_contract_v2" if setup.assisted else "web_contract_v1"`

也就是说，**用户通过 Web 接入向导添加的新项目走 B**；预注册的内置项目走 C。
两条路径共享 `Executor`、`DSL`、`Policy`、`Report`，但**各自维护一套编排**。这是本文
第 2.4 节要指出的第一个真实重复。

### 2.2 入口 B（产品主链）的完整调用链

以下是 `ProductTasks.run()`（`product_tasks.py:302-1253`）逐段对应的真实调用：

| 步 | 位置 | 做什么 | 模型调用 |
|---|---|---|---|
| 1 | `product_tasks.py:326` | `QUEUED → RUNNING`，取 project 快照并 `guard()` 绑定版本 | — |
| 2 | `product_tasks.py:346-370` | 若当前请求不是默认轮次且有 `focus_factory`，进入 Focus | — |
| 3 | `product_focus.py:580` `select_focus()` | 组装约 25 个 context key + 145 行 `FOCUS_PROMPT` + 闭合枚举 JSON Schema | **1 次** |
| 4 | `product_tasks.py:383-386` `request_planning_ir()` | 把 Focus 结果规范化为 `PlanningIR` | — |
| 5 | `planning_ir.py:359` `build_planning_ir()` | 由原文子句推导请求实例、资源句柄、依赖边、歧义 | — |
| 6 | `product_tasks.py:411-524` | 把需求拆成 `requested_checks` / `questions` / 阻塞操作 / 输入问题 | — |
| 7 | `product_design.py:449` `design_groups()` | **确定性**分组成 `group_N`（每组 ≤6 个主动作，闭包 ≤12） | — |
| 8 | `product_tasks.py:643` | `for position, group in enumerate(summary["groups"])` | — |
| 9 | `product_tasks.py:686-692` | 逐组计算规划引擎选择条件 → `IR_ENGINE` 或 `LEGACY` | — |
| 10 | `product_design.py:568` `propose_design()` | 该组的 `design_context` + `DESIGN_PROMPT` + 闭合枚举 Schema | **每组 1 次** |
| 11 | `product_tasks.py:816-824` | **全部 case 先预检**，任何登录/写请求之前完成校验 | — |
| 12 | `product_tasks.py:825` | `for index, case in enumerate(ordered_cases)` | — |
| 13 | `product_tasks.py:885-899` | `Workflow(..., prepared_plan=scenario, FakeLLM(""), budget=WorkflowBudget(max_model_calls=0, ...))` | **0 次** |
| 14 | `executor.py:273-290` | 失败时按 `product_read_probe` 选调查实现 | 最多 2 次 |
| 15 | `product_tasks.py:982-999` | `coverage()` → `complete` / `checks_passed` → `FINISHED` | — |

**关键推论 1**：一轮任务的模型调用次数 = `1（Focus）+ N（Design，N=组数）+ ≤2（调查）`。
L1–L3 真实证据里「每个最终运行都是 2 次调用（Focus + Design）」正对应 N=1。

**关键推论 2**：第 8 步和第 12 步是**普通 `for` 循环，没有回边**。循环体内部任何失败
（`DESIGN_REJECTED`、`PLAN_REJECTED`）的处理都是 `continue` 或 `return`，不存在"回头再试一次
另一个策略"的代码路径。

### 2.3 LangGraph 在产品主链里到底承担什么

`workflow.py:312-325` 构建的图：

```
START → load_contract → plan → validate → execute → analyze_or_finish → report → END
                          ↑        ↑         ↑
                          └────────┴─────────┴── 只有 ready=False 时短路到 analyze_or_finish
```

- 节点之间是 `add_conditional_edges(name, lambda s: s["ready"], {True: following, False: "analyze_or_finish"})`，
  **`False` 分支指向终点，不指回任何节点**。
- `GraphState` 只有一个字段：`class GraphState(TypedDict): ready: bool`（`workflow.py:58-60`）。
  没有 messages、没有 tool 列表、没有中间观察。
- `WorkflowBudget` 把能力锁死（`workflow.py:30-37`）：
  ```python
  max_model_calls: int = Field(default=1, ge=0, le=1)   # 注释：one plan and one deterministic execution
  max_tool_calls:  int = Field(default=1, ge=0, le=1)
  graph_steps:     int = Field(default=10, ge=1, le=20)
  ```
- 产品路径传入 `budget=WorkflowBudget(max_model_calls=0, ...)`（`product_tasks.py:897`），
  且 `plan` 节点因 `prepared_plan is not None` 直接 `return {"ready": True}`（`workflow.py:191-196`）。

**结论**：在产品主链里，LangGraph 承担的是「一次校验 + 一次执行 + 一次总结」的**线性管道**，
外加把节点名写进 `report.workflow_nodes`（`workflow.py:127-129`）作为审计轨迹。
它**不承担**编排决策。

这不是缺陷本身 —— 缺陷是 `ProductTasks` 用手写 Python 承担了本应由图承担的编排，
导致**两处都在表达"下一步"**，而 LangGraph 这一侧被架空。

### 2.4 真实的重复机制与兼容层清单

| # | 重复/兼容点 | 位置 A | 位置 B | 性质与风险 |
|---|---|---|---|---|
| 1 | **编排所有权** | `ProductTasks.run()` 命令式循环 | `Workflow` LangGraph 图 | 真重复。B 被降级为执行壳，LangGraph 的价值（状态机可视化、持久化、条件边）未兑现 |
| 2 | **两套产品编排** | `ProductTasks`（入口 B） | `run_goal` / `run_suite` + `GoalPlanner`（入口 C） | 真重复。共享执行层，各自实现规划入口与预算 |
| 3 | **两套规划构造** | `planning_compiler.compile_design`（`NORMALIZED_IR_COMPILER_V1`） | `plan_construction` 内联旧逻辑（`UNORDERED_APPROVED_NODES_V1`） | **兼容层**，但已是"单入口双后端"（`plan_construction.py:31-34`），迁移成本低 |
| 4 | **两套失败调查** | `product_probe.investigate_product`（`for` 循环，≤2） | `feedback_runtime.run_feedback`（**有环 LangGraph**，≤3 模型调用） | 真重复。产品走前者，CLI 走后者，能力不一致 |
| 5 | **两套"工具词汇"** | `tool_registry.TOOLS`（6 个） | `mcp_server` 只注册 5 个；`native_tools` 用 `submit_test_plan` | 半统一。`submit_test_plan` 在 MCP 中不可达（`mcp_service.call()` 无该分支） |
| 6 | **两套目标推导** | `derive_goal`（别名匹配 + 目录） | Focus 的自然语言选择 | 只在入口 C 使用，入口 B 不用 |
| 7 | **评测旁路规划栈** | `composed_planning.py`(`FrozenContextPlanner`) | 生产 `ProductTasks` | 仅评测脚本使用，未污染产品，但增加了理解成本 |

### 2.5 真实架构图

```
                        ┌──────────────────────────────────────────┐
  用户自然语言 ────────►│  Web 控制台 (React/TS)  web/src/...       │
                        └───────────────┬──────────────────────────┘
                                        │ POST /tasks
                        ┌───────────────▼──────────────────────────┐
                        │ TaskService.submit()  task_service.py:92 │
                        │  · 私有输入/凭据闸门                     │
                        │  · business_scenario == web_contract_v2? │
                        └──────┬───────────────────────┬───────────┘
                        yes    │                       │  no
                 ┌─────────────▼──────────┐   ┌────────▼─────────────────┐
                 │ ProductTasks.submit    │   │ derive_goal() → run_goal │
                 │ product_tasks.py:220   │   │ / run_suite  (入口 C)    │
                 └─────────────┬──────────┘   └────────┬─────────────────┘
                               │                       │
        ┌──────────────────────▼───────────────────────▼──────────────┐
        │ 规划层                                                      │
        │  Focus  (1 次模型)   product_focus.py:580                    │
        │    └─ PlanningIR  planning_ir.py:359  ← 确定性子句→实例       │
        │  design_groups()  product_design.py:449  ← 确定性分组         │
        │  Design (每组 1 次模型)  product_design.py:568                │
        │    └─ 引擎选择 product_tasks.py:132                          │
        │         ├─ IR_ENGINE   planning_compiler.py:22               │
        │         └─ LEGACY      plan_construction.py:8                │
        └──────────────────────┬──────────────────────────────────────┘
                               │ 每个 case 一个已校验 Scenario
        ┌──────────────────────▼──────────────────────────────────────┐
        │ 执行层  Workflow( LangGraph, 线性无环 )  workflow.py:312      │
        │   load_contract → plan(跳过) → validate → execute → report   │
        │    └─ Executor  executor.py  (唯一允许发 HTTP 的地方)         │
        │         └─ 失败时 investigate_product / run_feedback          │
        └──────────────────────┬──────────────────────────────────────┘
                               │
        ┌──────────────────────▼──────────────────────────────────────┐
        │ 证据层  RunReport  report.py:23   →  runs/<run_id>/report.json│
        │   TaskStore(SQLite)  task_store.py:20  → 状态/事件/用量        │
        │   coverage()  product_tasks.py:1080  → 覆盖与未测原因          │
        └──────────────────────────────────────────────────────────────┘

  旁路（未接入产品主链）：
    MCP Server  mcp_server.py     ← 对外暴露 5 个工具，仅 mini target
    discovery   discovery.py      ← 静态候选发现，被入口 C / intake 使用
    local_trace local_trace.py    ← 事后从 report 导出 span，不上传
    eval_v2/    eval_v2/scoring.py← 冻结评测评分
```

### 2.6 一处必须点明的真实风险模式

`docs/planning-ir-production-activation.md` 记录了一个已经发生过的失败：
新 IR 编译器写完了、测试也过了，但因激活条件里有一条恒为假的判据，
**生产入口实际一直走旧构造器**；而"E2E PASS"只证明了旧路径可用。

项目当前的应对是对的：把 `planning_engine_conditions` 的每个布尔值写进报告
（`product_tasks.py:112-129`、`product_tasks.py:1108-1134`），使"哪条路径真的跑了"
可审计。**这个习惯必须在后续所有演进中保持**，否则任何"已接入"的结论都不可信。

---

## 3. 能力差距矩阵

### 3.1 主矩阵

分类严格按第 1.2 节标签。**已存在的 LangGraph、MCP、状态管理与执行能力不再列为待引入技术。**

#### A. 规划与决策

| 能力 | 状态 | 源码证据 | 说明 |
|---|---|---|---|
| 自然语言 → 结构化测试意图 | **已接入** | `product_focus.py:580` `select_focus`；`product_focus.py:229` `FocusSelection` | 单次结构化解码，闭合枚举约束操作/策略/角色 |
| 子句级义务分解（请求/断言/约束/延后） | **已接入** | `planning_ir.py:23` `ClauseRole`；`planning_ir.py:359` `build_planning_ir` | 确定性地把原文切成 `REQUEST/ASSERTION/CONSTRAINT/DEFERRED` |
| 请求实例身份推导（同一 GET 前后两次） | **已接入** | `planning_ir.py:102` `ActionInstance`；`request_requirements.py:18` `RequiredOrder` | `source_id + action_index` 定址，不靠数组位置 |
| 依赖边与拓扑序 | **已接入** | `planning_ir.py:68` `EDGE`；`planning_ir.py:335` `topological_order` | 每条边带 `reason` 与证据 |
| 动态资源绑定（生产者→消费者） | **已接入** | `planning_ir.py:91` `InstanceArgument`；`planning_compiler.py:60` | 编译期解析 `from_instance + select` |
| 确定性计划编译 | **已接入** | `planning_compiler.py:22` `NORMALIZED_IR_COMPILER_V1` | 单入口双后端（`plan_construction.py:31-34`） |
| **测试用例自主设计（场景枚举）** | **部分** | `product_design.py:568` `propose_design`；`product_design.py:55` `TestDesign` | 见 3.2 的边界说明：模型能提场景，但候选空间被上游裁死 |
| **根据执行反馈重新规划** | **缺失** | `workflow.py:32` `max_model_calls le=1`；`product_tasks.py:643/825` 无回边 | 无任何代码路径能在拿到执行结果后再规划一次 |
| **自主决定"测够了/继续测"** | **缺失** | `product_tasks.py:982-999` `coverage()` 只写结论；续轮靠用户再提交 | `DEFAULT_FOLLOWUP` 是给用户点的按钮文案，不是 Agent 决策 |
| 覆盖缺口识别 | **已接入** | `product_tasks.py:1080` `coverage()`；`product_focus.py:236` `not_covered` | 能算"哪些必测没测到"，但**算完不会自动补** |

#### B. 工具与执行

| 能力 | 状态 | 源码证据 | 说明 |
|---|---|---|---|
| 确定性 HTTP 执行器 | **已接入** | `executor.py:97-120`；`executor.py:255-290` | 唯一发 HTTP 的地方，模型无法绕过 |
| 预算/超时/取消 | **已接入** | `goal_suite.py:304` `SuiteLedger`；`goal_suite.py:319` `BudgetTransport`；`executor.py:291-322` | 含 `usage_unknown` 熔断 |
| 写请求未知结果不重放 | **已接入** | `executor.py:298`、`task_store.py:55-59`、`task_service.py:1` | 明确的"Do not replay writes"纪律 |
| **模型自主选择工具** | **部分** | `product_probe.py:22` `ProductProbeModel`，候选集 `product_probe.py:50` `candidates()` | 只有失败后 1 个工具族、≤2 次、只读 |
| MCP 工具 | **未接入（作为出站能力）** | `mcp_server.py:65-76` 注册 5 个工具；`mcp_service.py:68` 绑死 mini target | AgentTest **对外**提供 MCP；自己的 Agent 不消费 MCP |
| 工具统一词汇表 | **部分** | `tool_registry.py:42-55` 6 个定义 | `submit_test_plan` 只被 `native_tools.py:21` 使用，MCP 侧不可达 |
| 只读调查循环（ReAct） | **已接入** | `product_probe.py:111-188`（产品，≤2）；`feedback_runtime.py:247-256`（CLI，有环，≤3） | 两套实现并存，能力不一致 |
| 负向/边界测试 | **已接入** | `product_design.py:767` `validate_negative_preparation`；`report.py:72-75` | 需事先批准策略 |

#### C. 上下文与记忆

| 能力 | 状态 | 源码证据 | 说明 |
|---|---|---|---|
| 上下文裁剪（按契约/策略/角色收窄） | **已接入** | `planner_input.py:44-131` `planning_context`；`product_focus.py:473-500` | 只做"权限与相关性收窄"，不做检索 |
| 上下文体积上限 | **已接入** | `planner_input.py:120` 100 KB 硬上限；`project_goals.py:61` 20k input tokens | 超限直接报错而非截断 |
| 摘要 / 压缩 | **缺失** | grep `compaction|summar` 无实现 | 无 |
| 检索 / RAG / 向量 | **缺失** | grep `RAG|retriev|embedding|vector` 无实现 | 有意为之（AGENTS.md 禁止） |
| Skills 按需加载 | **缺失** | 无实现 | Prompt 是单一巨型常量（`product_focus.py:259-403` 145 行） |
| **主 Agent 的长期记忆** | **缺失** | `task_store.py` 只有任务/事件/用量/资产表 | 无"某接口历史上出过什么问题"的可查询存储 |
| 单任务内延续上一轮证据 | **已接入** | `product_focus.py:396-402`（previous_result 进 Prompt）；`product_tasks.py:346` | 关键：这是**同任务内**，不是跨任务学习 |
| 任务/事件持久化 | **已接入** | `task_store.py:27-53`（6 张表） | SQLite，含状态版本与幂等 `(owner, submission)` |
| **重启后恢复执行** | **缺失（有意）** | `task_store.py:55-59` `interrupt_unfinished`；`task_service.py:1` "no restart recovery" | 安全选择，但也意味着长任务不可续 |
| LangGraph checkpointer | **缺失** | grep `checkpointer|thread_id` 无 | 图状态不落盘，审计靠 `report.workflow_nodes` |

#### D. 观测与评测

| 能力 | 状态 | 源码证据 | 说明 |
|---|---|---|---|
| 结构化证据报告 | **已接入** | `report.py:23-75`；`product_tasks.py:1241-1244` | 含请求证据、检查项、尝试记录 |
| 结论五分类 | **已接入** | `report.py:23-28` `PASS/ASSERTION_FAILED/TEST_INFRA_ERROR/TOOL_ERROR/NEEDS_REVIEW` | 禁止把未知变 PASS |
| 本地 trace（span） | **未接入** | `local_trace.py:18-40`；`__main__.py` 的 `export-trace` | 事后从 report 派生，不上传 |
| LangSmith tracing | **关闭** | `workflow.py:330` `tracing_context(enabled=False)` | 两个实现都显式关闭 |
| 在线评测/打分 | **缺失** | — | 没有任何"这次测试质量如何"的产品内评分 |
| 冻结离线评测 | **未接入** | `eval_v2/scoring.py:50/118/231`；`nl_evaluation.py` | 强评测基建，但只服务历史冻结对比 |
| 轨迹对比/实验管理 | **未接入** | `eval_v2/comparison.py`、`eval_v2/archive.py` | 同上 |

#### E. 人机边界

| 能力 | 状态 | 源码证据 | 说明 |
|---|---|---|---|
| 权限闸门（策略/角色/操作白名单） | **已接入** | `product_focus.py:99-180`；`planning_ir.py:54-65` `AmbiguityKind`；`safety.py` | 强项 |
| 需要人工确认时产出结构化问题 | **已接入** | `product_focus.py:28` `ExpectationQuestion`；`product_design.py:38` `DesignQuestion` | 区分 `business_rule` / `permission` / `clarification` |
| 人工确认后**自动重排剩余测试** | **部分** | `DEFAULT_FOLLOWUP` + 用户再提交；`product_tasks.py:220-300` | 需人来点，不是 Agent 自己续 |

### 3.2 逐条回答第三节的十个问题

**Q1. `ProductTasks` 与 LangGraph 是否职责重叠？**
**是，而且重叠方向是反的。** 直觉上 LangGraph 应该是编排器，但在产品路径上它被降级为
执行壳（`FakeLLM("")` + `prepared_plan` + `max_model_calls=0`，`product_tasks.py:885-899`），
编排全在 `ProductTasks.run()`（`product_tasks.py:302-1253`，单函数约 950 行）。后果不是"跑不起来"，而是：
- LangGraph 的条件边/状态机能力完全没用上；
- `GraphState` 只剩 `ready: bool`，图内无法携带任何跨步观察；
- 想让图承担 Agent 循环时，需要先给图加状态、加节点、加回边，而不是复用现有 `ProductTasks` 逻辑。

**Q2. Focus / Design 是否已经具备自主测试用例设计能力？**
**具备"在给定候选空间内提场景"的能力，不具备"自主决定候选空间"的能力。**
证据链：
- Focus 能选操作范围（`FocusSelection.operation_ids`），是自主的；
- 但 `design_groups()`（`product_design.py:449-513`）是**确定性分批**，模型无法选择"先测哪组""这组不够要不要拆"；
- Design 在组内能提 `normal/boundary/negative/permission/flow` 五类 case（`product_design.py:25`），
  且必须落到已批准 policy 上（`validate_design_case`，`product_design.py:876`）；
- `TestDesign.cases` 上限 12（`product_design.py:58`），`not_covered` 上限 20 —— 模型可以声明"我没覆盖"，
  但**没有人消费这个声明去补测**。
一句话：**模型是"用例作者"，不是"测试计划的作者"。**

**Q3. 模型是一次性生成计划，还是能根据反馈继续决策？**
**主体是一次性；例外有一处，且范围很窄。**
- 一次性：Focus 1 次，Design 每组 1 次，之后 `Workflow` 内部 0 次（`max_model_calls=0`）。
  证据：`docs/autonomous-testing-agent-l1-l3-20261110.md` 第 2 节"每个最终运行都是 2 次调用（Focus + Design），没有自动重规划"。
- 例外：执行失败后的 `investigate_product`（`product_probe.py:111`），
  最多 2 轮，每轮"看已观测结果 → 选 1 个已批准只读检查 → 执行 → 观察"。
  **这是真正的 ReAct 结构**，但只作用于失败善后，且不产生新的测试用例。

**Q4. MCP 是否真正被自主选择和调用，还是只有固定工具入口？**
**两者都不是你期望的那个。** 真实情况：
- AgentTest 是 MCP **服务端**（`mcp_server.py:65-76`），对外提供
  `list_tests / get_contract / validate_plan / run_scenario / read_report` 五个工具；
- 它绑死在一次性 mini target（`mcp_service.py:68` `create_target({})`），
  预算 `max_runs=3, max_tool_calls=100`（`mcp_service.py:33-34`）；
- AgentTest 自己的 Agent **不调用任何 MCP 工具**；它的"工具"是 Python 函数调用。
- `submit_test_plan` 在 `tool_registry.py` 与 `native_tools.py:21` 里是
  OpenAI function-calling 信封，**不是** MCP 工具（`mcp_server.py:66` 的注册元组里没有它）。
结论：MCP 目前是"对外接口作品"，不是"自主工具调用能力"。

**Q5. Planning IR 是否被用于自主发现的测试实例？**
**没有。IR 实例来自用户请求子句，不来自发现。**
- `build_planning_ir(requirements, atoms, ...)`（`planning_ir.py:359`）的两个输入
  都来自 `request_atoms(当前用户文本)` 与 Focus 的 `requirements`；
- `planning_ir.py:1-12` 文档说得很直白："The LLM decides *what* the user asked for...
  This module decides *how many* requests that means"；
- 真正的"发现"能力在 `discovery.py`（605 行，静态候选生成，`MAX_CANDIDATES=2000`），
  但它只被 `goal_suite.py`、`onboarding.py`、`project_inventory.py`、`eval_v2/runner.py` 使用，
  **`ProductTasks` 不调用 `discover()`**（grep 证实）。
后果：当用户只说"测一轮"（`DEFAULT_ROUND`）时没有子句可解析，IR 不被激活，
退化为"对每个允许操作跑一个正常请求"。

**Q6. 新旧规划编译器的重叠及迁移条件？**
**重叠比预想的小：已经是单入口双后端。**
- 分派点：`plan_construction.py:31-34`
  ```python
  if context.get('planning_ir') is not None:
      return compile_design(context['planning_ir'], plan, context)   # NORMALIZED_IR_COMPILER_V1
  ...  # UNORDERED_APPROVED_NODES_V1 旧逻辑
  ```
- 激活条件共 9 条（见 `docs/planning-ir-production-activation.md` 第 3.1 节），
  实际布尔值写入报告 `summary["planning_engine_groups"][*]["conditions"]`。
- 迁移条件因此是**可枚举的**：消除这 9 条阻塞事实的每一种，旧路径的适用面就缩小一格。
  当前最常见的回退原因是 `IR_AMBIGUITIES`、`PENDING_PERMISSION`、
  `UNRESOLVED_REQUEST_CLAUSES`、`PARTIAL_GROUP_COVERAGE`（`product_tasks.py:120-146`）。
- 建议的淘汰顺序：先让"默认轮次"也走 IR（当前默认轮次无子句 → 不激活），再淘汰旧分支。

**Q7. 执行后的失败分析能否反馈给 Agent？**
**能产出结论，但不能反馈成下一次决策。**
- `executor.py:273-290` 在业务结果**已落盘之后**才启动调查，保证"原失败不可变"；
- `product_probe.investigate_product` 会把每轮观察拼进下一轮 context
  （"Earlier probes are observations; use them to choose a different second check"，
  `product_probe.py:33`）——所以**调查循环内部有反馈**；
- 但循环结束后只写 `report.failure_explanation` / `report.investigations`，
  **没有任何代码把它送回 Focus 或 Design**。
- `feedback_runtime.run_feedback`（CLI 侧）有更强的 `FeedbackAction` + `supported_finding`
  确定性校验（`feedback_runtime.py:42-78`），但同样只产出结论，不触发新计划。

**Q8. 是否能利用历史测试结果选择新的测试重点？**
**同任务内能，跨任务不能。**
- 同任务：`previous_result` / `previous_executed_checks` / `coverage_basis_changed`
  会进入 Focus 的 Prompt（`product_focus.py:396-402`），且 `DEFAULT_FOLLOWUP`
  明确要求"沿用上次测试重点，按最新已确认的信息安排尚未完成的检查"。
- 跨任务：`task_store.py` 的 `saved_tests` / `product_assets` 只用于导出固定回归，
  不是 Agent 可查询的经验库；没有"接口 X 过去 3 次断言失败"这种聚合。
- 换句话说：**记忆的载体是"用户又提交了一次"，不是系统自己记得。**

**Q9. 测试任务是否有持久化状态，能否安全恢复？**
**有持久化，明确不恢复，且这是刻意的安全设计。**
- `task_store.py:27-53`：`tasks`（状态+版本）、`task_events`（相位+请求数）、
  `task_context`（上下文快照）、`model_usage_journal`（用量）、`product_assets`；
- `task_store.py:55-59` `interrupt_unfinished()`：进程启动时把所有非终态任务改成 `INTERRUPTED`；
- `task_service.py:1` 文档字符串直接写 "serial admission and no restart recovery"。
- 优点：不可能出现"重启后自动重放写请求"。
- 代价：超过一次进程生命周期的长任务（P2 要做的"长期任务恢复"）当前无解。

**Q10. Context Engineering、Skills、Memory、Tracing、Eval 是否已有实现？**

| 项 | 结论 | 一句话证据 |
|---|---|---|
| Context Engineering | **部分** | 有收窄与硬上限（`planner_input.py`），无压缩/检索/分层/按需读取 |
| Skills | **缺失** | 能力全部写死在 `FOCUS_PROMPT`(145 行) 与 `DESIGN_PROMPT`(约 120 行) 两个常量里 |
| Memory | **缺失** | 无长期记忆；grep 全库无 memory store |
| Tracing | **未接入** | `local_trace.py` 可用但只在 CLI 旁路；LangSmith 显式 `enabled=False` |
| Eval | **未接入** | `eval_v2` 是完整评分框架，但只服务冻结历史对比，不进入产品反馈 |

### 3.3 一句话总结矩阵

> AgentTest 在**"确定性正确性"这一列几乎全是绿的**：执行、权限、证据、分类、预算、幂等。
> 在**"模型驱动的动态决策"这一列几乎全是红的**：重新规划、自主续轮、自主选工具、
> 上下文压缩、长期记忆、在线评测。
>
> 而后者恰好就是"像真人测试同事一样工作"所需要的那一列。

---

## 4. 国内外 Agent 架构对照

### 4.0 证据获取说明（先读，否则会误引）

| 事项 | 结论 |
|---|---|
| `deepseek-ai/deepseek-harness` 默认分支 | **`master`，不是 `main`**。用 `/main/` 构造引用会得到 404 |
| `deepseek-harness` 成熟度 | README 自称 **_developer preview_**，"THERE WILL BE COMPATIBILITY-BREAKING CHANGES"；SAFETY.md 称"has not undergone a security audit... must not be treated as secure or production-ready" |
| `dsh-superagent-prototype` 成熟度 | README 自称 **"Research prototype for reference only, not an official DeepSeek product"**；并明确"Some designs discussed in the article are **directions for future development and are not implemented in this prototype**" |
| 关键纠正 1 | 原型仓库**没有** `Task` / `Router` / `Worker` / `Watchdog` 类。`router`/`worker` 只是 DSH preset 名（`ROUTER_PRESET`/`WORKER_PRESET`）与 session 角色 |
| 关键纠正 2 | 常被引用的"外部 Watchdog Session 用模型检查 Worker 是否真在推进"，**来自配套文章，不在本仓库代码里**（`docs/007-architecture.md` 约 70 KB 全文无此词；`infra/health.ts` 是组件健康登记，不是进展检测） |
| 关键纠正 3 | 域名文档真实文件名是 `docs/001-domain.md`，不是 `001-domain-model.md` |
| Claude Code 源码泄露 | **不作为架构论据**。它是第三方对已发布客户端的解读，部分细节互相矛盾（见 4.2.7）。官方文档与官博才是架构证据 |
| 官方量化"模型 vs Harness"比例 | **不存在**。任何声称 Anthropic 给出 X/Y 比例的说法都是编造的 |
| **README 不等于代码** | 国内四个项目另做了逐文件源码核对，**推翻了本报告初稿对 youtu-agent `ContextManager` 的判断**，并发现多项"名不符实"。详见 **4.6.5**。这正是本报告 2.6 节所警告的风险模式，且它发生在国外/国内项目身上同样成立 |

---

### 4.1 DeepSeek：`deepseek-harness` 与 `dsh-superagent-prototype`

#### 4.1.1 两个项目是**两层**，不是一回事

| | `deepseek-harness` | `dsh-superagent-prototype` |
|---|---|---|
| 定位 | Agent **运行时/Harness**（模型 + 工具 + 会话 + 审批） | 在 DSH 之上的**编排器**（跨会话派活） |
| 语言/形态 | TypeScript，Cordis 插件体系，50+ packages | TypeScript，单进程 + SQLite |
| 核心名词 | Session, SessionEvent, Turn, Step, Agent, AgentLoop, Tool, Seam, Profile, Bundle, Preset, Approval, Sandbox, Goal, Job | Source, Trigger, Thread, Correlation, Channel, Policy, Eval(Gate+Assessment), Decision, **Mandate**, **Ticket**, Assignment, Subscription, Executor, Report, **Attention**, Disposition |
| 有无 `Task`/`Router`/`Worker`/`Watchdog` 类 | **无** | **无** |

#### 4.1.2 `deepseek-harness` 的核心工程思想（附依据）

1. **一切皆插件，没有特权内核。** `docs/architecture.md`: "There is no privileged core to patch."
   分层为 profile → bundles → `cordis.patch.yml` → `--patch`。
2. **Step / Turn 的严格定义。** "A **step** is one model request plus the tools it calls.
   A **turn** is zero or more steps: it opens before its first input is claimed and closes
   once nothing is owed."（`docs/architecture.md`）
   —— 这个定义比 AgentTest 的"一轮测试"精确得多，值得直接借用命名。
3. **"模型可见即已记录"。** "**Model-visible means logged. Every model request must be
   reconstructable from the log.**" 上下文不是被"组装"出来的，而是从 session log
   `deriveMessages()` **投影**出来的。这是本项目在可靠性上最值得学的一条。
4. **日志是一等公民，且只追加。** `session.jsonl` / `session.vN.jsonl[.zstd]`；
   已提交的 generation 永不重命名/替换/删除；相邻 `vN → vN+1` 迁移包；
   未封口的中断尾巴有 `packages/core/session/src/repair.ts`，分叉有 `fork.ts`。
5. **审批默认拒绝。** `docs/tool-execution-pipeline.md`：`tools/pre-execute` 瀑布流执行
   hooks→permission→sandbox，注册**单调**守卫（"deny or abstain"），最后 `ctx.approval`
   一次性询问；"absent or unanswerable: **deny**"。
6. **压缩是插件，不是内核。** `dsh-compaction-basic`，"optional tool-result pruning runs
   before summary selection"。

#### 4.1.3 `dsh-superagent-prototype` 的核心工程思想

1. **哲学即成本函数。** `docs/000-philosophy.md`：
   "**Take as little human attention as possible, and give as much value as possible.**"
   —— 这句直接对应本任务第四节第 10 问（减少用户注意力投入）。
2. **授权与执行分离，且授权是独立不可变物。** `Mandate` 是独立聚合根，
   被 `revalidate()` 反复校验；只有人能 `stopTicket` 撤回。**策略变更不撤销 Mandate**
   （`policyEffects()` 明确"it does not revoke Mandates"）。
3. **先持久化意图，再执行副作用。** `src/app/store.ts`：`Store.atomic()` 用嵌套
   `SAVEPOINT`；`afterCommit()` 只在最外层提交后跑回调；副作用走 `Effects`
   ——**事务内入队，提交后执行，指数退避，按 `(kind, key)` 幂等**。
4. **阶段是推导出来的，不是存出来的。** `src/core/stage.ts` 的 `ticketStage` 是
   对已存事实的纯函数（first-matching-row）。彻底消灭状态漂移。
5. **模型判断之前先过确定性闸门。** `src/policy.ts` 的 `gate()` 先做允许表/准入表检查，
   通过后才让 `Assessor` 判断。
6. **上下文按尺寸硬约束，没有检索。** `src/app/context.ts` 用 `promptFile('shared' |
   'orchestrator' | 'worker')` 读 Markdown；文本分页 4,000 UTF-16 单位（默认）/8,000（上限）；
   列表 20/50；Thread 窗口"首 5 条 + 两侧各 5 条"；其余消息压成 50 字符 stub，
   需要时由 `orchestrator_eval_read` 展开。

#### 4.1.4 "事件驱动"与"异步 Agent Loop"的真相

不是进程内递归循环，而是**定时器驱动的持久化队列**：

```
Passes.start()  →  setInterval(() => this.request(), 2000)        # 2 秒一拍
pumpOnce()      →  host.effects.kick()                            # 先跑已入队副作用
                →  host.engine.evaluateDue()                      # 再处理到期 Eval / 唤醒 Ticket
Engine.receive(trigger)  →  存 Trigger（一次）→ 扇出到订阅的 Ticket → 排队 Eval
Engine.decide()          →  原子创建 Ticket + Mandate + Assignment
Effects(kind=launch)     →  DshExecutor 启动 Session
```

重启语义（`docs/003-execution.md` "Restart"）：运行中的 Assignment 以 "host restarted"
结束，然后**按最老 Ticket 优先**重新开始；Session、目录、订阅、待发消息、已存工作全部存活；
关停时每个 worker 最多等 10 秒。

#### 4.1.5 严格分类：什么真做了，什么只是文章

| 能力 | 分类 | 依据 |
|---|---|---|
| 领域模型（Source/Trigger/Thread/Mandate/Ticket/…） | **(1) 真代码且接线** | `docs/001-domain.md` + `src/core/engine.ts` 中的 `UserError` 守卫 |
| 异步驱动 | **(1) 真代码** | `src/app/passes.ts` 的 2 秒 `setInterval` |
| 持久化与恢复 | **(1) 真代码** | `Store` DDL、`SCHEMA_VERSION=11`、`Effects` 队列、`docs/003` Restart |
| 上下文组装 | **(1) 真代码** | `src/app/context.ts`、`src/app/resources.ts` 分页上限 |
| 授权/Mandate/HITL | **(1) 真代码** | `src/policy.ts` `gate`/`revalidate`，被 `Engine.decide` 调用 |
| **模型驱动的"无进展"看门狗** | **(4) 不存在** | 源码树与 `docs/007` 均无；README 明说文章设计未实现 |
| **机械性故障/停滞处理** | **(1) 真代码** | `HealthBoard`、`RetryHealth`、同类副作用连续 3 次失败升级、单轮后台 30 分钟上限、Eval 120 秒超时重排 2 次后 `unavailable`、失败 turn 在 15/30/60 秒补发续作消息 |
| harness 的 Agent Loop 实现体 | **部分 UNVERIFIED** | `packages/core/agent-loop/src/index.ts` 抓取失败；`constants.ts` 只有一个常量 `DEFAULT_MAX_PARALLEL_TOOL_CALLS = 10` |
| harness 的 no-progress watchdog | **(4) 在 loop 的常量文件与两篇 loop 文档中均不存在；全仓范围 UNVERIFIED** | 同上 |

#### 4.1.6 对 AgentTest 的适用性

**可以直接借鉴（高价值、低复杂度）：**

| 借鉴点 | 为什么对 AgentTest 特别合适 |
|---|---|
| **Mandate 作为独立不可变授权物** | AgentTest 已有 `operator-approved policy/operation` 契约，正好是 Mandate 的形态。把"凭什么是允许的"（来源策略、批准版本、作用域）与"要做什么"（Ticket/任务）分开存，能在续轮/恢复时**重新校验**而不是盲信快照 |
| **先持久化意图，再执行副作用** | 直接对应 AGENTS.md 的"写请求未知结果不得自动重放"。AgentTest 现在是"启动即全部 INTERRUPTED"来规避，代价是长任务不可续。DeepSeek 的 `Effects`（事务内入队 + 提交后执行 + 按 `(kind,key)` 幂等）是**既能续又不重放**的正解 |
| **阶段推导而非存储** | 对应 AgentTest 的 `PASS/ASSERTION_FAILED/NEEDS_REVIEW`。当前 `coverage()` 已是推导式，应保持并在续轮逻辑中复用 |
| **模型之前先过确定性闸门** | AgentTest 已做（Focus 闭合枚举 + `AmbiguityKind`），这是它的强项，继续加强而不是替换 |
| **按尺寸硬约束上下文 + 分页读取指针** | 比 RAG 便宜得多且可审计，符合"不为技术名词增加基础设施" |
| **"model-visible means logged"** | AgentTest 已有 `report.json` 与 `task_events`；缺的是"每次模型请求都能从日志重建"。补这一条能让所有上下文工程的争论变成可测问题 |

**不应照搬（对 AgentTest 是过度工程）：**

1. Cordis 插件体系 / profile / bundle / patch 分层 —— 单一用途 Agent 不需要。
2. Source/Thread/Correlation/Subscription/Channel 六件套 —— AgentTest 的"source"是一份测试契约，不是多租户事件流。
3. 12 种 effect 类型与 outbox 会话内排序 —— 那是聊天投递语义。
4. Schema 迁移脚手架 + 设置/提示词版本史 —— 成本远大于收益。
5. 便宜 router + 昂贵 worker 的双模型分工 —— 只在并发很高时才划算。
6. **自己造 watchdog。** 两个仓库都没实现；不要发明"LLM 检查 Agent 是否在推进"。用确定性预算/超时/连续失败上限。
7. **绝对不要参考它的执行模型。** `docs/003-execution.md` 明确："Executor Sessions run commands
   without approval as the operator's OS user"，且"These wrappers and directories establish
   defaults, **not a security boundary**." 这与 AGENTS.md 第 6 条完全相反。

---

### 4.2 Claude Code / Anthropic

#### 4.2.1 Agent Loop：官方的三段式，以及"谁有权结束"

官方定义（[how-claude-code-works](https://code.claude.com/docs/en/how-claude-code-works)）：

> "When you give Claude a task, it works through three phases: **gather context**, **take action**,
> and **verify results**. These phases blend together."

> "The agentic loop is powered by two components: models that reason and tools that act.
> Claude Code is the layer around the model that provides the tools and manages the context
> the model sees. This surrounding layer is what the term **agentic harness** refers to."

机械循环在 SDK 文档里写得最清楚（[agent-sdk/agent-loop](https://code.claude.com/docs/en/agent-sdk/agent-loop)）：

1. 收 prompt + system prompt + 工具定义 + 历史 → `SystemMessage`(`init`)；
2. "Claude evaluates the current state... It may respond with text, request one or more tool calls, or both."；
3. "The SDK runs each requested tool and collects the results. Each set of tool results feeds back to Claude for the next decision."；
4. "Steps 2 and 3 repeat as a cycle... **until it produces a response with no tool calls**."；
5. 最终 `AssistantMessage` + `ResultMessage`（文本、token、cost、session id）。

**终止权的关键区分**（这是本节最值得写进设计的一条）：

| 终止方式 | 谁决定 | 依据 |
|---|---|---|
| 正常结束 | **模型**（发出无工具调用的轮次） | agent-loop 文档 |
| `Stop` hook 退出码 2 | **Harness 拒绝接受"结束"**，"sends Claude back to work... enforced by Claude Code (the harness), so the turn doesn't end until the typecheck passes or the block limit is reached" | [best-practices](https://code.claude.com/docs/en/best-practices) |
| `/goal` | 另一个模型每轮核对条件 | 同上 |
| `maxTurns` / `--max-budget-usd` | Harness 硬上限 | [tools-reference](https://code.claude.com/docs/en/tools-reference)、[headless](https://code.claude.com/docs/en/headless) |

> **模型决定要不要停；Harness 决定这个"停"是否被接受。**

#### 4.2.2 工具：选择在模型，动作空间在 Harness，**错误文本是接口**

- 内置工具是固定命名清单（约 40 个），工具名同时是权限规则、子 Agent 工具表、hook 匹配器的标识符。
- "For the most part, Claude decides when to use these tools and you don't need to name them yourself."
  ——选择由模型做；Harness 只负责**收窄候选集**（权限规则、子 Agent 工具白名单、`--tools`、hook `if`、MCP schema 延迟加载）。
- **ACI（agent-computer interface）工程**，官方两条依据：
  - [building-effective-agents](https://www.anthropic.com/engineering/building-effective-agents) 附录 2：
    "Tool definitions and specifications should be given just as much prompt engineering attention as your
    overall prompts."；"we actually spent more time optimizing our tools than the overall prompt"；
    SWE-bench 里把工具改成"只接受绝对路径"后"the model used this method flawlessly"；
    以及 **"Poka-yoke your tools. Change the arguments so that it is harder to make mistakes."**
  - [writing-tools-for-agents](https://www.anthropic.com/engineering/writing-tools-for-agents)：
    "Tools are a new kind of software which reflects a contract between deterministic systems
    and non-deterministic agents."
- **错误消息本身就是 Prompt。** Read 对大文件返回 `PARTIAL view` 并告诉模型如何用 `offset`/`limit` 继续；
  WebFetch 拒绝 localhost 时"tells Claude to reach local servers with `curl` through Bash instead"。

#### 4.2.3 上下文工程：官方有明确清单，压缩不是玄学

- **压缩后什么会活下来，官方给了表**（[context-window](https://code.claude.com/docs/en/context-window)）：
  system prompt 与 output style 保留；项目根 `CLAUDE.md`、无作用域规则、auto memory、
  skill 描述、git 快照与 plan-mode 计划**从磁盘重新注入**；路径作用域规则与嵌套 `CLAUDE.md`
  按需重载；**最多 5 个最近修改过的读/写文件被重读**（单文件 >5,000 tokens 只回一个路径引用）；
  已调用 skill 正文按**每 skill ≤5,000 tokens、合计 ≤25,000** 重注入，超了先丢最老的。
- **压缩 vs 重置是明确的设计轴**（[harness-design-long-running-apps](https://www.anthropic.com/engineering/harness-design-long-running-apps)）：
  "While compaction preserves continuity, it doesn't give the agent a clean slate, which means
  **context anxiety** can still persist. A reset provides a clean slate, at the cost of the handoff
  artifact having enough state."
- **子 Agent 的上下文隔离是机制不是口号**：每个子 Agent 有独立上下文窗口；非 fork 子 Agent
  **拿不到**你的对话、已调用 skill、auto memory、已读文件，只拿 `CLAUDE.md`（Explore/Plan 除外）、
  git 快照、预加载 skill 与同级 Agent 名册。
- **渐进披露有明确的成本账**（[features-overview](https://code.claude.com/docs/en/features-overview)）：
  `CLAUDE.md` = 每次请求全量；Skills = 启动只加载描述，用到才加载正文；
  MCP = "**Full JSON schemas stay deferred until Claude needs a specific tool**"。
- **动机是 context rot**（[effective-context-engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)）：
  "as the number of tokens in the context window increases, the model's ability to accurately
  recall information from that context decreases."

#### 4.2.4 Skills：按需加载的官方定义

- Claude Code（[skills](https://code.claude.com/docs/en/skills)）：
  "Create a `SKILL.md` file with instructions, and Claude adds it to its toolkit."；
  遵循 [Agent Skills](https://agentskills.io) 开放标准。
- 标准本身对加载模型的定义（[agentskills.io](https://agentskills.io)），三段式**渐进披露**：
  1. **Discovery**：启动只加载 name + description；
  2. **Activation**：任务匹配描述时，把完整 `SKILL.md` 读进上下文；
  3. **Execution**：按指令执行，可再按需加载 `scripts/`、`references/`、`assets/`。
- 官方给的价值理由（也是"为什么不用 RAG"的理由）：
  "Unlike CLAUDE.md content, a skill's body loads only when it's used, so long reference material
  costs almost nothing until you need it."
- 易错的细节：一旦调用，"the rendered `SKILL.md` content enters the conversation as a single message
  and **stays there across later turns**"；重复调用同样内容只加一句"already loaded"。

#### 4.2.5 Memory：`CLAUDE.md` + auto memory

| 维度 | 官方规定 |
|---|---|
| 两套系统 | `CLAUDE.md`（人写规则）与 auto memory（Claude 自己写经验），**都在会话开始加载** |
| 定位 | "Claude treats them as context, not enforced configuration. To block an action regardless of what Claude decides, use a PreToolUse hook instead." |
| 层级 | managed policy → `~/.claude/CLAUDE.md` → `./CLAUDE.md` → `./CLAUDE.local.md`；工作目录**之上**的在启动时加载，**子目录**的按需加载 |
| 导入 | `@path/to/import`，可递归，**最大 4 层**；解析到工作目录外会触发一次性批准 |
| 上限 | "target under 200 lines per CLAUDE.md file"；单文件 >4 MiB **直接跳过**；auto memory 只加载 `MEMORY.md` 前 200 行或 25 KB |
| 重要陷阱 | "imports help you organize a long file **but don't reduce its context cost**, because imported files load together with the CLAUDE.md that imports them" |

#### 4.2.6 权限与验证环

- 六种权限模式：`default`/`acceptEdits`/`plan`/`auto`/`dontAsk`/`bypassPermissions`
  （[permission-modes](https://code.claude.com/docs/en/permission-modes)）。
- **不变式**："**Deny rules block in every mode, including `bypassPermissions`.**"
  且"`claude -p` 与 SDK 在未设模式时按 Manual 启动，需要批准而未获批准的调用会被拒绝"。
- 规则语法统一为 `ToolName(specifier)`：`Bash(npm run *)`、`Read(~/secrets/**)`、
  `Edit(/src/**)`、`WebFetch(domain:example.com)`。
- Hooks（[hooks](https://code.claude.com/docs/en/hooks)）：`PreToolUse` 返回
  `permissionDecision ∈ allow/deny/ask/defer`；退出码 2 阻断并把 stderr 喂给模型；
  **超时默认不阻断**（"don't count on a stalled hook to act as a gate"）。
- **验证环的官方原文**（[best-practices](https://code.claude.com/docs/en/best-practices) §"Give Claude a check to run"），
  这段是本报告后半部分所有设计的直接依据：

> "**Claude stops when the work looks done.** If it has nothing to run that tells it whether the
> work actually *is* done, 'looks done' is all it has to go on."

> "**Give Claude something it can run that comes back pass or fail. Once it has that,
> it can keep going on its own and you don't have to watch.**"

> "Claude said it was done and it wasn't quite, **usually because it had no way to check its own work**."

  强化阶梯：prompt → `/goal`（独立模型每轮核对）→ `Stop` hook（Harness 强制）→
  `/code-review`（"a fresh subagent that never saw your conversation. It isn't swayed by the
  reasoning that produced the code."）。并要求"ask Claude for *evidence*: the command it ran and
  its output... 'All tests pass' on its own tells you very little."

#### 4.2.7 2026 年源码泄露事件：**只作为背景，不作架构论据**

已知可引用的事实（第三方报道，非 Anthropic 一手页面）：

- npm `@anthropic-ai/claude-code` **v2.1.88** 包内含 `.map` source map，
  `sourcesContent` 直接内嵌原始 TypeScript，约 **59.8 MB / 1,900 文件 / 512,000 行**；
  包已被下架。（[NodeSource](https://nodesource.com/blog/anthropic-claude-code-source-leak-bun-bug)、
  [Alex Kim](https://alex000kim.com/posts/2026-03-31-claude-code-source-leak/)）
- 发现者 Chaofan Shou（@Fried_rice），2026-03-31。
- Anthropic 的口径是"release packaging issue caused by human error, not a security breach"，
  但没有可抓取的 Anthropic 一手页面 → **只能写成"多方报道称"**。
- 根因**有争议**：Anthropic 口径 vs 第三方指向 Bun bundler 在 `development: false` 下仍产出 source map
  （[oven-sh/bun#28001](https://github.com/oven-sh/bun/issues/28001)）+ `.npmignore` 缺 `*.map`。
  **两种说法并存，本报告不裁决。**

对本研究**唯一有架构意义的**一条第三方观察：有分析称其**多 Agent 协调逻辑主要写在 Prompt 里，
而不是代码里**。这与官方"`/goal` 由另一个模型核对"的表述方向一致，可作为"协调智能可以放在文本里"的旁证，
但**不能作为事实引用**。

明确不做的事：不复述泄露的内部 Prompt、诱饵工具名、内部代号，不把"五层 Harness""四阶段上下文管道"
这类聚合站说法写入本报告——它们在两份实质性技术分析中都不存在（`UNVERIFIED`）。

#### 4.2.8 "为什么 Claude Code 能在宽泛目标下自主探索、行动、自验？"

这是本任务最核心的提问。诚实的答案是**三段式**，不是一个比例：

**（1）必要性在 Harness 一侧。** 没有持久循环、工具结果回灌、上下文管理与权限层，
模型拿到宽泛目标根本无法行动。Anthropic 自己的消融：
"Out of the box, even a frontier coding model like Opus 4.5 running on the Claude Agent SDK
in a loop **across multiple context windows** will fall short of building a production-quality
web app if it's only given a high-level prompt"（[effective-harnesses-for-long-running-agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)）。
注意这句话的形状：**循环与压缩已经在位，它仍然失败。循环 ≠ 充分。**

**（2）上限在模型一侧。** "we were able to improve Claude's performance well above baseline
through prompt engineering and harness design—but **both eventually hit ceilings**"
（[harness-design-long-running-apps](https://www.anthropic.com/engineering/harness-design-long-running-apps)）。
推论：Harness 投入有**边际递减曲线**，膝盖位置随模型变强而移动，所以复杂度必须"重新挣得"，
不能累积。

**（3）放大器是"可验证性"与"上下文纪律"。** 二者都是 Harness 提供的、模型随后加以利用的**可供性**。

按能力归因（这是可辩护的分解方式）：

| 能力 | 归属 | 依据 |
|---|---|---|
| 决定下一步做什么、要不要继续 | **模型** | "Claude decides what each step requires"；无工具调用即结束 |
| 跑循环、执行工具、回灌结果 | **Harness** | "The SDK runs each requested tool and collects the results" |
| 正确使用工具（schema/边界/参数纪律） | **模型**，但形状由 Harness 设计的 ACI 决定 | ACI/poka-yoke 原文、绝对路径案例 |
| 知道工作是否真的完成 | **Harness 提供判据 + 模型判断** | "no way to check its own work" |
| **拒绝接受"完成"** | **Harness** | `Stop` hook、`/goal`、deny 规则 |
| 长时程不丢一致性 | **Harness** | 压缩/重置/工件；官方点名的模型失效模式："context anxiety"、过早宣布完成 |
| 跨会话持久 | **Harness（文件）** | initializer agent + feature-list JSON + progress file + git |
| 广度探索的规模 | **Harness 派发 + 模型分解** | 子 Agent；且"**token usage by itself explains 80% of the variance**" |
| 权限、安全、爆炸半径 | **完全在 Harness** | 六模式 + deny 规则不变式 |
| 任务难度天花板 | **模型** | "both eventually hit ceilings" |

**两个最有价值的失效模式**（同出自 [effective-harnesses-for-long-running-agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)），
它们恰好落在分割线两侧：

1. **"the agent tended to try to do too much at once—essentially to attempt to one-shot the app"**
   → 实现中途上下文耗尽。这是**上下文管理（Harness）问题**，带一个**规划（模型）成分**。
2. **"a later agent instance would look around, see that progress had been made, and declare the
   job done"** → 这是**终止接受权（Harness）问题**，解法是把"完成"外化成状态
   （per-feature pass/fail 清单），而不是求模型"更认真一点"。

> 全篇最可迁移的一条结论：
> **Harness 必须持有"完成"的定义，因为模型会把自己的"看起来合理"当作证据。**

#### 4.2.9 对 AgentTest 的适用性与额外复杂度

**应借鉴（高价值、低风险）：**

| 借鉴点 | 对 AgentTest 的具体落点 |
|---|---|
| **以 Executor 的判据作为唯一可接受的终止条件** | 对应 `Stop` hook 退出码 2。AgentTest 的图不应因"模型说做完了"而到 END，只能因**每条断言都有机器判据**（`PASS/ASSERTION_FAILED/TEST_INFRA_ERROR/TOOL_ERROR/NEEDS_REVIEW`）而结束。这一条现在基本成立，需要**显式化并加测试** |
| **错误消息即 ACI，按 poka-yoke 设计** | 直接改进 `planning_ir.py` 的 `AmbiguityKind` 与 `diagnostics.py` 的 `ValidationFailure`：每条拒绝都要说明"哪里错、允许集合是什么、最接近的合法操作/策略是哪个"。这是**最便宜的可靠性提升** |
| **对 IR 做 poka-yoke** | 把"目标主机"从字符串改成"已解析的 allowlist 引用"；把"期望状态码"从自由文本改成由契约派生的枚举；操作 ID 强制全限定。减少自由度 = 减少静默误规划 |
| **把不变量全部放进确定性列，并用测试断言** | AGENTS.md 第 7 条（未知期望→`NEEDS_REVIEW`）与第 6 条（SSRF/allowlist）必须由代码与测试保证，不能只写在 Prompt 里 |
| **小而带版本的"项目事实文件"** | 采用 `CLAUDE.md` 的纪律：<200 行、只写事实不写流程、不做会膨胀上下文的导入。内容：base URL、身份别名、环境类别、跑检查的那一条命令。多步流程移进可按 ID 检索的文档 |
| **观测压缩 + 按需取详情** | 工具结果只返回"摘要 + 工件句柄"（request id、状态、断言差异、Top-N 日志、完整 trace 路径），完整 trace 按需再读——与"超 5,000 tokens 的文件只回引用"同构 |
| **写操作按参数模式放行** | 借 `Operation(http.POST /users/*)` 的思路，把"隔离环境授权"从布尔升级为**模式**；并保持"deny 在任何模式下都生效" |
| **连续失败达上限即停** | 同一 step 连续 N 次 `TEST_INFRA_ERROR`/`TOOL_ERROR` 后带分类终止，而不是烧预算 |
| **"要证据"结构化** | 报告里每条断言都要引用 request id + 状态 + 命中期望，让人不必相信散文 |
| **Fresh-eyes reviewer 节点** | 对应 `/code-review`：新增一个只看 `planning_ir` + 工件、**看不到规划推理过程**的图节点做复核。单进程，不需要多 Agent 框架 |

**不应照搬：**

- **子 Agent 扇出 / 动态工作流 / Agent 团队 / 跨会话消息**。官方给多 Agent 的理由是"广度优先的只读研究，
  token 花费就是杠杆"；AgentTest 是顺序、有界、带写操作的测试计划，且 AGENTS.md 冻结多 Agent。
  那个 90.2% 不能迁移过来。
- **自动压缩机制**（5 文件重读启发式、skill 重注入预算、25k/5k 上限）。AgentTest 任务有界、
  IR 才是持久状态；"摘要 + 工件引用"足够。
- **auto memory + 后台巩固（"Dream" 子 Agent）**。一个静态事实文件 + 一个**人审过的**
  `lessons.md` 追加日志足够，还能避免"Agent 自己的笔记变成未经验证的真理"。
- **十几个生命周期事件的 hooks DSL**。只需要两个拦截点：操作前（校验/拒绝）与运行后（分类）。
- **Skills 市场 / 插件打包 / 按描述渐进披露**。AgentTest 只有一个 DSL，schema 本身就是工具说明；
  1,536 字符描述预算是"几百个工具"才需要解决的问题。
- **分类器自动批准（auto mode）**。我们要的是确定性，不是第二个模型裁决安全。
- **泄露事件里的一切**（反蒸馏诱饵、客户端认证、prompt-cache 破坏检测）。无关，且不可引用。

**一个必须诚实写下的代价：**

> Claude Code 的通用性很大程度来自 `Bash` ——在安全包装下的任意动作。
> AgentTest 有意用"允许清单 DSL"换掉了它，这对测试 Agent 是正确取舍；
> 但代价是：**操作覆盖度与错误可读性成为关键路径**。
> 如果 DSL 表达不了某个想测的东西，Agent 没有退路，只能卡住或产出 `NEEDS_REVIEW` 噪音。
> 因此必须把"**因不可表达而被拒绝的计划数**"作为一级指标来度量，
> 就像 Anthropic 度量工具选择错误一样。

---

### 4.3 阿里 AgentScope

**证据深度**：官方 README（`raw.githubusercontent.com/agentscope-ai/agentscope/main/README.md`，HTTP 200）
+ 官方文档站 `docs.agentscope.io` + 论文 arXiv 2402.14034 / 2508.16279。以下"设计立场"部分为一手引用；
具体类名层面的实现细节本报告不作断言。

#### 核心工程思想

1. **明确的、与 AgentTest 相反的设计立场。** README 原文：

   > "We design for increasingly agentic LLMs. Our approach leverages the models' reasoning and
   > tool use abilities **rather than constraining them with strict prompts and opinionated
   > orchestrations**."

   这句话值得被 AgentTest 认真读三遍：**AgentScope 把"严格 Prompt + 强编排"视为缺点。**
   而 AgentTest 的整个安全模型正建立在"严格 Prompt + 闭合枚举 + 强编排"之上。

2. **基础构件（README 列出的原语）**：ReAct、Toolkit（Python 工具 / MCP server / skills）、
   Model、Context（自动压缩、工具结果 offload、通过 middleware 注入上下文）、
   Event System、Permission & HITL（确认、bypass 模式）、
   Middleware（可组合钩子：reply / reasoning / acting / model calling / permission checking /
   context compression / system prompt）、Memory（可切换后端，如 ReMe / Mem0）、Workspace / Sandbox。
3. **2.0 走向"服务化"**：FastAPI 后端、多租户、TeamPipeline（leader-worker）、channels、
   RAG 服务、MCP & Skill Hub、持久化、调度。

#### 已实现 vs 概念设计

| 能力 | 判断 |
|---|---|
| ReAct / Toolkit / Memory / HITL | README 与文档均描述为**已提供的框架能力**，有 PyPI 包与长期版本序列，可视为已实现 |
| Context 自动压缩、工具结果 offload | README 明列为 Context 构件能力；具体策略本报告未逐行核实（`UNVERIFIED` 细节） |
| TeamPipeline（leader-worker 多 Agent） | 属 2.0 agent service，**不是**核心库默认路径；对 AgentTest 无用 |
| Memory 后端（ReMe/Mem0） | 作为可切换后端存在，说明其 Memory 是"外挂式"而非内建知识层 |

#### 对 AgentTest 的适用性与借鉴价值

**借鉴价值（一条，但很重）：**

> **它把"约束模型"当成要避免的事；AgentTest 把"约束模型"当成安全前提。
> 两者都对——但取舍点必须说清楚：AgentTest 约束的是「能碰什么」（权限面），
> 不该约束「怎么想」（决策面）。**

这直接支持本报告 P0-2 的设计：**决策空间可以放宽，动作空间必须锁死。**
现在的代码把两者一起锁死了——`FOCUS_PROMPT` 的 145 行规则既在限定权限，
也在限定模型"应该怎么推理"。

**额外的（低优先级）借鉴：** middleware 的可组合钩子形态，说明"两个拦截点"（操作前/运行后）
在工程上是常见且足够的；不需要十几个生命周期事件。

#### 额外复杂度（不应照搬）

- 多租户 + 服务化 + channels + RAG 服务：这是平台公司的形态，与"本地单用户测试同事"不匹配。
- Memory 可切换后端：引入的是"选择困难"而不是能力。
- leader-worker 多 Agent：见 6.3。

---

### 4.4 Qwen-Agent

**证据深度**：官方 README（HTTP 200）+ 官方文档 `qwenlm.github.io/Qwen-Agent`。

#### 核心工程思想

1. **"指令跟随 + 工具使用 + 规划 + 记忆"四能力驱动的应用框架**，README 自述
   "Now Qwen-Agent plays as the backend of Qwen Chat" —— 有真实产品负载，不是玩具。
2. **工具注册与参数解析是重点工程**：`BaseTool` + `@register_tool`，工具声明
   `description` + `parameters` + `function`；历史上为 Qwen 系列反复调整
   "默认 Function Call 模板"（README 变更日志里明确写了"adjust the default Function Call
   template"），并支持 native API tool call（vLLM 内建解析）。
3. **多轮/多步/并行工具调用**是显式支持的能力（QwQ-32B demo 明确写 parallel / multi-step /
   multi-turn tool calls）。
4. **有专门的规划评测基准 `DeepPlanning`**（2026-01 开源）——
   这是六个项目里对 AgentTest **最直接有用**的一个产物。

#### 已实现 vs 概念设计

| 能力 | 判断 |
|---|---|
| Function calling / 参数解析 | 已实现且是一等公民（有多年积累的模板与解析逻辑） |
| 多步/并行工具调用 | 已实现（demo 明示） |
| MCP | 通过 `[mcp]` extra 与 cookbook 支持，属可选集成 |
| Planning | 有 `Planning` 相关能力与 `DeepPlanning` 基准；具体算法本报告未核实 |
| Memory / RAG | 有 `[rag]` extra 与长文档 QA 方案（MiniMax 长上下文工作的产物） |

#### 对 AgentTest 的适用性与借鉴价值

**借鉴价值：`DeepPlanning` 提供了一个"如何客观评规划质量"的现成范式。**
AgentTest 目前只有 PASS 计数与冻结离线对比；`DeepPlanning` 的思路是
**把"规划"与"执行结果"分开评估**——这正是本报告 P1-3 需要的。
（具体任务格式与评分口径需在实施前单独核实。）

**第二条借鉴**：README 变更日志显示它**为模型反复重调 Function Call 模板**。
这与 Claude Code 的 ACI 结论一致（4.2.2）：**工具调用的可靠性主要花在接口设计上，不在 Prompt 措辞上。**

#### 额外复杂度（不应照搬）

- 通用工具生态（code interpreter / browser / image gen）：与 API 测试无关，且 code interpreter
  涉及"执行模型产出的代码"，与 AGENTS.md 第 5 条直接冲突。
- 自身的长上下文/并行文档 QA 方案（MiniMax 风格）：AgentTest 的上下文问题是"该不该放"，
  不是"放不下"。

---

### 4.5 腾讯 Youtu-Agent

**证据深度**：官方 README（HTTP 200）+ 论文 arXiv 2512.24615、2510.08191。

#### 核心工程思想

1. **不自己写 Agent loop，而是建在 `openai-agents` SDK 之上。**
   README：`"Flexible architecture: Built on openai-agents, with extensible support for diverse
   model APIs..."` —— 循环、流式、tracing 来自依赖库，本项目专注 Agent/Toolkit/Environment/
   **ContextManager**/Benchmark。
   > 对 AgentTest 的直接含义：**"复用已有循环"是行业常规做法，不必自研循环。**
   > AgentTest 的"已有循环"就是 LangGraph。
2. **自动化 Agent 生成**：Workflow 模式（标准任务）与 Meta-Agent 模式（复杂需求），
   自动生成工具代码、Prompt 与配置，声称工具合成成功率 >81%。
3. **持续经验学习（Agent Practice）**：基于 Training-Free GRPO（arXiv 2510.08191），
   "Agents accumulate experience and improve performance through **in-context optimization
   without parameter updates**"，并给出 +5.4% on AIME 2025 的量化。
   **这是六个项目里唯一认真回答"失败经验能否变成长期可复用知识"的机制。**
4. **Agent RL**：端到端强化学习流水线（128 GPU、40% 训练加速）。与研究目标无关。
5. **2026-01-17 支持 Agent Skills**，README 明说 "inspired by Anthropic's Claude Code skills"。
   ——说明 Skills 已是跨厂商的事实标准，但也说明它**是可选件**。
6. **基准成绩**：WebWalkerQA 71.47%、GAIA 72.8%（纯开源权重模型）。

#### 已实现 vs 概念设计

| 能力 | 判断 |
|---|---|
| Agent + Toolkit + ContextManager + Benchmark | 已实现（README 一级概念，有完整 benchmark 结果支撑） |
| 经验学习 / Training-Free GRPO | 有论文 + 模块名 + 量化结果，**已实现**；但它是"in-context 优化"，本质是把经验写进上下文，不是训练 |
| 自动生成工具代码 | 声称 >81% 成功率；对 AgentTest **不可用**（AGENTS.md 禁止模型生成代码执行） |
| Agent Skills | 2026-01 新增，属可选扩展 |
| Agent RL | 已实现但与本研究无关 |

#### 对 AgentTest 的适用性与借鉴价值

**借鉴价值 1（正面）："经验"的正确形态是"可重算的上下文片段"，不是散文。**
Training-Free GRPO 的本质是**把成功/失败轨迹提炼成 in-context 的经验条目**，
在推理时注入。它没有更新参数，也没有建向量库。
这恰好支持本报告 5.6 的设计：**经验 = 从真实证据算出来的结构化事实**，
而不是"模型总结的一段话"。区别在于 AgentTest 必须把"谁算的"钉死为代码。

**借鉴价值 2（负面，但同样重要）：它证明了"经验学习"需要一整套评测才能成立。**
Youtu-Agent 敢说 +5.4%，是因为它有 AIME / GAIA / WebWalkerQA 这样的外部基准。
AgentTest 若要说服自己"自主测试能力提升了"，**必须先有 P1-3 的指标体系**，
否则任何"经验层"都是不可证伪的装饰。

**借鉴价值 3（已修正——这是一条"README 与代码不符"的实例）：**
本报告最初把 `ContextManager` 写成一等概念并建议借鉴。**逐行核对源码后该判断被推翻：**
`utu/context/base_context_manager.py` 只有约 40 行的**消息注入钩子**，
`BaseContextManager.preprocess()` 的全部工作是"追加一条提示"或"前置环境 system prompt"，
**完全没有任何上下文裁剪、压缩、摘要或滑动窗口**。
相关证据：全仓 grep `truncat|compact|summariz|sliding|keep_last` 在 `utu/` 下的命中
全部在展示层（`print_utils.py`）、分词工具（`token.py`）与沙箱工具输出（`swerex_env.py`）。
唯一带"压缩"名字的工具 `CompactifyMemoryToolkit` 在 `return` 之前
`raise NotImplementedError`，而它的类文档字符串却声称"Applies the context manager's
truncation strategy to compress the conversation history"。
真正起作用的只是 `filters.py` 里的一句**追加式提示**：
"你已超出 token 预算，请不要再使用任何工具，立即给出最终答案。"

> **这条对 AgentTest 的价值不在你图，而在方法论**：它和本项目
> `planning-ir-production-activation.md` 记录的教训是同一个形状——
> **"名字叫 X"不等于"做了 X"**。AgentTest 自己的
> `planning_engine_conditions` 布尔审计必须继续做，并且应扩展到任何新增模块。

#### 额外复杂度（不应照搬）

- 自动生成工具代码：与"不执行模型产出代码"直接冲突。
- Agent RL / 分布式训练：与目标无关。
- 完整 Benchmark 套件（WebWalkerQA/GAIA）：AgentTest 需要的是**API 测试领域的评测集**，
  不是通用 Agent 基准；照搬只会得到与自己无关的分数。

---

### 4.6 字节 Coze Loop

**证据深度**：官方 README（HTTP 200）+ 官方 wiki Quickstart。

#### 核心工程思想

1. **它不是 Agent 框架，是 Agent 的 LLMOps 平台。** 语言是 **Go（>=1.24.0）**，
   基于 CloudWeGo / Eino，Apache-2.0，Docker Compose 或 Helm 自托管。
2. 三大模块：
   - **Prompt 开发**：可视化 Playground 调优、多模型输出对比、**版本管理**；
   - **评测**：评测集管理、**评估器（evaluator）管理**、实验（experiment）管理，
     对输出做多维度自动化测试（accuracy / conciseness / compliance 等）；
   - **可观测**：SDK trace 上报 + trace 数据观察，README 原文：
     "fully recording every stage from user input to AI output, including key stages such as
     prompt parsing, model invocation, and tool execution, and automatically capturing
     intermediate results and exceptions."
3. 支持 OpenAI / 火山方舟等模型接入。

#### 已实现 vs 概念设计

| 能力 | 判断 |
|---|---|
| Trace 采集与观察 | 已实现（SDK 上报 + 平台展示，是产品核心） |
| 评测集 / 评估器 / 实验对比 | 已实现（README 功能表的三大项之一） |
| Prompt 版本管理 | 已实现 |
| Agent 执行循环 / 工具协议 | **不在本项目范围**（它不提供 Agent 运行时） |

#### 对 AgentTest 的适用性与借鉴价值

**借鉴价值（对 P1-3 最直接）：它给出了"客观评价 Agent"的四个必需件：**
**评测集 → 评估器 → 实验对比 → 轨迹观察**。

AgentTest 的处境很具体：
- "评测集"**已有**（冻结请求、`eval_v2` 的 suite），只是面向历史对比；
- "评估器"**已有雏形**（`eval_v2/scoring.py` 的 rubric 评分），但没接进产品；
- "实验对比"**已有**（`eval_v2/comparison.py`），同上；
- "轨迹观察"**部分**（`local_trace.py`，无任务级 trace id）。

**所以 Coze Loop 的启示不是"引入它"，而是"把已有的四件接起来"。**
这也再次印证本报告 9.2 的结论：AgentTest 缺的是连接，不是能力。

第二个启示：**评估器必须是"多维度"而不是单一 PASS 率。**
Coze Loop 的 accuracy/conciseness/compliance 三分法，对应到 API 测试就是
本报告 8.5 的"自主收敛率 / 业务断言 PASS 率 / 覆盖依据率"必须成组看。

#### 额外复杂度（不应照搬）

- 整套平台（Go 服务、Postgres/MySQL、对象存储、Helm）：为本地单人工具引入运维成本。
- Prompt 版本管理：AgentTest 的 Prompt 是代码常量，git 已经是版本管理。
- 自托管平台的多租户与权限体系：与"本地、单用户、操作员即审批人"的定位冲突。

---

### 4.6.5 源码级复核：四个项目的"文档声称 vs 代码实际"

上面 4.3–4.6 主要依据 README 与官方文档。为避免"照 README 推断能力"（正是本报告
第 2.6 节点名的风险模式），对四个仓库的 `main` 分支做了**逐文件核对**
（代码树条目数：youtu-agent 735 / agentscope 1487 / Qwen-Agent 417 / coze-loop 7418，均未截断）。

| 项目 | 文档/名字所声称 | 代码实际 | 判定 |
|---|---|---|---|
| youtu-agent | `ContextManager` 管理上下文 | 约 40 行**追加式注入钩子**，无裁剪/压缩/摘要；唯一的"压缩"工具 `CompactifyMemoryToolkit` 在 return 前 `raise NotImplementedError` | **名不符实** |
| youtu-agent | "Training-Free GRPO" | 无梯度、无策略优化；用"组内奖励方差"仅做**样本筛选**，再让 LLM 出 ADD/UPDATE/DELETE 编辑指令改一份文本经验集 | **品牌名 > 实现** |
| youtu-agent | 框架能力 | 循环/流式/tracing 全部来自 `openai-agents==0.10.4`；自带 `ReactRunner`（596 行）`from agents._run_impl import ...` **读取 SDK 私有内部**，且无任何 shipped 配置启用、无测试 | **升级风险最高** |
| agentscope | 不约束模型 | 循环确无内建 Prompt 模板（`system_prompt` 必填、无默认），但脚手架带工具组激活协议、强制结构化输出、极规定性的压缩 Prompt | **大体属实（限循环）** |
| Qwen-Agent | 工具调用健壮 | `_verify_json_format_args` 是基类**可选**助手；20 个工具模块中只有 11 个调用它；未知异常连同**完整 traceback 回灌给模型** | **部分属实** |
| Qwen-Agent | 循环 | `MAX_LLM_CALL_PER_RUN=20` 用尽后**静默**退出，无异常、无 `finished_reason`、无标记消息 | **静默截断（缺陷）** |
| Coze Loop | 评测/可观测 | 三项均为真实 Go 代码（span 表、evaluator 四类型、`BaseExptID` 基线对比） | **属实** |

#### （1）youtu-agent 的"经验学习"到底做了什么（六个项目里唯一真实实现）

流程确实接线、可运行（`scripts/run_training_free_GRPO.py` + `configs/practice/web_search.yaml`）：

```
rollout（每题多次、温度 0.7）
  → 按题分组，只保留 0 < 组内平均奖励 < 1 的组（全对/全错都不学）
  → judge LLM 对比同题好坏轨迹，抽取 <Experiences>
  → LLM 产出 JSON 编辑指令 ADD / UPDATE / DELETE（可引用已有 id），Python 应用
  → 合并为新的经验集，重新编号 G0..Gn
  → 写入 SQL 表 cache_experience(experiment_name, step, experiences JSON)
  → 最终把整份经验列表拼进 agent.instructions 并落成 YAML 配置
```

产物形态（仓库里的 `configs/agents/practice/web_practice_agent.yaml` 实存 **46 条**
`[G0]`–`[G45]` 的祈使句经验）：

```yaml
agent:
  instructions: |
    ...（基础说明）
    When solving problems, you MUST first carefully read and understand the helpful
    instructions and experiences:
    [G0] Verification methods: Implement multiple independent approaches ...
    [G4] URL prioritization: Immediately explore relevant URLs ...
```

关键判断，逐条说清：

- **有长期知识吗？有，但是"烘进系统提示词"，不是"检索"。**
  全仓 grep `embedding|vector_store|faiss|similarity_search|chromadb` → **零命中**。
  每条查询都注入**全部 46 条**，无相关性排序、无任务域裁剪、无置信度、
  **无任何反向指针指向产生它的那条轨迹**。文件里还存在重复标题（"Uniqueness verification" 出现两次），
  说明 LLM 驱动的去重很弱。
- **它诚实吗？部分是。** 论文的 +5.4% 无法在本报告范围内复现（`UNVERIFIED`）；
  且"GRPO"只是用奖励方差做筛选，没有任何策略优化目标。
- **对 AgentTest 的可迁移点只有一条，而且必须改造后才可用：**
  "**只从成败混合的样本里学**"这个筛选思路便宜且合理；
  "**把学到的经验落成可 diff、可 review 的仓库文件**"比塞进数据库更符合本项目的审计习惯。
  但**不能**照搬"自动注入系统提示词"——那会与 AGENTS.md 第 7 条
  （未知期望→`NEEDS_REVIEW`，不得凭空生成）直接冲突。
  正确形态见 5.6：**经验 = 代码从 `report.json` 算出来的结构化聚合，人审后才进上下文。**

#### （2）AgentScope：三处真正值得抄的源码级设计

| 机制 | 真实位置与做法 | 对 AgentTest 的含义 |
|---|---|---|
| **工具参数修复是 call-site 强制的** | `_json_loads_with_repair(tool_call.input, tool_func.input_schema)`：用 `json_repair.repair_json(..., schema=..., stream_stable=True)` 做**按 schema 引导的容错解析**，并显式拒绝 `NaN`/`Infinity`（理由：它们会"静默绕过 minimum/maximum 约束"）；修不动的原样交给工具的 validator 去报错 | 这正是本报告 8.4 与 4.2.9 想要的 **poka-yoke**。AgentTest 的 `diagnostics.ValidationFailure` 可以照此升级：**先尝试按 schema 修复，再拒绝**，并在错误里给出合法取值范围 |
| **工具分组的激活协议** | 调用未激活组的工具返回 `ToolGroupInactiveError: ... You should first activate the group by calling the '<meta tool>' tool.` | **错误消息即接口**（4.2.2 同结论）的又一例证：错误里直接告诉模型下一步该调什么 |
| **压缩是结构化产物 + 失败阶梯** | 触发线 `trigger_ratio=0.8 × context_size`；保留 `reserve_ratio=0.1`；工具结果上限 50,000 tokens；摘要是**固定 schema**（`task_overview / current_state / important_discoveries / next_steps / context_to_preserve`）经模板渲染；失败时逐级降级：先丢最老消息重试 → 再退化为截断并注入 `<system-info>Some earlier messages were truncated...</system-info>`；若摘要是被中断的则 `raise asyncio.CancelledError()` | 若 P1-1 将来真需要压缩，**这个 5 段 schema 是可直接借用的、且与"证据/待办"天然对齐的形状**。另注意其真实失效模式：当 system prompt 本身超过阈值时直接 `RuntimeError`，说明**压缩不能替代"一开始就别塞满"** |
| **溢出写盘 + 提示路径**（而非静默截断） | 工具结果超 `tool_result_limit` 时按 token 比例切成"保留段/溢出段"，溢出段交给 `offloader` 落盘，并在上下文里追加**该文件路径**，模型可再读 | 与 Claude Code"大文件只回路径引用"同一思想；AgentTest 已有工件机制，可直接采用 |
| **人机确认是可续跑的状态机** | `ToolCallState: PENDING → ASKING → ALLOWED/SUBMITTED → FINISHED`；`reply` 可带 `RequireUserConfirmEvent` **停住并跨进程恢复**，用 `UserConfirmResultEvent` 续跑 | AgentTest 现在是"操作员事前批准操作集"，不需要中途暂停，**暂不适用**；但这是将来做"中途审批写操作"的正确参考 |

另注：其权限引擎把 `bypass_immune` 作为工具可设置的"永不可自动放行"标记
（BYPASS 模式下仍生效），并把规则匹配按工具类型区分（shell 用子串、文件用 glob）。
AgentTest 现在只有布尔式 allowlist，**"某些操作在任何模式下都不得放行"这一位是缺的**
（例如真实业务写）。这是 P2 可以补的一个小位，而不是引入整套引擎。

#### （3）Qwen-Agent 的 `DeepPlanning`：本项目最该抄的**评测形状**

形状比分数重要。它的设计对本报告 8.5 的指标体系有直接改造价值：

- **任务文件带机器可校验的约束**，而不是一段自然语言参考答案。
  购物域把约束写在 query 里，但真值单独物化为 `validation_cases.json`
  （`ground_truth_products[].product_id` + `ground_truth_coupons{name: qty}`）；
  旅行域更进一步，`meta_info.hard_constraints` 是**带类型的对象**：
  `{"constraint_type": "superlative_star_service_required", "hotel_name": ...,
    "required_service": "Swimming Pool", "hotel_star": 3, "hotel_price": 441.0}`。
  → 与 AgentTest 的 Planning IR 是同一个思想，但**用于判分**。
  **建议：把"验收场景"写成这种带类型的断言对象，而不是写一段中文期望。**
- **双指标并存**：每题同时给
  `score = 命中数/期望数`（部分分）与 `case_score = 1.0 if 全中 else 0.0`（严格分）。
- **把"多做了"和"没做完"都当一等指标**：
  `extra_products_count`（多余产物）、`unmatched_ground_truth_products`（漏项）、
  `is_completed`（最后一条消息是否还带 `tool_calls`——即**是被预算砍断还是自己停的**）。
- **硬约束一票否决 + 8 个软维度等权加权**：
  软维度为路线一致性、沙箱合规、行程结构、时间可行性、营业时间、时长合理性、
  费用计算正确性、活动多样性（各 0.125）；硬约束 `score = 1.0` 当且仅当全部通过。
  → 对应到 AgentTest 就是"**安全/权限类判定一票否决，质量类判定加权**"。
- **必须记下的反面教材**：旅行域评分要先用 **LLM 把 Markdown 计划转成 JSON**
  再校验（`convert_report.py`，默认 `qwen-plus`）。
  这是全项目最脆弱的一环，且与本项目"确定性优先"直接冲突 → **不采用**。
- 另一个实现缺陷：`_calculate_pass_at_k_for_problems` 空集返回 tuple、正常路径返回 dict，
  形状不一致。**指标函数的返回形状必须稳定**，否则聚合会静默出错。

#### （4）Coze Loop：把"观测"拆成三个对象，是本节最值得学的架构

它的核心不是平台，而是**三个问题各自一个对象**：

| 对象 | 回答哪个问题 | 关键字段（真实字段名） |
|---|---|---|
| `Span` | **发生了什么** | `trace_id / span_id / parent_id / span_type / call_type / status_code / start_time / duration / input / output / object_storage`，加三层标签：固定字段 + `system_tags_*`（框架算）+ `tags_*`（用户自定义），每层再分 string/long/double |
| `EvaluatorRecord` | **判定说了什么** | `EvaluatorVersionID / SourceType(legacy|builtin|inline) / Alias / InlineKey / TraceID / EvaluatorResult{Score, Correction, Reasoning} / EvaluatorUsage{tokens} / EvaluatorRunError / Stdout` |
| `Annotation` | **我们信什么，凭什么** | `AnnotationType(auto_evaluate|manual_feedback|...) / Key / Value / Reasoning / Corrections[] / Status / CreatedBy` |

四条可迁移的具体做法：

1. **更正不覆盖原判**：`AnnotationCorrection{Reasoning, Value, Type ∈ {llm, manual}, UpdatedBy}`
   累积保存。**这直接满足本项目"不自动改写断言"与"保留全部历史失败"两条硬规则**：
   人可以把某个 `ASSERTION_FAILED` 标注为"测试缺陷"，但原判定仍在。
2. **标注 id 确定性生成**：`sha256("{span_id}:{trace_id}:{annotation_type}:{key}")`
   → 重跑同一次评测是 **upsert 而不是插入重复项**。对 AgentTest 的
   "任务重跑/续轮"场景，这一位几乎零成本却能避免重复判定。
3. **多聚合器共存 + 分布是一等公民**：
   `AggregatorType ∈ {Average, Sum, Max, Min, Distribution}`，
   `ScoreDistributionItem{Score, Count, Percentage}`、`BooleanDistributionData{TrueCount, FalseCount}`。
   → **别只报"通过率"，要报结论分布**（见 8.5 与 P1-3）。
4. **对比是基线相对的列式模型**：`MGetExperimentResultParam{ExptIDs[], BaseExptID}`，
   每行返回"参与对比的实验序列"，`ExptID != BaseExptID` 的一侧即为差异方。
   → AgentTest 的 `eval_v2/comparison.py` 已有类似能力，**接入即可，无需重写**。

`Span` 的 `span_type` 词表（`prompt / model / parser / embedding / memory / plugin /
function / graph / remote / loader / transformer / vector_store / vector_retriever /
agent / tool / LLMCall`）可以直接裁剪成 AgentTest 的词表：
`plan / model_call / http_request / assertion / tool / investigation`。

#### （5）代码里发现的具体陷阱（作为"不要照抄"的依据）

| 项目 | 陷阱 |
|---|---|
| youtu-agent | `ReactRunner` 依赖 `agents._run_impl` 私有 API（升级即碎）；`CompactifyMemoryToolkit` 用 `NotImplementedError` 兜底却写着能压缩；`_calculate_pass_at_k_for_problems` 返回形状不一致 |
| youtu-agent | `utu/practice/README.md` 宣传一个并不存在的 `dataset/` 目录（文档漂移） |
| Qwen-Agent | 20 次调用上限**静默**退出；工具参数校验**逐工具可选**（20 个里 11 个）；异常把完整 traceback 回灌给模型 |
| coze-loop | 同一个量在实体里叫 `DurationMicros`、在 ClickHouse 列里叫 `duration`；`TagsDouble` vs `tags_float` —— **命名缝**，照抄会把缝一起抄进来 |
| agentscope | system prompt 自身超压缩阈值时直接 `RuntimeError` |

> **对本项目的净结论：四个国内框架里，没有一个实现了本项目尚缺的"长期记忆"，
> 也没有一个实现了"无进展看门狗"。** 
> 最有价值的三件东西都是**形状**而不是能力：
> AgentScope 的"按 schema 修复参数 + 错误消息即接口"、
> Qwen-Agent 的"部分分 + 严格分 + 多余项 + 是否被砍断"四元组、
> Coze Loop 的"span / 判定 / 标注"三层分离与不覆盖式更正。

### 4.7 六个项目横向对照

| 维度 | DeepSeek Harness | DeepSeek 原型 | Claude Code | AgentScope | Qwen-Agent | Youtu-Agent | Coze Loop |
|---|---|---|---|---|---|---|---|
| 本质 | Agent 运行时 | 跨会话编排器 | 产品级 Agent | Agent 框架 | Agent 框架 | Agent 框架 + 训练 | LLMOps 平台 |
| 语言 | TS | TS | TS(闭源) | Python | Python | Python | **Go** |
| 有回边的 Agent loop | 有（step/turn） | 无（定时器+队列） | 有（官方明示） | 有（ReAct） | 有（FnCall/ReAct） | 有（继承 openai-agents） | 无（不提供运行时） |
| **是否有"无进展 watchdog"** | **无** | **无（仅文章提及）** | 未见官方描述 | 未见 | 未见 | 未见 | 不适用 |
| 上下文策略 | 日志投影 + 压缩插件 | Prompt 文件 + 分页上限 | 压缩表 + 渐进披露 | **结构化摘要(5段)+offload+失败阶梯** | **无压缩**（仅 json5 容错） | **无压缩**（仅注入钩子） | 不适用 |
| 长期记忆 | 无（会话级） | 无 | CLAUDE.md + auto memory | 可切换后端（未核） | RAG（文档，非经验） | **经验学习（唯一真实实现，但烘进提示词）** | 不适用 |
| 评测能力 | 无 | 有 Eval（Gate+Assessment）概念 | 有 `/goal` 核对 | 有 benchmark | **DeepPlanning（部分分+严格分+is_completed）** | 有 benchmark（LLM judge） | **最完整（span/判定/标注三层）** |
| 对 AgentTest 最大的借鉴 | 日志即上下文 | Mandate + Effects 幂等 | **Harness 持有"完成"定义** | schema 引导修复 + 错误即接口 | 评测四元组 + 类型化约束 | 只从混合样本学 + 可 diff 产物 | 三层分离 + 不覆盖式更正 |
| 整体照搬的风险 | 高（插件体系） | 高（六件套） | 高（多 Agent/压缩） | 中（服务化/中间件） | 中（工具生态） | **高（私有 API/静默截断）** | 高（平台运维） |

**一句话横向结论：**
> 六个项目里，**没有一个实现了"模型驱动的无进展看门狗"**——
> 被广泛引用为"DeepSeek 有 Watchdog"的说法，来自配套文章而非代码。
> 因此 AgentTest **不应该去发明这个东西**，而应该用确定性预算与上限（8.4）。
>
> 六个项目里，**也没有一个实现了 AgentTest 尚缺的"长期记忆"**。
> youtu-agent 是唯一认真尝试的，而它做的其实是"把经验烘进系统提示词"，
> 既不检索、也无出处。**这降低了"记忆"这件事的紧迫性，也提高了"如果要做得更好"的门槛。**
>
> 真正值得抄的只有五条：**Claude Code 的"完成定义在 Harness"、
> DeepSeek 的"先存意图再执行副作用"、
> AgentScope 的"按 schema 修复参数 + 错误消息即接口"、
> Qwen-Agent 的"部分分/严格分/多余项/是否被砍断"评测四元组、
> Coze Loop 的"span / 判定 / 标注"三层分离且更正不覆盖**。
> 其余全部应归入"过度工程"。

---

## 5. 对照"真正的测试同事"：七个真实差距

本节回答第四节的问题。每一项都给"现在是什么""差在哪""最小改法"，并明确**能不能复用现有代码**。

### 5.1 自主测试范围选择：用户说"帮我测一下包裹管理功能"，Agent 怎么知道要覆盖什么？

**现在是什么：**
- 语义映射**是存在的**：Focus 在 `allowed_operations` 内按 `summary/path/参数/已批准策略`
  选择 `operation_ids`，并且 `FOCUS_PROMPT` 明确要求"只选语义上属于该功能的操作，
  不要加健康检查"（`product_focus.py:263-265`）。
  L2 真实用例"只测试包裹功能…不要调用可用性接口" 通过，证明这条链路有效。
- **但"测试价值排序"不存在。** 选完范围后，`design_groups()`（`product_design.py:449`）
  只是把操作按出现顺序切成 ≤6 个一批，`supported = len(included) <= 12`。
  它对每个操作的"测试价值"完全无感：一个只读列表接口和一个"删除+级联"接口被同等对待。

**差在哪：** 缺一层"**候选测试目标 + 价值证据**"，于是模型只能从一张扁平的操作清单里凭语义挑，
而不是从"值得测的东西"里挑。

**最小改法（复用现有代码）：**
`discovery.py` 已经能静态算出 `CoverageRatio / OperationCoverage / PolicyCoverageRow /
ScenarioSlot`（`discovery_models.py`），并且已被 `goal_suite.py`、`project_inventory.py` 使用。
把它的输出作为 **Focus 的候选面**注入即可——不需要新模块，只需要 `ProductTasks` 调一次
`discover()`，把 `PolicyCoverageRow`（哪些操作有已批准断言、哪些只有状态码、哪些完全没有规则）
和依赖度一起投进 context。

价值排序可以用**确定性代理指标**，无需模型打分：出度/入度（依赖被多少操作需要）、
是否有已批准字段断言、是否可变（`MUTATION_LIFECYCLE_REQUIRED`）、是否需要跨角色。
这与"不为技术名词增加基础设施"一致。

### 5.2 自主设计测试用例：九类检查，现在支持几类？

`DesignCase.kind` 只有五种（`product_design.py:25`：`normal/boundary/negative/permission/flow`），
但真实覆盖面比这个名字宽。逐类核对：

| 任务要求的检查类 | 现状 | 证据 |
|---|---|---|
| 正常业务流程 | **支持** | `kind=normal`；`required_normal_operations` 强制每操作至少一个正常步骤（`product_design.py:102-104`） |
| CRUD 生命周期 | **支持** | `target.resource_lifecycles` 进 IR（`product_tasks.py:45`）；`ResourcePhase.EXISTS/DELETED/VERIFY_ABSENT`（`planning_ir.py:34-40`）；L1 真实通过 |
| 边界值 | **部分** | 只能走已批准 policy 的约束（`product_checks.RequestedCheck` / `boundary_obligations.py`）；模型不能凭空造边界值。L3"数量 0 → 422"通过，但依赖事先批准 |
| 错误输入 | **部分** | 同上，必须命中已批准 negative policy；`product_design.py:107` "A negative case always needs an actually approved negative policy, not a title alone" |
| 权限/身份差异 | **支持** | `kind=permission` + `target.roles` + `ExpectationQuestion(kind="permission")` 强制 `{403,404}` 二选一（`product_focus.py:39-43`） |
| 资源不存在 | **支持** | `ConstraintKind.RESOURCE_ABSENT`、`RESOURCE_DELETED`（`planning_ir.py:49-50`）；L3 删除后 404 真实通过 |
| 资源状态变化 | **部分** | 有 `phase` 与 lifecycle，但"状态机中段状态"（如 pending→paid）没有表达方式 |
| 跨接口依赖 | **支持** | `ConstraintKind.SAME_RESOURCE / DATA_DEPENDENCY`、`policy.binding_rules`、`design_groups` 的闭包 |
| **回归风险推导** | **缺失** | 没有任何代码能从"本次改了哪里"推出"该重测哪里"；`DEFAULT_FOLLOWUP` 只按"上次未完成"续，不按"变更影响面"续 |

**"区分可靠断言依据 vs 仍需人工确认"——这是 AgentTest 的强项，应保留并强调：**
- `RequestRequirement.disposition ∈ {required, needs_review}`；
- `rule_review_scope()` / `review_only_operations()` / `permission_review_scope()`
  （`product_focus.py:99-180`）——业务规则缺口**不允许**降级成普通请求；
- 未知期望产出 `NEEDS_REVIEW` 而非 PASS（AGENTS.md 第 7 条，`report.py:28`）。

> 这一条恰好是 Claude Code 那条核心结论的正向印证：
> **Harness 持有"什么算完成"的定义。** AgentTest 在"断言依据"这件事上已经做到了，
> 只是还没把同一纪律用到"测试是否足够"上。

### 5.3 自主调用工具：现在是不是"固定链路里只能走既定步骤"？

**是固定链路，但不是死链路。** 精确表述：

| 任务要求的工具动作 | 现状 | 证据 |
|---|---|---|
| 查看 OpenAPI | **固定发生**（每次 Focus/Design 都注入收窄后的契约） | `planner_input.py:44`、`product_focus.py:460-478` |
| 查询接口信息 | 同上 | 同上 |
| 查询已批准测试规则 | **固定发生** | `planner_input.py:46` `registry.catalog()` |
| 查询历史 Bug / 测试结果 | **部分**：只有同任务 `previous_result` | `product_focus.py:396-402` |
| 生成测试方案 | **固定 1 次/组** | `product_design.py:568` |
| 执行测试 | **固定**：每个 case 一次 `Workflow` | `product_tasks.py:885` |
| 查看失败记录 | **失败后才发生，且只读** | `executor.py:273-290` |
| **自主决定"先查哪个、要不要再查"** | **仅调查阶段成立，≤2 次** | `product_probe.py:111-188` |

所以准确的答案是：
> **规划阶段没有工具选择（信息是预先塞满的）；只有"善后阶段"有工具选择。**

这对测试同事而言是错配的：**人真正需要选择工具的时机恰恰是在规划阶段**
——"这个接口的规则我不确定，先查一下已批准策略"、"这个字段历史上有过 bug，先看记录"。

**最小改法：** 不要在规划阶段上开放式 function calling（会破坏闭合枚举带来的安全性）。
正确做法是把"**已存在的内部能力**"做成一个**闭合枚举的下一步菜单**，让模型选菜单项，
由确定性代码执行。这正好复用 `product_probe.py` 已验证的形态：

```python
class NextStepChoice(StrictModel):          # 新增，模仿 ProbeChoice
    step: Literal["DESIGN_MORE", "READ_PROBE", "ASK_QUESTION", "FINISH"]
    target: str | None      # 只能是本次未被覆盖的 operation_id 或已存在 question id
    reason: str             # 面向用户的一句中文
```

菜单项全部对应**已有函数**：`DESIGN_MORE → propose_design`、`READ_PROBE → investigate_product`、
`ASK_QUESTION → 已有的 DesignQuestion 通道`、`FINISH → coverage()`。
**没有一个是新工具**，所以不增加基础设施，也不扩大权限面。

### 5.4 测试后的决策循环：六种判断，现在有几种？

| 任务要求的判断 | 现状 | 谁做的 |
|---|---|---|
| 已达标，可以结束 | **有计算，无决策**：`coverage()` 算出 `complete` 并写 `FINISHED` | 确定性代码 |
| 发现异常，需要受控复核 | **有**，≤2 次只读 | 模型（`investigate_product`） |
| **覆盖不足，需要追加测试** | **缺失** | —— |
| 前置资源缺失，需合法准备数据 | **部分**：产出 `input_questions` 让人补 | 模型提，人补 |
| 缺少业务规则，需询问用户 | **有** | 模型（`ExpectationQuestion`、`DesignQuestion`） |
| 风险/成本过高，需停止 | **有，但纯确定性**：`SuiteLedger`、`usage_unknown`、超时 | 确定性代码 |

**结论：六项里 1 项缺失（追加测试）、4 项部分、1 项完整。**
缺的那一项恰恰是"自主"二字的实质：**自己发现测得不全，并自己补。**

**边界必须写清楚（任务明确要求）：** 无论怎么改，
- 决策只能落在**计划空间**（选哪些已批准操作/策略/角色），**不能落在 HTTP 空间**；
- `Executor` 仍是唯一发请求的地方；
- 权限闸门（`AmbiguityKind.OPERATION_NOT_APPROVED`、`RESPONSE_STATUS_NOT_APPROVED`）不因决策而放宽；
- 追加测试必须重新过 `validate_design_case` 预检（`product_design.py:876`）。

### 5.4.1 一次"人 vs Agent"的闭环对比（说明差距在哪一步）

```
现在： 用户请求 → Focus → IR → 分组 → Design → 预检 → 执行 → 覆盖报告 → 用户读报告
                                                                        │
                                                    用户判断"还差什么"，再提交一次
                                                    （"补充澄清"/"补充测试需求" 按钮）
        ↑ 这一跳由人承担 = 人与 Agent 之间的循环，不是 Agent 内部循环

目标： 用户请求 → [ Agent 循环：选范围 → 设计 → 预检 → 执行 → 读覆盖 → 决定下一步 ]
                              ↑______________________________________________│
                     预算 + 权限 + 连续无进展上限，全部确定性
```

### 5.5 上下文工程：如何避免把所有东西塞进每次 Prompt？

**现在是"全量注入 + 硬上限报错"（数字已实测）：**
- `FOCUS_PROMPT` **145 行**规则常量（`product_focus.py:259-403`）；
- `DESIGN_PROMPT` **179 行**（`product_design.py:79-257`）；
- Focus 的 context **约 25 个顶层 key**（`product_focus.py:590-663`），其中 `grouped_index`
  包含**全部组、全部操作**的 id/method/path/summary/参数类型（`:584-589`）；
- 硬上限：100 KB（`planner_input.py:120`）、20k input tokens（`project_goals.py:61`）；
- 超限**直接抛错**，不截断、不摘要。

对照组是真人在 Claude Code 里看到的成本结构（4.2.3）：
`CLAUDE.md` 每次全量（因此要求 <200 行）、Skills 只加载描述、MCP schema 延迟到需要时。

**已经做对的部分（不要改）：**
- Design 按组注入，只给**该组**的操作（`design_context`，`product_design.py:523`）；
- 策略按场景/角色收窄（`planner_input.py:60-62`）；
- 私有字面量检测与拒绝（`planner_input.py:13-17`、`:122-130`）。

**最小改法（三步，全都不引入新基础设施）：**

1. **把巨型 Prompt 拆成"不变量 + 按 case 族加载的片段"。** 本质就是 Skills 的渐进披露，
   用 Python dict + 字符串拼接实现即可：
   ```python
   PROMPT_CORE = "..."            # 每次都发：权限边界、格式、纪律
   PROMPT_BY_KIND = {"negative": "...", "permission": "...", "flow": "..."}  # 只发本组用得到的
   ```
   收益可测：`record_bytes` 已在 `report.ModelCallStats` 里（`product_tasks.py:66`），
   改动前后直接比。
2. **观测压缩 + 工件句柄。** 现在 Design/续轮看到的只有计数与状态；应改成
   "摘要 + `run_id:step_id` 句柄"，需要细节再按句柄读 `report.json`。
   这正好对应 Claude Code"超 5,000 tokens 的文件只回路径引用"。
3. **让"因上下文超限而失败"成为可观测指标。** 现在 `planning_context` 超限是
   `ValueError("Planner context too large")`，应落进报告分类，否则永远不知道上下文是否够用。

### 5.6 长期测试记忆：必须区分三类知识

任务特别要求"必须区分可信业务知识、历史测试证据与模型总结，防止把模型猜测长期保存为事实"。
AgentTest 恰好已经有一条干净的分界线，应该**沿着它**扩展，而不是新建存储：

| 知识层 | 定义 | AgentTest 现状 | 存储位置 | 谁可以写 |
|---|---|---|---|---|
| **可信业务知识** | 操作员批准的契约、策略、身份、生命周期 | **已有且严格** | `.json` policy registry + OpenAPI + `target.resource_lifecycles` | **只有人**（`PolicyRegistry`、`assertions.reviewed`） |
| **历史测试证据** | 真实执行过的断言结果、失败分类、用量 | **已有但不可查** | `runs/<run_id>/report.json` + SQLite `task_events` | **只有 Executor** |
| **模型总结** | Agent 自己写的"经验""猜测""规律" | **不存在**（这是好事） | —— | 若引入，必须标记为不可信 |

**推荐的最小实现（复用现有代码，零新存储）：**
新增一个**只读派生层** `operation_history.py`：
- 输入：已有的 `ReportStore.list_runs()`（`report_queries.py:73`）+ `report.json`；
- 输出：每个 `operation_id` 的确定性事实聚合
  `{last_outcome, consecutive_failures, last_seen_contract_sha256, last_asserted_selectors, run_ids[]}`；
- **不存任何模型文本**；如果要展示模型总结，单独放一个 `hypothesis` 字段并在 UI 上标"未验证"。

这直接回答任务的六个问题：

| 任务问题 | 这个派生层怎么答 |
|---|---|
| 某接口过去出现过什么问题 | `last_outcome` + `consecutive_failures` + 对应 `run_id` |
| 某流程已经被验证过 | 按 `scenario_sha256` / 已执行 policy 集合聚合 |
| 某类测试经常失败 | 按 `policy_template_sha256` 聚合 `ASSERTION_FAILED` 率 |
| 当前版本新增哪些回归风险 | 对比 `contract_sha256` 变化 + 受影响操作的历史失败 |
| 哪些业务预期仍未确认 | 已有的 `questions` / `needs_review`（`product_focus.py`、`product_design.py`） |
| **防止模型猜测变事实** | 结构上做到：这个层的每个字段都由代码从 `report.json` 算，模型**无权写入** |

> 这条设计比 Youtu-Agent 的"经验学习"更朴素，但更符合 AGENTS.md 第 7 条。
> 经验**不是**存下来的散文，而是**可重算的聚合**。

### 5.7 工程可靠性：现状与真实缺口

**已经做对了（不要动）：**

| 机制 | 位置 |
|---|---|
| 提交幂等：`UNIQUE(owner, submission)`，同内容返回旧行，不同内容 `SUBMISSION_CONFLICT` | `task_store.py:32`、`:73-83` |
| 状态机 + 版本号 | `task_store.py:13` `STATES` / `TERMINAL` |
| 进程级互斥（单例 lease） | `task_service.py:44` `ServiceLease` |
| 启动即中断，杜绝重放 | `task_store.py:55-59` |
| 写请求结果未知显式分类，不当作失败也不重试 | `executor.py:604`、`:647` `ATTEMPT_OUTCOME_UNKNOWN`；`:298` |
| 用量未知熔断后续模型调用 | `goal_suite.py:311/374` `usage_unknown` |
| 取消/超时也落盘报告 | `executor.py:291-322` |
| 报告路径防符号链接/硬链接、原子写 | `report_files.py` `pinned_directory` / `writer_lock` |

这已经是**中上水平的工程可靠性**，且明显强于多数开源 Agent 项目。

**真实缺口（按优先级）：**

1. **无长任务恢复。** 当前"安全"的实现方式是全部 `INTERRUPTED`。
   P2 要做"长期任务恢复"时，DeepSeek 的 **`Effects`（事务内入队意图 + 提交后执行 + 按 `(kind,key)` 幂等）**
   是唯一可接受的形态：恢复的是**意图行**，不是 HTTP 请求。
2. **无 LangGraph checkpoint。** `report.workflow_nodes` 只是节点名列表，
   无法重建图状态。好消息是：**产品路径的图是无环的，本来也不需要 checkpoint**；
   一旦引入循环（第 7 节），checkpoint 才成为必需品 —— 这是引入循环的**隐藏成本**，必须写进风险。
3. **连续无进展无上限。** 有预算上限，没有"跑了 N 步但什么都没新增"的检测。
   但注意 DeepSeek 与 Anthropic 两个一手来源都**没有**实现 LLM watchdog；
   正确做法是**确定性**的：`(重复的 next_step 决策次数)` 与
   `(连续无新增断言的 case 数)` 达到阈值即停。
4. **trace 不可关联到任务。** `local_trace.py` 从单个 report 派生，
   跨 case/跨组没有统一 trace id，排障时无法回答"这轮任务总共发生了什么"。

---

## 6. 最小演进架构

### 6.1 先回答那个特别判断：能否在现有 LangGraph 上实现真正的 Agent Loop？

**能。但第一步不该是把 `ProductTasks` 搬进 LangGraph。**

这句话需要解释，因为它是本报告最重要的架构判断。

#### 6.1.1 为什么"搬进去"不等于"变成 Agent"

如果把 `ProductTasks.run()` 的每一步原地改写成图节点：

```
load_project → focus → build_ir → split_groups → [for each group] design → validate → execute → report
```

得到的图**和现在的代码在自主性上完全等价**——它仍然是一条固定管线，
只是换了一种语法表达。**把固定管线画成状态机，不会让它变成 Agent。**

自主性来自两件与图无关的事：
1. **存在一个由模型做的选择，且选项之间真的不同**；
2. **存在一条回边，使选择可以改变后续路径**。

现在的代码两者都没有。

#### 6.1.2 更重要的：现有"固定管线"是资产，不是包袱

`ProductTasks.run()`（`product_tasks.py:302-1253`，约 950 行）与
`test_product_tasks.py`（807 行）承载了本项目最难的
确定性正确性：预检先于登录、拒绝不复用、写后不重放、覆盖可归因、引擎选择可审计。
把它拆进图节点会带来一个**只有风险没有收益**的大重构，且直接违反 AGENTS.md
第 2 条（最小下一步）与第 9 条（自主测试能力明显改善前不做大规模重写）。

#### 6.1.3 正确的形状：确定性骨架 + 一个受控循环

用 Anthropic 自己的分类描述目标形态：

> **外层是 workflow（可预测、可审计），内层嵌一个 bounded agent loop。**

```
┌────────────────────────── 确定性骨架（保留）  ──────────────────────────┐
│  TaskService 准入 → Focus → PlanningIR → design_groups → Design → 预检   │
│                     → Executor（唯一 HTTP）→ Report/coverage             │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
                    ┌──────────────▼───────────────┐
                    │  Round Supervisor（新增，唯一回边）│
                    │  cyclic LangGraph，节点：        │
                    │   gather_facts → decide ─┐      │
                    │        ▲                 │      │
                    │        │   DESIGN_MORE   │      │
                    │        └─────────────────┘      │
                    │        ├─ READ_PROBE → investigate_product
                    │        ├─ ASK_QUESTION → questions 通道
                    │        └─ FINISH → coverage()      │
                    └──────────────┬───────────────┘
                                   │ 只写计划空间，永不直连 HTTP
                          预算 / 权限 / 连续无进展上限（全部确定性）
```

关键点：**这个循环里"工具"全部是已经存在的函数，没有一个是新能力。**
新增的只有"选择"与"回边"。

#### 6.1.4 为什么实现形态是"嵌套 cyclic LangGraph"而不是手写 while

- 仓库**已经**有两处这样的写法：`feedback_runtime.py:247-256` 是真有环的
  `decide → validate → execute → decide`（`add_conditional_edges` 把三个节点互相路由，
  `recursion_limit: 12`，`:260-261`）；`product_probe.py:124` 是手写 `for`（`range(limit)`，`limit≤2`）。
  前者更好，因为条件边把"什么情况下回边"写成了代码而非隐含在循环变量里。
- 复用同一模式意味着**不引入新框架**（AGENTS.md 第 1 条），且代码风格一致。
- 图仍然不配 checkpointer——因为它是**任务内的**嵌套循环，生命周期短于任务；
  任务级持久化继续由 `TaskStore` 承担（保持 `workflow.py:1` 的
  "trusted authority outside graph state" 设计）。

#### 6.1.5 渐进迁移路径（本轮**不执行**）

| 阶段 | 动作 | 兼容风险 | 预计收益 |
|---|---|---|---|
| **M1（P0）** | 在 `ProductTasks` 内嵌 Round Supervisor；**用一个布尔预算 `supervisor_max_rounds`（默认 0=关闭）**控制 | 极低：关闭时行为与今天逐字节一致 | 首次获得"自主续轮"能力，可量化 |
| **M2（P1）** | 把 supervisor 的事实组装抽成 `supervision_facts()`，报告与决策共用同一事实源 | 低 | 消除"报告说 A、决策看 B"的漂移 |
| **M3（P1/P2）** | 把 group 循环包成子图调用，supervisor 成为一级节点；**此时才需要 checkpointer** | 中：`test_product_tasks.py` 会大面积改动 | 单一编排所有权 |
| **M4（P2，条件触发）** | `ProductTasks` 与 `goal`/`suite` 两条编排收敛为一个 `TaskGraph` | 高 | 消除第 2.4 节重复项 #2 |

**M3/M4 的启动条件（必须写成硬门槛，否则会变成"为了架构而重构"）：**
1. M1 的自主收敛率有明确提升并被独立复核；
2. M1 暴露出的失败中，**≥30% 可归因于"两套编排各自持有状态"**；
3. `test_product_tasks.py` 已有可迁移的等价测试集。

不满足就不做。**仅"架构更整齐"不构成理由。**

### 6.2 模块处置表

| 模块 | 处置 | 理由 |
|---|---|---|
| `executor.py` | **保留不动** | 唯一 HTTP 出口；预算/未知结果/取消语义已验证 |
| `planning_ir.py` + `planning_compiler.py` | **保留，增强 poka-yoke** | 核心资产；只改错误消息与自由度 |
| `policies.py` + `PolicyRegistry` | **保留** | 可信知识的唯一权威来源 |
| `report.py` + `report_files.py` | **保留**，增加 supervision 记录 | 证据层稳定 |
| `task_store.py` | **保留**；P2 再加 `intent` 表 | 现在的"启动即中断"是安全选择，不急改 |
| `product_focus.py` | **增强** | ①候选面来自 `discovery`；②Prompt 拆成 core + 按族片段 |
| `product_design.py` | **增强** | 同上；`design_groups` 保留（确定性分批是优点） |
| `product_probe.py` + `feedback_runtime.py` | **合并** | 两套失败调查能力不一致；保留有环 + 有确定性 finding 校验的那一套 |
| `tool_registry.py` / `mcp_service.py` / `native_tools.py` | **收敛为一套词汇** | `submit_test_plan` 在 MCP 侧不可达（`mcp_server.py:66`），要么接通要么删掉，不要悬空 |
| `plan_construction.py` 旧后端 | **P2 淘汰** | 先让默认轮次也走 IR，再删 `UNORDERED_APPROVED_NODES_V1` |
| `composed_planning.py` 等评测旁路 | **隔离到 `eval/` 命名空间** | 不是产品代码，但让新人误以为有第三套规划 |
| `discovery.py` | **保留并接回主链** | 现在被 intake/goal/suite 用，产品主链没用——这是"已实现但未接入"的典型 |
| `local_trace.py` | **增强** | 加任务级 trace id |
| `eval_v2/` | **保留，P1 转为在线指标** | 已有 rubric 评分能力（`eval_v2/scoring.py:50/118`），别重写 |

### 6.3 现在**完全不需要**的技术（明确排除，避免后续反复讨论）

| 技术 | 为什么不需要 | 什么条件下才需要 |
|---|---|---|
| 多 Agent / Router-Worker | 两个 DeepSeek 仓库都没有这种类；Anthropic 给多 Agent 的理由是广度优先只读研究，靠 token 花费换性能 | 并发执行**大量独立只读**测试且延迟是瓶颈 |
| RAG / 向量库 | 契约 + 策略本身就是结构化可枚举的；`planner_input.py` 的收窄比检索更精确 | 出现跨项目、非结构化的历史文档语料 |
| 新的工作流框架 | 已有 LangGraph，且已验证可写有环图 | 不会 |
| 新的计划表示 | Planning IR 已覆盖实例/约束/边/歧义 | 不会 |
| 新的存储系统 | SQLite + JSON 报告足够 | 多机部署 |
| LangGraph checkpointer | 当前图无环，checkpoint 无对象可存 | **M3 引入循环编排时** |
| Skills 文件系统 / 市场 | 只有一套 DSL，按族拆字符串即可 | 出现第二种测试类型（如性能/安全）且需要独立知识包 |
| auto-memory + 后台巩固 | 与本项目"未知不得变事实"纪律直接冲突 | 不会 |
| hooks DSL | 只需两个拦截点 | 不会 |
| 外部 trace SaaS | AGENTS.md 要求不上传；`tracing_context(enabled=False)` 已刻意关闭 | 不会 |
| 模型生成代码执行 | AGENTS.md 第 5 条明令禁止 | 不会 |

### 6.4 目标能力图（一年内可达的最小版本）

```
用户："这个服务准备上线了，帮我测一轮。"
  │
  ├─ ① 范围证据      discovery → 有断言依据的操作 / 只有状态码 / 完全无规则（复用）
  ├─ ② 历史证据      operation_history → 该接口上次结果、连续失败、契约是否变更（新增派生层）
  ├─ ③ 设计          Focus → IR → groups → Design → 编译 → 预检（复用）
  ├─ ④ 执行          Executor（复用，唯一 HTTP）
  ├─ ⑤ 观察          每步断言结论 + 覆盖缺口 + 失败归因（复用 report）
  ├─ ⑥ 决策 ← 缺的     Round Supervisor：FINISH / DESIGN_MORE / READ_PROBE / ASK_QUESTION
  │                   ↑ 闭合枚举 + 只写计划空间 + 确定性预算与上限
  └─ ⑦ 报告          结论 + 剩余风险 + 未确认预期（复用 + 增补 supervision 记录）
```

**⑥ 是唯一缺失的环节，也是把 ①–⑤ 串成"同事"的那一环。**

---

## 7. 分阶段开发计划

排序原则：**先补"能自己决定下一步"，再补"决定得更好"，最后补"活得久"。**
与任务建议的 P0/P1/P2 划分一致，但 P0 的落点根据代码审查结果做了调整——
因为"自主测试用例设计"这件事其实**大部分已经存在**（见 5.2），
真正缺的是**范围证据**与**决策闭环**。

### 7.1 P0

#### P0-1 规划期候选测试目标与价值证据（接回 `discovery`）

| 项 | 内容 |
|---|---|
| **真实用户问题** | 用户说"帮我测一下包裹管理功能"时，Agent 只能从一张扁平操作清单里凭语义挑；它不知道哪些操作**有可靠断言依据**、哪些被别的操作依赖、哪些是破坏性的。于是容易出现"测了五个只读接口，没测那个删除" |
| **代码位置** | 注入点：`product_tasks.py:374-386`（Focus 调用前）<br>context 组装：`product_focus.py:473-500`<br>复用：`discovery.py:discover()`、`discovery_models.py:PolicyCoverageRow`、`project_inventory.py` |
| **最小改动范围** | 新增一个 `test_candidates(project)` 函数（约 60 行），输出**有界**的候选清单：`operation_id / mutation / approved_assertion_count / dependents / coverage_class`。`ProductTasks` 调一次，放进 Focus context 的新 key `test_candidates`。**Focus 的 Schema 与输出契约不变**（仍然只选 `operation_ids`），因此校验逻辑零改动 |
| **验收场景** | 用 L2 冻结请求（"只测试包裹功能…不要调用可用性接口"）重放：`test_candidates` 必须出现在模型请求里（用 `RecordingModel` 断言，`scripts/run_development_agent.py` 已有此设施），且候选里"仅状态码"与"有字段断言"的操作被正确分类；不新增业务 HTTP |
| **成功指标** | ①「有已批准断言的操作」在最终执行集合中的占比（先测基线，再要求不下降）；②Focus 首次合法率不下降（当前基线见 `docs/autonomous-testing-agent-l1-l3-20261110.md`） |
| **技术风险** | context 体积上升 → 触发 `planner_input.py:120` 的 100 KB 上限或 20k token 上限。缓解：候选按 `coverage_class` 排序后截断到 ≤ 操作数的 1/3，并把"截断了多少"写进报告 |
| **前置依赖** | 无 |

#### P0-2 Round Supervisor：最小 Agent 决策闭环 ← **核心**

| 项 | 内容 |
|---|---|
| **真实用户问题** | 一轮测试跑完，**必须由人再点一次"补充测试需求"**。Agent 不会自己判断"还没测到 X，我再来一轮"。这就是"工具"和"同事"的分界线 |
| **代码位置** | 插入点：`product_tasks.py:982` 之前（`coverage()` 之后、写终态之前）<br>新模块：`src/agenttest/product_supervisor.py`（约 250 行）<br>循环写法参照：`feedback_runtime.py:247-256`<br>决策 Schema 参照：`product_probe.py:16` `ProbeChoice`<br>复用：`design_groups`、`propose_design`、`validate_design_case`、`Workflow`、`coverage` |
| **最小改动范围** | ①新增 `NextStepChoice(step: Literal["FINISH","DESIGN_MORE","READ_PROBE","ASK_QUESTION"], target: str\|None, reason: str)`；②`target` 的 Schema 用**闭合枚举**约束到"当前未覆盖的 operation_id"，模型无法发明操作；③cyclic LangGraph：`gather_facts → decide →(DESIGN_MORE) design_more → gather_facts`，其余分支到 END；④`DESIGN_MORE` 复用 `design_groups(operation_ids={target})` + `propose_design` + **同一套预检**，因此权限面不变；⑤预算：`ProjectBudget.supervisor_max_rounds` 默认 **0（关闭）**，开启后上限 2；⑥每轮决策与理由写入 `summary["supervision_rounds"]` |
| **验收场景** | 冻结 3 个**宽泛**请求（例：①"测一下包裹管理的完整生命周期"；②"这个服务准备上线了，帮我测一轮"；③"包裹接口最近偶尔报错，帮我检查一下"），在自建回环目标上，**不做任何人工二次提交**，要求：<br>· 每个任务要么由 Agent 自己达到 `complete`，要么给出**具体**的 `NEEDS_REVIEW` 原因（哪个操作缺哪条业务规则）；<br>· `supervision_rounds` 里每一步的 `target` 都必须是当时确实未覆盖的操作；<br>· 业务 HTTP 总数与新增 case 数可逐条核对；<br>· **零**写请求重放、零断言改写 |
| **成功指标** | ①**自主收敛率**（无需二次人工提交即达 complete 的任务比例）：0/0（现在不适用）→ 目标 ≥2/3；②每任务人工提交次数：今天结构性 ≥1，目标降到 **1**（即"一次提交就收敛"）；③首次提交业务断言 PASS 率（当前 3/6 首次可用，目标提升）；④模型调用增量（每任务 +1~2 次，用 `ModelCallStats` 核对）；⑤**不新增**任何 `TEST_INFRA_ERROR` |
| **技术风险** | ①**循环烧预算/重复测试**：用"同一 target 不得被 DESIGN_MORE 两次"+"连续 2 轮无新增断言即强制 FINISH"两道确定性闸门；②**与 `coverage()` 双重真相**：supervisor 只读 `coverage()` 的输出，不自行计算；③**测试面爆炸**：`test_product_tasks.py` 807 行，用默认关闭的预算开关保证既有测试逐字节不变；④**模型把"没测"说成"测不了"**：`FINISH` 必须带 `reason`，且当 `coverage()` 显示有未覆盖的 required 操作时，`FINISH` 只能产生 `NEEDS_REVIEW` 而非 `FINISHED` |
| **前置依赖** | 建议先做 P0-1（否则 DESIGN_MORE 没有好的候选面），但**非硬依赖** |

#### P0-3 失败调查实现二合一

| 项 | 内容 |
|---|---|
| **真实用户问题** | 同一个失败，Web 产品走 `product_probe.investigate_product`（手写 `for`，≤2，无确定性 finding 校验），CLI 走 `feedback_runtime.run_feedback`（有环图，≤3，有 `supported_finding` 校验）。同一产品两条路径给出不同深度的结论 |
| **代码位置** | `executor.py:273-290`（分派）；`product_investigation.py:12-16`；`product_probe.py`；`feedback_runtime.py:42-78`（`supported_finding`，应该被保留的那部分） |
| **最小改动范围** | 统一到**有环图 + 确定性 finding 校验**的那一套；`analyzer` 缺省时退化为单次确定性只读复核（现有行为）。预算字段合并为一个 |
| **验收场景** | 现有 `scripts/smoke_investigation_http.py` 与 `tests/test_readonly_runtime.py` 全绿；产品路径在 503 场景下仍产出 `cause_confirmed=False` 而非臆断根因 |
| **成功指标** | 两条路径的结论字段完全一致；调查相关死代码减少；无新增未分类结果 |
| **技术风险** | 触碰已验证的安全边界。缓解：先只做**适配层**（两套实现共用一个入口与预算类型），不改图内部 |
| **前置依赖** | 无（可与 P0-2 并行） |

### 7.2 P1

#### P1-1 上下文工程：Prompt 分层 + 观测压缩

| 项 | 内容 |
|---|---|
| **真实用户问题** | 每次 Focus 都要发 145 行规则 + 整份分组索引；Design 发 179 行。规则之间大量与本次无关，既贵又分散模型注意力（context rot） |
| **代码位置** | `product_focus.py:259-403`（`FOCUS_PROMPT`）；`product_design.py:79-...`（`DESIGN_PROMPT`）；`product_focus.py:473-500`、`product_design.py:523`（`design_context`） |
| **最小改动范围** | ①把两个常量拆成 `PROMPT_CORE`（权限边界/格式/纪律，每次都发）+ `PROMPT_BY_KIND`（按本组将出现的 case 族选择性拼接）；②观测改为"摘要 + `run_id:step_id` 句柄"；③"因上下文超限失败"落进报告分类 |
| **验收场景** | 同一冻结请求重放，报告中的 `model_call.request_bytes` 下降 ≥20%，而首次合法率与最终 PASS 率**不下降** |
| **成功指标** | `request_bytes` / `input_token_upper_bound` 下降幅度；首次合法率不变；上下文超限错误数归零 |
| **技术风险** | 删规则导致回归。缓解：**逐条可追溯**——每条规则标注它由哪个测试或哪个真实失败引入（`docs/autonomous-testing-agent-l1-l3-20261110.md` 第 3 节正好有这张表），删除必须有证据 |
| **前置依赖** | 无 |

#### P1-2 `operation_history`：历史测试证据的可查派生层

| 项 | 内容 |
|---|---|
| **真实用户问题** | Agent 不记得"这个接口上次是哪条断言失败的""这个契约上周变了"。用户得自己翻历史 |
| **代码位置** | 新模块 `src/agenttest/operation_history.py`（约 150 行）；复用 `report_queries.py:73` `list_runs()`、`ReportStore.read`、`task_store` 的 `task_events` |
| **最小改动范围** | **只读派生**，不新增表、不新增写路径。输出每操作 `{last_outcome, consecutive_failures, last_contract_sha256, asserted_selectors, run_ids}`；作为 P0-2 的 `gather_facts` 的一路输入 |
| **验收场景** | 用已有 `runs/` 证据回算，结果与人工核对一致；**注入模型后不得出现任何报告里没有的断言**；模型总结字段与事实字段在 Schema 上分离 |
| **成功指标** | ①`DESIGN_MORE` 命中"历史失败过的操作"的比例；②断言依据来源 100% 可回溯到 `report.json` 或 policy；③零"模型猜测被写成历史事实"事件 |
| **技术风险** | 把旧报告的脱敏数据重新暴露。缓解：**必须**走 `report_queries.public_report()`（`:39`）的既有脱敏逻辑，不自己读原始字段 |
| **前置依赖** | P0-2（没有 supervisor 就没有消费者）；建议 P1-1 之后 |

#### P1-3 在线质量指标与最小评测

| 项 | 内容 |
|---|---|
| **真实用户问题** | 现在只有 `PASS` 计数与冻结离线评测；无法回答"这次测试的质量比上次好吗"，也无法客观评价"自主测试能力" |
| **代码位置** | 复用 `eval_v2/scoring.py:50`（`score_intent`）、`:118`（`score_report`）、`:231`（`summarize`）；新增产品内指标写入 `summary` |
| **最小改动范围** | 定义 5 个产品级指标并每轮自动计算：**自主收敛率、覆盖依据率（有批准断言的操作/已测操作）、误报率（后被人工判为测试缺陷的比例）、人工接续率（需二次提交）、上下文效率（断言数/千 token）**。另按 Qwen-Agent `DeepPlanning` 的四元组补齐三项防刷量指标：**多余项**（执行了未被要求且无断言依据的请求数）、**漏项**（有批准断言但未覆盖的 required 操作数）、**是否被预算砍断**（`TEST_INFRA_ERROR` 占比，与"自己停的"严格区分）。并按 Coze Loop 的形状**报分布不报均值** |
| **验收场景** | 同一冻结集两次运行，指标可比较且方向可解释；指标全部可由报告字段重算（无黑箱） |
| **成功指标** | 5 个指标 100% 可重算；不依赖任何外部服务 |
| **技术风险** | 指标变成新 KPI 而被优化。缓解：指标只进报告，不进产品判定逻辑 |
| **前置依赖** | P0-2（否则"自主收敛率"恒为 0） |

### 7.3 P2

#### P2-1 长任务安全恢复（意图日志）

- **真实用户问题**：任务跑 20 分钟被重启，现在只能全部 `INTERRUPTED`，用户从头再来。
- **代码位置**：`task_store.py`（新增 `intent` 表）、`executor.py:604/647`（已有 `ATTEMPT_OUTCOME_UNKNOWN`）、`task_service.py:44`。
- **最小改动**：借 DeepSeek `Effects` 形态——**事务内写意图行，提交后执行，按 `(kind, key)` 幂等**；恢复只重放"未执行"的意图，`UNKNOWN` 的写意图永不重放，只标记待人工核对。
- **验收场景**：在只读 case 中途强杀进程并重启，任务能继续且不重复发同一请求；写 case 中途强杀，重启后必须产出 `NEEDS_REVIEW` 要求人工核对，**不得**自动重发。
- **成功指标**：0 次写重放；可续任务比例；重启后人工核对提示 100% 覆盖 `UNKNOWN`。
- **风险**：这是唯一可能破坏"不重放"纪律的改动。**必须**先有独立的写意图测试集。
- **前置依赖**：P0-2、P1-2。

#### P2-2 主动测试（契约变更驱动）

- **真实用户问题**："这个接口最近偶尔报错，帮我检查一下"/"删除功能改好了，帮我回归一下"——现在都需要人重新描述。
- **代码位置**：`contract_sha256`（`report.py` 已有）、`operation_history`、`discovery` 的覆盖分类。
- **最小改动**：新增"契约变更 → 受影响操作 → 建议回归集"的**确定性**推导，产出待确认的测试建议，由人一键接受后变成普通任务（**不自动执行**）。
- **验收场景**：改一份契约的 `DELETE` 响应定义，系统能准确列出受影响的操作与历史失败，且不自动发任何请求。
- **成功指标**：建议集的人工接受率；漏报率（人工补充的操作数）。
- **风险**：变成"自动跑测试"，越权。缓解：只建议，不执行。
- **前置依赖**：P1-2。

#### P2-3 编排收敛（M3/M4，条件触发）

- 见 6.1.5 的硬门槛。**当前不应启动。**

### 7.4 不建议做的（本轮明确排除）

| 候选 | 为什么不 |
|---|---|
| 把 `ProductTasks` 重写进 LangGraph | 见 6.1.1：会产出等价管线，风险与收益倒挂 |
| 引入 Router/Worker 双层 | 两个 DeepSeek 仓库都没有这类实现；任务是顺序的，不是并发的 |
| 引入 RAG 检索历史用例 | 结构化契约 + 策略枚举已更精确；RAG 会引入不可审计的召回 |
| 重跑 M2/F1 冻结位置 | 任务边界明确禁止；且旧结论不受本次改动影响 |
| 给 Supervisor 加 LLM watchdog | 两个一手来源都没实现；用确定性双闸门 |

---

## 8. 下一轮最值得实施的一项工作

### 8.1 结论

> **实施 `Round Supervisor v0`（P0-2），以"自主收敛率"为唯一主指标，默认关闭、按预算开启。**

一句话：**把"一轮跑完后由人再点一次"改成"由模型在确定性预算内决定要不要再来一轮"。**

### 8.2 为什么是它，而不是别的

| 候选 | 能否直接提升"像同事一样工作" | 判断 |
|---|---|---|
| P0-1 候选面（`discovery` 接回） | 只让**选择更准**，不改变"选完就结束" | 次要，且是 P0-2 的输入，可后做 |
| P0-3 调查二合一 | 提升**一致性**，不提升自主性 | 工程债，可并行但不该单独成为里程碑 |
| P1-1 上下文分层 | 提升**效率**，不提升自主性 | 效果依赖 P0-2 才有意义（现在多轮本来就不存在） |
| P1-2 历史派生层 | 提升**决策质量**，但需要消费者 | 消费者就是 P0-2 |
| **P0-2 Supervisor** | **唯一能改变"是否自主"的项** | **选它** |
| P2-1 恢复 | 提升**可靠**，不提升自主 | 复杂度和风险最高，放最后 |

判据很简单：**AgentTest 现在几乎所有"模型驱动的动态决策"都是红的（3.3 节），
而其中只有一项决定了它是不是 Agent —— 有没有回边。**

### 8.3 精确范围

**做：**

1. 新增 `src/agenttest/product_supervisor.py`（目标 ≤300 行，含 docstring）。
2. 决策模型（**闭合枚举，模型无法发明操作**）：
   ```python
   class NextStepChoice(StrictModel):
       step: Literal["FINISH", "DESIGN_MORE", "READ_PROBE", "ASK_QUESTION"]
       target: str | None          # Schema 中被约束为"当前未覆盖 operation_id"的枚举
       reason: str = Field(min_length=4, max_length=200)   # 面向用户的一句话中文
   ```
3. 循环图（cyclic LangGraph，**不配 checkpointer**）：
   ```
   START → gather_facts → decide ──FINISH──────────────→ END
                              ├──DESIGN_MORE → design_more ──→ gather_facts
                              ├──READ_PROBE  → read_probe  ──→ gather_facts
                              └──ASK_QUESTION → ask_question → END
   ```
   `design_more` 内部**只调用已有函数**：`design_groups(operation_ids={target})`
   → `propose_design` → `validate_design_case` → `Workflow(...)`。
   `read_probe` 内部只调用已有的 `product_investigation.recheck_read`。
4. 预算与开关：`ProjectBudget.supervisor_max_rounds: int = Field(default=0, ge=0, le=2)`。
   **默认 0 = 行为与今天完全一致**，既有 807 行测试逐字节不变。
5. 审计记录：`summary["supervision_rounds"] = [{round, facts_digest, step, target,
   reason, model_call, new_cases, new_assertions, requests_delta}]`，
   与 `planning_engine_groups` 同样风格——**"哪条路径真的跑了"必须可审计**（2.6 节的教训）。

**不做（明确排除）：**

- 不新增任何工具 / MCP 方法 / 网络路径；
- 不放宽任何权限闸门；`target` 只能是已批准且当前未覆盖的操作；
- 不允许 Supervisor 修改断言、重放写请求、或跳过 `validate_design_case`；
- 不引入新存储、新框架、新计划表示、多 Agent、RAG；
- 不改 `Executor`、`PlanningIR`、`planning_compiler`；
- 不动 `goal`/`suite` 路径。

### 8.4 六道确定性闸门（防止"自主"变成"失控"）

| # | 闸门 | 实现位置 |
|---|---|---|
| 1 | `target` 必须是**当前未覆盖**的已批准操作（Schema 枚举 + 运行时复核） | `product_supervisor.py` 的 schema 构造函数 |
| 2 | 同一 `target` 不得被 `DESIGN_MORE` 两次 | supervisor 状态里的 `attempted_targets` |
| 3 | 连续 2 轮无新增断言 → 强制 `FINISH` | 比较 `coverage()` 的断言计数 |
| 4 | `supervisor_max_rounds` 用尽 → 强制 `FINISH` | 预算 |
| 5 | 存在未覆盖的 `required` 操作时，`FINISH` **只能**产出 `NEEDS_REVIEW`，不能产出 `FINISHED` | 终态映射 |
| 6 | 任何新增 case 都必须重新走 `validate_design_case` 预检；业务请求预算仍由 `SuiteLedger` / `BudgetTransport` 把关 | 复用现有 |

第 5 条是对"模型会把看起来合理当成完成"的直接防御——正是 4.2.8 那条核心结论的落地。

### 8.5 验收：可客观评估，且不能靠"多测"刷指标

**冻结集（3 个宽泛请求，自建回环目标，非生产）：**

| # | 请求 | 期望 |
|---|---|---|
| R1 | "测一下包裹管理的完整生命周期" | 自主达到 complete，或给出具体的规则缺口 |
| R2 | "这个服务准备上线了，帮我测一轮" | 同上 |
| R3 | "包裹接口最近偶尔报错，帮我检查一下" | 能落到受控复核，并给出 `cause_confirmed=False` 的诚实结论 |

**指标（必须成组看，防止单指标被优化）：**

| 指标 | 定义 | 采集方式 | 目标 |
|---|---|---|---|
| **自主收敛率**（主） | 未追加人工提交即达 `complete=True` 的任务比例 | 报告字段重算 | 先测基线，要求 **提升**（3 个任务中≥+1） |
| 人工接续次数 | 每个 goal 的人工提交次数 | `TaskStore.tasks` 计数 | 不高于基线 |
| **业务断言 PASS 率**（守门） | 最终运行中业务断言 PASS 的比例 | 报告重算 | **不得下降** |
| 每任务业务 HTTP（守门） | 实际请求数 | `SuiteLedger.requests` | ≤ 基线 × 1.5 |
| 每任务模型调用 | `ModelCallStats.calls` | 报告 | ≤ 基线 + 2 |
| 未分类结果数（守门） | 非五分类的结果数 | 报告 | **必须为 0** |
| 写请求重放次数（守门） | —— | 审计 | **必须为 0** |
| 因不可表达被拒的计划数 | 新的诊断分类 | 报告 | 记录（4.2.9 末尾的指标） |

**这一组指标的价值在于：**
> 只加测试数量会让"每任务业务 HTTP"和"人工接续次数"变差；
> 只求快会让"业务断言 PASS 率"下降；两者都不能单方面改善主指标。
> 因此**不能用"多测几个接口"冒充"更自主"**。

### 8.6 风险与回滚

| 风险 | 缓解 | 回滚 |
|---|---|---|
| 循环烧预算 | 闸门 4 + 预算沿用 `SuiteLedger` | `supervisor_max_rounds = 0`（一项配置） |
| 与 `coverage()` 真相不一致 | supervisor 只读 `coverage()`，不自行统计 | 同上 |
| 既有测试大面积改动 | 默认关闭 → 既有测试零改动 | 同上 |
| 写请求被重放 | 结构上不可能：`DESIGN_MORE` 只产生新计划，执行走同一 `Executor` + 同一预算 | —— |
| 报告体积膨胀 | `supervision_rounds` 只存摘要与 digest，不存正文 | —— |

### 8.7 预计工作量

| 项 | 规模 |
|---|---|
| `product_supervisor.py` | 约 250–300 行 |
| `product_tasks.py` 插入点 | 约 30 行 |
| `project_goals.py` 新增一个预算字段 | 1 行 |
| 新测试 `tests/test_product_supervisor.py` | 约 300–400 行（含 6 道闸门各一个反例） |
| 回环验收脚本改动 | 复用 `scripts/run_development_agent.py`，约 40 行 |
| **合计** | **约 650–800 行，无新依赖** |

### 8.8 为什么这个里程碑"能在现有架构上实施"

逐条对照 AGENTS.md 与技术选择原则：

| 原则 | 本方案如何满足 |
|---|---|
| 已有 LangGraph，不引入新工作流框架 | 用 LangGraph 写循环图，仓库已有先例（`feedback_runtime.py:247`） |
| 已有 MCP，不重复同类工具接口 | 不新增工具；菜单项全部映射到已有内部函数 |
| 已有 Planning IR，不重新设计计划表示 | IR 完全不动，`DESIGN_MORE` 复用它 |
| LLM 不得绕过 Executor | Supervisor 只写计划空间，HTTP 仍只由 `Executor` 发出 |
| 不因 DeepSeek 用 Router/Worker 就上多 Agent | 单 Agent，单进程 |
| 不因 Claude Code 有 Skills/Memory 就建多套存储 | 不建存储；P1 的历史层是只读派生 |
| 不为技术名词增加基础设施 | 新增零依赖、零存储、零服务 |
| 最小、可测量、可复用 | 一个开关、一组指标、全复用 |
| 自主能力明显改善前不做大重构 | 显式拒绝 M3/M4（6.1.5） |

---

## 9. 结论：回答任务的四个判断标准

### 9.1 AgentTest 距离真正的 AI API 测试同事还缺什么？

**缺一个回边。** 展开成三层：

1. **能力层几乎不缺。** 自然语言理解、子句义务分解、请求实例身份、动态绑定、
   依赖排序、确定性编译、只读调查、五分类证据、预算与幂等——都已接入且有真实运行证据。
2. **决策层缺得最多。** 执行完之后，"要不要继续、继续测什么"这个判断**没有代码承载**。
   今天它由人在 Web UI 上点"补充测试需求"完成（`web/src/pages/GoalTasks.tsx:1113-1114`）。
3. **记忆层基本为零。** 没有跨任务的经验；上下文是"全量注入 + 超限报错"；
   Skills / 检索 / 压缩都不存在。

用一句话说清程度：
> 它已经能**理解一句中文并正确地做一轮测试**；
> 但它还不会**决定自己该做第二轮的什么**。

### 9.2 哪些能力可以直接复用现有代码？

| 现在缺的 | 可以复用什么 | 复用度 |
|---|---|---|
| 自主决定下一步 | `product_probe.py` 的闭合枚举决策 + `feedback_runtime.py` 的循环图 | 形态直接照搬 |
| 追加测试 | `design_groups` + `propose_design` + `validate_design_case` + `Workflow` | **100%，一行不改** |
| 范围证据 | `discovery.py` + `discovery_models.py` + `project_inventory.py` | 已被三处使用，只差接回主链 |
| 历史证据 | `report_queries.list_runs()` + `public_report()`（含脱敏）+ `task_events` | 只读派生即可 |
| 客观评测 | `eval_v2/scoring.py` 的 `score_intent` / `score_report` / `summarize` | 已有 rubric 能力 |
| 上下文分层 | 现有 `design_context` 的分组注入 + Python 字符串拼接 | 无新依赖 |
| 安全恢复 | `ATTEMPT_OUTCOME_UNKNOWN` + `SuiteLedger` + 现有幂等提交 | P2 时按 DeepSeek `Effects` 形态扩展 |

**这就是本项目最大的好消息：P0 与 P1 几乎不需要写新能力，只需要写"连接"。**

### 9.3 哪些成熟 Agent 的设计能带来明确的产品能力提升？

只列**有明确落点**的四条，其余一律归入"过度工程"：

| 设计 | 来源 | 落点 | 带来的能力 |
|---|---|---|---|
| **Harness 持有"完成"的定义；拒绝接受模型自认完成** | Claude Code `Stop` hook / `/goal` + Anthropic 失效模式分析 | 8.4 闸门 5 | 让"自主结束"变得可信 |
| **闭合行动空间 + 确定性闸门先于模型判断** | AgentScope 的"不约束模型"是反例；DeepSeek `Policy.gate` 是正例 | 已有，继续加强 | 保证自主性不越权 |
| **按 schema 修复参数；错误消息本身就是接口** | AgentScope `_json_loads_with_repair` + 工具组激活提示；Claude Code 的 ACI/poka-yoke | `diagnostics.ValidationFailure` 升级（P1） | 降低首次合法率失败、让拒绝可自愈 |
| **评测四元组：部分分 / 严格分 / 多余项 / 是否被砍断** | Qwen-Agent `DeepPlanning`（`score` / `case_acc` / `extra` / `is_completed`） | 8.5 与 P1-3 | 让"更自主"不能被"多测几个"冒充 |
| **观测拆三层：发生了什么 / 判定说了什么 / 我们信什么** | Coze Loop `Span` / `EvaluatorRecord` / `Annotation` | P1-3 | 人可标注而**不覆盖**原判定，直接满足"不自动改写断言" |
| **观测压缩 + 工件句柄（而非全量上下文）** | Claude Code 压缩表 / AgentScope offload-with-reminder | P1-1 | 上下文成本可测地下降 |
| **"模型可见即已记录"** | `deepseek-harness` `docs/architecture.md` | P1-3 | 让所有上下文争论变成可测问题 |

### 9.4 如何以最少的架构复杂度实现自主规划、工具调用、执行反馈和持续决策？

**一个循环、四个菜单项、六道闸门、零新基础设施。**

```
自主规划   = 已有 Focus/Design/IR/Compiler（不改）
工具调用   = 闭合枚举菜单（FINISH / DESIGN_MORE / READ_PROBE / ASK_QUESTION），
             每项映射到一个已有函数
执行反馈   = 已有 report + coverage()，作为 gather_facts 的输入
持续决策   = 一条回边 + 确定性预算与上限
```

**不新增**：框架、存储、服务、模型供应商、计划表示、工具协议。
**新增**：约 300 行一个模块，加一个默认关闭的预算开关。

### 9.5 最后一句判断

AgentTest 目前的强项（确定性、证据、权限、幂等）恰好是**大多数 Agent 项目最弱的地方**；
它目前的弱项（动态决策、记忆、上下文）恰好是**最容易用现有代码补齐的地方**。
所以正确的下一步不是学谁，而是**把自己的第 ⑥ 步接上**。

---

## 10. 附录

### 10.1 证据索引（本报告引用的源码位置）

| 主题 | 文件:行 |
|---|---|
| 产品主链入口 | `task_service.py:114-115`、`task_service.py:184-186` |
| 产品编排 | `product_tasks.py:215`、`:302`、`:643`、`:825`、`:885-899`、`:982-999`、`:1080` |
| LangGraph 线性图 | `workflow.py:30-37`、`:58-60`、`:312-325` |
| 有环图（唯一真正循环） | `feedback_runtime.py:81`、`:247-256` |
| 只读调查（产品） | `product_probe.py:16`、`:50`、`:111-188`；`product_investigation.py:10-16` |
| Focus | `product_focus.py:229`、`:259-403`、`:580` |
| IR | `planning_ir.py:23-65`、`:68-79`、`:102`、`:359` |
| 编译器与分派 | `planning_compiler.py:22`、`plan_construction.py:8`、`:31-34` |
| Design | `product_design.py:23-62`、`:79`、`:449-513`、`:568`、`:876` |
| Executor 与调查分派 | `executor.py:97-120`、`:255-322`、`:604`、`:647` |
| 报告与结论分类 | `report.py:23-28`、`:39-75` |
| 任务持久化 | `task_store.py:20-59`、`:73-83` |
| 用量与预算 | `goal_suite.py:304-330`、`:337-380`；`project_goals.py:56-65` |
| MCP（出站） | `mcp_server.py:65-76`、`mcp_service.py:33-34`、`:68`；`tool_registry.py:42-57` |
| 工具词汇统一 | `native_tools.py:21`、`tool_registry.py:60` |
| 上下文组装 | `planner_input.py:44-131`、`product_focus.py:473-500` |
| 报告查询与脱敏 | `report_queries.py:39`、`:73` |
| 本地 trace | `local_trace.py:18-40` |
| 评测评分 | `eval_v2/scoring.py:50`、`:118`、`:231` |
| 发现（未接主链） | `discovery.py:48` `discover()`、`:31-40` 候选上限与 `trusted()` |
| 失败说明（确定性，非模型） | `product_tasks.py:903-907` → `product_probe.explain_failure` |
| 人机接续 UI | `web/src/pages/GoalTasks.tsx:1113-1114`、`web/src/components/ProductResult.tsx:398-400` |

### 10.2 一手资料清单（本报告实际抓取）

**DeepSeek**
- `https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/master/README.md`（注意 `master`）
- `deepseek-harness` 的 `SAFETY.md`、`docs/architecture.md`、`docs/agent-lifecycle.md`、`docs/tool-execution-pipeline.md`
- `https://raw.githubusercontent.com/deepseek-ai/dsh-superagent-prototype/main/README.md`
- 该仓库 `docs/000-philosophy.md`、`docs/001-domain.md`（不是 `001-domain-model.md`）、`docs/003-execution.md`、`docs/007-architecture.md`
- 该仓库 `src/core/engine.ts`、`src/policy.ts`、`src/app/store.ts`、`src/app/context.ts`、`src/app/passes.ts`、`src/app/retry-health.ts`、`src/infra/health.ts`、`src/limits.ts`

**Anthropic / Claude Code**
- `code.claude.com/docs/en/how-claude-code-works`、`/agent-sdk/agent-loop`、`/tools-reference`、
  `/permissions`、`/permission-modes`、`/hooks`、`/skills`、`/memory`、`/sub-agents`、
  `/context-window`、`/best-practices`、`/features-overview`、`/mcp`、`/headless`
- `https://agentskills.io`
- `anthropic.com/engineering/building-effective-agents`（现 302 到 `/research/`）
- `anthropic.com/engineering/effective-context-engineering-for-ai-agents`
- `anthropic.com/engineering/writing-tools-for-agents`
- `anthropic.com/engineering/multi-agent-research-system`
- `anthropic.com/engineering/effective-harnesses-for-long-running-agents`
- `anthropic.com/engineering/harness-design-long-running-apps`
- 泄露事件（第三方，仅背景）：`nodesource.com/blog/anthropic-claude-code-source-leak-bun-bug`、
  `alex000kim.com/posts/2026-03-31-claude-code-source-leak/`

**国内四个框架**（详见 4.3–4.6 各自的文件清单）

### 10.3 本报告明确**没有**做的事

- 未修改任何生产逻辑、未新增框架、未调用付费模型、未向真实业务环境发请求；
- 未重跑冻结的 M2/F1 位置；
- 未把泄露事件材料当作架构证据，未复述任何泄露的内部 Prompt 或源码；
- 未安装或运行四个参考项目；
- 未启动 6.1.5 的 M3/M4 重构。

### 10.4 核查基线

| 检查 | 结果 |
|---|---|
| HEAD | `795310c`（`master`，工作区干净） |
| `ruff check src tests` | All checks passed |
| `pytest tests/test_planning_ir.py tests/test_task_store.py tests/test_task_service.py` | 41 passed（89.86s） |
| 全量离线套件 | 本轮**未重跑**（上一次记录：`2d8fac9` 上 2656 passed / 15 skipped / 0 failed，见 `docs/autonomous-testing-agent-l1-l3-20261110.md` 第 8 节） |








