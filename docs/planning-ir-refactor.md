# AgentTest 规划引擎重构：规范化 Planning IR 与确定性依赖编译器

日期：2026-11-10（工作区实现；未进行真实模型调用）

## 0. 结论摘要

M2 的 `CONSTRUCTION_DEPENDENCY_CYCLE` 不是“模型把顺序写错了”，而是三层职责混在一起：

1. **Focus 的 requirement schema 把“状态/顺序/资源约束”逼成了可执行动作**。归档 M2 的
   8 个 `required` 子句中只有 4 个是真实请求（create/get/delete/get），另外 1 个是取 id、
   3 个是状态断言；旧 schema 却要求每个 `required` 子句至少有一个 action，于是 4 个非请求
   子句也被计成请求义务。
2. **Design 只能给“操作”排序，不能给“同一操作的多个实例”排序**。M2 中 `getItem` 出现
   两次、`deleteItem` 只出现一次，旧 wire 在操作层面无法表达“第一次 GET 是删除前、第二次
   GET 是删除后”。
3. **构造器用“整个操作池”代替实例**。`node_for` 在缺少实例绑定时把一个未绑定节点扩成该
   操作的所有节点，于是 GET 与 DELETE 之间同时出现两个方向的文本顺序边，形成假环；真正的
   实例级 DAG 是 `create → read → delete → readback`。

本次重构把“语义判断”留给模型，把“请求实例化、依赖分析、资源绑定、拓扑排序”收归程序：

- 新增 `planning_ir.py`：`ActionInstance` / `Constraint` / `ResourceHandle` /
  `DependencyEdge` / `Ambiguity`，实例 ID 由程序分配（`ai_N`），绝不使用数组下标；
- 新增 `planning_compiler.py`：消费 IR，校验实例覆盖，解析动态参数，构建
  producer→consumer、文本顺序、资源生命周期、已批准策略前置边，稳定拓扑排序，输出既有
  Executor DSL；
- 生产入口只在“规范化确实去掉了非请求子句、无歧义、无待确认问题、且该 Design 组覆盖全部
  实例”时启用 IR 编译器；其余情况保持旧路径，降低回归面；
- 离线重放归档 M2：旧路径仍复现历史 `CONSTRUCTION_DEPENDENCY_CYCLE`
  （`plan.steps.1` / `plan.steps.2`），新路径得到 4 个实例的正确 DAG；
- 新增产品入口 E2E：Focus → Design → IR → 构造/DSL 校验 → Executor，MockTransport 记录
  到 `POST /items → GET /items/item_1 → DELETE /items/item_1 → GET /items/item_1`，4 个
  请求全部执行且用例 PASS；
- 模型用量统计在构造失败时不再丢失：已知的 2 次调用/22574 tokens 保留；缺失用量记为
  `UNKNOWN`，不回填 0。

本轮**没有**调用真实模型、没有消耗 M2/F1 位置、没有改冻结 rubric/评分/原始证据。下文的
“E2E PASS”是 MockTransport 离线 E2E，不是真实 LLM 端到端成功率。

## 1. 成熟方案对照（只依据实际代码/官方文档）

| 成熟方案 | 核心设计 | AgentTest 当前实现 | 可借鉴之处 | 是否采用 |
|---|---|---|---|---|
| Microsoft RESTler | 从 OpenAPI + 注解推断资源依赖，生成有状态测试序列，再以 checker 验证；核心是“依赖图 + 可执行序列”，不是让 LLM 决定调用顺序（来源：RESTler 官方 README，https://github.com/microsoft/restler-fuzzer） | Focus/Design 让模型给出动作与顺序；构造器在实例缺失时按操作池连边 | 把“谁产生资源、谁消费资源”显式建成图；用图而不是文本相邻关系决定执行顺序 | 采用“依赖图”思想；不采用其 fuzzing/随机生成与 Java 运行时 |
| Schemathesis | 基于 OpenAPI 的属性测试与 stateful testing；用 OpenAPI links 从响应推导后续参数，显式定义 check（来源：官方文档 https://schemathesis.readthedocs.io/） | 已有 approved policy、binding_rules、expectation，但动态参数仍可能依赖模型给的 `from_step` | 动态参数应由“生产者 + selector”推导，且每个断言要有明确来源；不把数组位置当身份 | 采用 link/selector 推导与显式断言；不采用随机属性测试作为主路径 |
| Anthropic《Building effective agents》 | 明确区分 workflow（预定义代码路径）与 agent（模型自主决定流程）；建议从最简单方案开始，按需增加复杂度；框架可能遮蔽 prompt/response 而不利于调试（来源：https://www.anthropic.com/engineering/building-effective-agents） | 之前把“顺序/依赖”也交给模型，属于把 workflow 做成 agent | 规划层只做语义判断；请求实例化、依赖、排序用确定性代码路径；不引入框架抽象 | 采用“workflow 边界”原则；不采用 agent 自主编排 |
| LangGraph | typed state + channels/reducers，节点做工作、边决定下一步；checkpointer/interrupt 支持恢复；conditional edges 决定路由（来源：https://docs.langchain.com/oss/python/langgraph/graph-api 和 /persistence） | 已有 SQLite 任务/报告持久化，但规划中间态没有 typed IR；边由模型/旧构造器混合产生 | typed、可序列化的规划状态；节点与边职责分离；中间态可审计、可恢复 | 采用“typed state + 节点/边分离”的数据模型；不引入 LangGraph 运行时 |
| Google ADK | workflow agents（Sequential/Parallel/Loop）按类型确定执行顺序，不由模型编排；LlmAgent 才由模型动态决定；ADK 2.0 起 template workflows 被 graph-based/dynamic workflows 取代（来源：https://google.github.io/adk-docs/agents/workflow-agents/ 及 llm-agents 页） | 顺序由 Design 提议、构造器再推导，模型和程序都可能影响顺序 | 顺序属于“workflow 类型”的确定性职责；模型只产出语义节点；迭代/循环要有显式上限 | 采用“确定性编排”原则；不采用 ADK 运行时或旧 template API |

结论：五种方案的共同点是**把语义理解和执行编排分开**。AgentTest 的新 IR/编译器采用这一
共同原则，但不引入任何新框架；RESTler/Schemathesis 提供依赖图与 link 推导的具体做法，
Anthropic/LangGraph/ADK 提供职责边界与状态建模的依据。

## 2. AgentTest 原有架构问题

### 2.1 Focus：约束被 schema 逼成请求

`src/agenttest/request_requirements.py` 的 `RequestRequirement` 要求 `required` 子句有
actions；`product_focus.py` 的 wire 校验又把“每个文本原子都必须出现在 requirement_map”作为
硬条件。结果：

- “确认返回 200”“确认返回 204”“确认返回 404”这类状态断言，被模型写成带 `getItem` /
  `deleteItem` action 的 `required` 子句；
- “并从返回值里拿到 id”这种数据捕获，也被写成带 action 的子句；
- 8 个 required 子句因此被下游当成 8 个请求义务，而真实请求只有 4 个。

这不是单纯的 prompt 问题：schema 允许并鼓励这种写法，校验还会因为“顺序对已覆盖”而通过。

### 2.2 Design：wire 只有操作级身份

`GenericDraftStep` 的 `requirement_source_id`/`requirement_action_index` 只在
`_needs_instance_bindings` 为真时出现，且模型完全可以选择不填。旧构造器在缺少绑定时用
`node_for(operation_id)` 返回整个操作池，导致：

- 一个未绑定的 GET 节点与一个未绑定的 DELETE 节点互相成为对方的前置；
- 文本顺序边在操作池层面双向生成；
- 依赖环在构造期才暴露，且字段是 `plan.steps.N`，看不出是哪个文本子句/哪个实例。

### 2.3 构造器：把“顺序”与“实例”混为一谈

`construct_plan` 旧路径同时处理 `request_requirements` 的 before 边、认证边、资源依赖边、
生命周期清理边和 Design 的 `from_step` 边。它没有“实例”这一层，无法回答：

- 两次 GET 是两个实例，还是一次请求的两种描述？
- DELETE 的 204 是否应产生 ID？
- 删除后的 GET 引用 CREATE 的 ID，但必须排在 DELETE 之后；
- 自动清理边与用户显式 DELETE 是否是同一件事？

### 2.4 用量统计：失败路径丢账

`failure_report()` 旧实现构造 `RunReport(planner_calls=0, model_call=None)`，即使
`design_records[*].model_call` 已经保存了两次调用的 usage。结果 M2 平台日志有
Focus 11276/892、Design 8924/1482，共 22574 tokens，而产品报告是
`planner_calls=0`、`model_call=null`。修复后：

- 已知调用求和；未知用量保持 `None` 并标记 `usage_unknown`；
- 失败时 `planner_calls` 仍为真实调用次数；
- 绝不把缺失值写成 0，也不改写 M2 归档。

## 3. M2 根因与完整依赖图

### 3.1 归档事实

- 位置：M2（`docs/evidence/rw-acceptance-m2-8845c31/evidence.json`）
- 文本（`scripts/rw_lifecycle/plan_v4.py`）：
  “先由 author 身份创建名称是 first 的条目，并从返回值里拿到 id；接着按这个 id 读取该条目，
  确认返回 200；随后删除该条目，确认返回 204；最后用同一个 id 回读，确认返回 404。”
- Focus：8 个 required 子句；其中 request_1 createItem、request_3 getItem、
  request_5 deleteItem、request_7 getItem 是真实请求；request_2 是取 id；
  request_4/6/8 是状态断言。
- 判定：`NEEDS_REVIEW`；2 次模型调用；22574 tokens；0 个业务 HTTP。
- 失败：`CONSTRUCTION_DEPENDENCY_CYCLE`，字段 `plan.steps.1`、`plan.steps.2`。
- 原始 Design 回复没有归档（只有 `output_sha256=421bb100...`、6880 bytes），所以“模型当时
  具体写了哪 4 步”不能声称是逐字节事实；可证明的是诊断字段指向 4 节点构造中的环，以及旧
  代码在“未绑定实例”时必然把 GET/DELETE 扩成操作池并产生双向文本边。离线重放采用显式的
  4 步未绑定重建，见第 7 节。

### 3.2 规范化后的实例与边

程序分配：

| 实例 | 操作 | 来源子句 | 策略 | 阶段 |
|---|---|---|---|---|
| `ai_1` | createItem | request_1 | createItem | EXISTS（创建） |
| `ai_2` | getItem | request_3 | getItem | EXISTS（读） |
| `ai_3` | deleteItem | request_5 | deleteItem | DELETED（删除） |
| `ai_4` | getItem | request_7 | getItem_expectation_1 (404) | VERIFY_ABSENT（回读） |

边（全部保留，不覆盖原因）：

| 边 | 原因 | 证据 |
|---|---|---|
| `ai_2 ← ai_1` | DATA_DEPENDENCY + TEXT_ORDER + SEQUENCE_MARKER | 路径参数 itemId 来自 create 的 `$.id`；原文“接着” |
| `ai_3 ← ai_1` | DATA_DEPENDENCY + RESOURCE_EXISTS | 删除必须使用已创建资源的 id |
| `ai_3 ← ai_2` | LIFECYCLE_CLEANUP + TEXT_ORDER + SEQUENCE_MARKER | 生命周期要求先读后删；原文“随后” |
| `ai_4 ← ai_1` | DATA_DEPENDENCY | 回读仍引用 create 的 id |
| `ai_4 ← ai_3` | RESOURCE_ABSENT + TEXT_ORDER + SEQUENCE_MARKER | 回读期望 404，必须排在删除后；原文“最后” |

关键点：**没有 `ai_4 → ai_3`**。回读引用 create 的 id 只说明数据来源，不说明它必须先于
删除；`RESOURCE_ABSENT` 边明确要求删除先执行。旧构造器把“引用 create”误当成“读必须在删除
之前”的同类边，才制造了环。

### 3.3 最早的错误阶段

1. **Focus（最早）**：把 4 个非请求子句写成可执行 action，产生 8 个请求义务；schema 和
   校验未阻止。
2. **Design**：没有为两个 GET 提供实例身份（或提供了但旧构造器未强制使用），无法区分
   删除前/后。
3. **构造器**：`node_for` 用操作池扩大未绑定节点，生成双向文本边，报
   `CONSTRUCTION_DEPENDENCY_CYCLE`。
4. **报错位置**：`plan.steps.1/2` 只暴露节点，不暴露子句/实例，诊断过晚且不可审计。

## 4. 新 Planning IR

`src/agenttest/planning_ir.py`：

- `ActionInstance`：`instance_id`（程序分配，稳定，`ai_N`）、`operation_id`、`role`、
  `source_id`/`source_quote`/`action_index`、`policy_id`/`policy_origin`、
  `arguments`（`InstanceArgument`：literal / from_instance + select）、`assertions`
  （`ResponseAssertion`：状态码、来源子句）、`resource_id`、`phase`。
- `Constraint`：`MUST_PRECEDE`、`SAME_RESOURCE`、`DIFFERENT_RESOURCE`、
  `RESPONSE_ASSERTION`、`DATA_DEPENDENCY`、`RESOURCE_EXISTS`、`RESOURCE_ABSENT`；
  约束不是新请求。
- `ResourceHandle`：`resource_id`、`producer_instance`、`selector`、`phase`。
- `DependencyEdge`：`source_instance`、`target_instance`、`reasons[]`（每条含
  `reason`/`evidence`/`required`/`detail`）；同一对节点可有多条原因，全部保留。
- `Ambiguity`：`MISSING_INSTANCE_IDENTITY`、`CONSTRAINT_CONFLICT`、
  `RESOURCE_REFERENCE_UNKNOWN`、`PARAMETER_SOURCE_UNKNOWN`、
  `OPERATION_NOT_APPROVED`、`DEPENDENCY_CYCLE`、`UNCLASSIFIED_CLAUSE` 等；不是笼统的
  `MODEL_INVALID_RESPONSE`。

状态机：`ClauseRecord.role` 为 `REQUEST` / `ASSERTION` / `DATA_CAPTURE` /
`CONSTRAINT` / `DEFERRED` / `UNCLASSIFIED`；只有 `REQUEST` 产生实例。

实例身份规则：

- 一个子句一个 action → 一个实例；
- 显式“连续读取同一资源 3 次” → 3 个 `GET` 实例（计数是显式语义，不是重复描述）；
- 同一操作被多个子句描述 → 由 `source_id` 区分，不按 `operation_id` 去重；
- 一个子句多个 action → 视为约束（当前实现的保守处理），不擅自拆成 N 个请求；
- 两次 CREATE 不共享 id，除非有显式同资源证据；
- DELETE 204 不产生资源 id；回读只能引用 CREATE 的 id。

## 5. 编译器算法与数据流

`src/agenttest/planning_compiler.py`：

1. **输入**：`PlanningIR` + `GenericPlannerDraft`（Design 原始步骤）+ `context`
   （policies/operations/lifecycles）。
2. **实例映射**：`_step_instance_map` 只看模型显式声明的
   `requirement_source_id`/`requirement_action_index`；同一子句的多个重复步骤按字节相同
   请求分配，绝不按数组位置推断。操作只出现一次且未声明绑定时，可用唯一操作回退。
3. **覆盖校验**：`validate_instance_coverage` 要求 IR 中每个实例在某个 case 中被规划；
   若模型明确提出问题或 `not_covered`，允许部分覆盖，但该缺口对用户可见。
4. **参数解析**：保留已批准 literal；把 `from_step` 的设计 id 重映射到实例；程序推导的
   `from_instance` 覆盖模型值；校验来源实例必须先执行；禁止自依赖。
5. **建边**：
   - 数据依赖：producer 的 selector → consumer 参数；
   - 文本顺序：before/sequence marker；
   - 资源生命周期：`RESOURCE_EXISTS`、`LIFECYCLE_CLEANUP`、`RESOURCE_ABSENT`；
   - 已批准策略前置：`POLICY_PREREQUISITE`；
   - 认证：`AUTHENTICATION`（由既有认证步骤管理）。
6. **拓扑排序**：稳定排序，保留输入顺序作为 tie-break；检测真环并报告完整节点集合。
7. **输出**：既有 Executor DSL（`plan.steps`），执行步骤 id 保留 Design 的 id，IR 实例
   id 作为内部身份与统计字段；不改变 DSL schema。
8. **失败**：`PLANNING_IR_AMBIGUOUS`、`PLANNING_INSTANCE_COVERAGE`、
   `PLANNING_INSTANCE_CONFLICT`、`PLANNING_REFERENCE_ORDER_INVALID`；不通过“删边直到
   能跑”来伪造计划。

数据流：

```
文本 → request_atoms → Focus（语义选择 + requirement_map）
     → build_planning_ir（程序：角色/实例/资源/边/歧义）
     → Design（只提语义节点，不决定执行顺序）
     → compile_design（程序：绑定/参数/边/拓扑）
     → 既有 DSL 校验 → Executor
```

## 6. 代码改动与向后兼容

| 文件 | 改动 |
|---|---|
| `src/agenttest/planning_ir.py` | 新增 IR 模型与 `build_planning_ir` |
| `src/agenttest/planning_compiler.py` | 新增 `compile_design`、覆盖校验、实例映射 |
| `src/agenttest/plan_construction.py` | `construct_plan` 在 `context['planning_ir']` 存在时走编译器；`construct_design` 做覆盖校验 |
| `src/agenttest/product_tasks.py` | 构建 IR、按组决定是否启用、失败时汇总用量；计算 `witness_requirements` |
| `src/agenttest/product_design.py` | `propose_design` 接收 `planning_ir`/`witness_requirements`，IR 不进入模型 wire |
| `src/agenttest/product_sources.py` | `compact_sources` 移除 IR，避免模型可见 |
| `src/agenttest/request_requirements.py` | 显式重复读取支持“连续读取同一资源 3 次”这类带宾语的表述 |
| `scripts/rw_lifecycle/acceptance.py` | 报告读取兼容新用量字段 |
| `tests/test_planning_ir.py` | IR/编译器/重放/用量/产品 E2E 测试 |
| `scripts/rw_lifecycle/planning_replay.py` | 归档 M2 离线重放入口 |

兼容策略：

- **窄启用**：只有 `normalization_applied`、无 `ambiguities`、无 `questions`、无
  `planning_requirements`、且该 Design 组覆盖全部实例时才启用 IR；否则旧路径。
- **wire 不变**：复用 `requirement_source_id` / `requirement_action_index`，不新增 Design
  字段，不改变 `draft_schema_sha256` / 冻结记录。
- **执行 id 不变**：编译后的 `step.id` 仍是 Design 给的 id，便于既有报告/测试/证据阅读；
  `ai_N` 只作为 IR 身份和统计。
- **负向用例**：带 `negative_test` 的策略仍走旧路径。
- **旧路径保留**：`construct_plan` 的 legacy 分支原样保留；归档重放仍能复现历史失败。

## 7. 离线重放（无模型、无 HTTP）

命令：

```powershell
.\.venv\Scripts\python.exe scripts/rw_lifecycle/planning_replay.py
```

输出（本次实际运行）：

```
archived position: M2 verdict: NEEDS_REVIEW
focus clauses: 8 atoms: 8
legacy failure: CONSTRUCTION_DEPENDENCY_CYCLE ['plan.steps.1', 'plan.steps.2']
ir coverage: {"assertion_clauses": 3, "constraint_clauses": 0, "data_capture_clauses": 1,
 "instances": 4, "normalization_applied": true, "request_clauses": 4,
 "required_clauses_after": 4, "required_clauses_before": 8, "resources": 1,
 "source_clauses": 8, "unclassified_clauses": 0}
ir order: ['ai_1', 'ai_2', 'ai_3', 'ai_4']
compiled order: ['create_1', 'get_3', 'delete_5', 'get_7']
compiled policies: ['createItem', 'getItem', 'deleteItem', 'getItem_expectation_1']
edge reasons: {"delete_5<=create_1": "DATA_DEPENDENCY+RESOURCE_EXISTS",
 "delete_5<=get_3": "LIFECYCLE_CLEANUP+TEXT_ORDER+SEQUENCE_MARKER",
 "get_3<=create_1": "DATA_DEPENDENCY+TEXT_ORDER+SEQUENCE_MARKER",
 "get_7<=create_1": "DATA_DEPENDENCY",
 "get_7<=delete_5": "RESOURCE_ABSENT+TEXT_ORDER+SEQUENCE_MARKER"}
REPLAY OK: legacy cycle reproduced; IR compiles create->read->delete->readback
```

说明：

- 旧路径的 `plan.steps.1/2` 与归档诊断字段集合一致；
- IR 覆盖：8 子句 → 4 请求实例、3 断言、1 数据捕获、0 未分类、0 歧义；
- 编译后的 3 个 `itemId` 参数全部 `from_step=create_1`、`select=$.id`；
- 回读边 `get_7<=delete_5` 的首个原因是 `RESOURCE_ABSENT`，不是数据依赖；
- 该脚本只读归档证据，不写归档文件，不调用模型/HTTP。

## 8. 新链路离线 E2E

`tests/test_planning_ir.py` 包含两层 E2E：

1. `test_archived_m2_semantics_reach_the_executor_as_four_requests`：归档 8 子句 → IR →
   `construct_plan` → DSL → Executor（MockTransport）。
2. `test_archived_m2_semantics_run_through_the_product_entry`：真实产品入口 Focus →
   Design → IR → 构造/DSL → Executor；Design 回复用 `resource_flows.fresh_1` 的 flow
   表达 create/read/delete/readback，Focus 使用归档的 8 子句（`getItem` 映射为测试夹具的
   `readItem`，语义不变）。

实际断言：

```
target.calls == [
    ("POST", "/items"),
    ("GET", "/items/item_1"),
    ("DELETE", "/items/item_1"),
    ("GET", "/items/item_1"),
]
summary["cases"][0]["outcome"] == "PASS"
summary["cases"][0]["http_requests"] == 4
target.resources == {}
```

这证明“4 个请求实例、动态 id 全部来自 CREATE、删除后回读 404”在真实执行器上成立；它仍是
MockTransport 离线 E2E，不能当作真实 LLM 端到端成功率。

全量离线回归（2026-11-10）：`2652 collected / 2637 passed / 0 failed / 15 skipped`
（`exact_test_id_coverage=true`，1184.7s）。

## 9. 用量统计修复

`src/agenttest/product_tasks.py` 新增 `aggregate_usage(records)`：

- 已知 `model_call.total_tokens` 求和；
- 任一调用缺失 `total_tokens` → 聚合值 `None`，并保留 `calls`；
- `failure_report()` 使用聚合结果设置 `planner_calls` / `model_call`；
- `summary["model_calls"]` / `summary["model_usage"]` 在失败路径也写入。

测试：

- `test_usage_survives_a_later_design_failure`：2 次调用/22574 tokens，输入 20200、输出
  2374；含未知调用时总 token 为 `None`，calls 仍为 2。
- `test_failure_report_keeps_the_known_usage`：构造失败报告仍保留 22574。
- `scripts/rw_lifecycle/acceptance.py` 的 `model_calls` 兼容
  `model_calls`/`models`/`model_call.calls`/`planner_calls`/`model_usage.calls`；
  `product.requests` 使用审计请求数，避免 `None`。

M2 归档的 `project-report.json` / `completed.json` 保持原样；修复只影响未来运行。

## 10. 指标（本轮离线样本）

| 指标 | 样本 | 结果 | 来源 |
|---|---|---|---|
| Action Instance 准确率 | 归档 M2，4 个真实请求 | 4/4，`ai_1..ai_4` 与子句一一对应 | `tests/test_planning_ir.py`、重放输出 |
| 请求义务覆盖率 | 8 子句 → 4 请求 | 4/4；3 断言、1 数据捕获不计为请求 | `ir.coverage` |
| 不受支持的多余请求 | M2 编译结果 | 0 | `compiled order` 4 步 |
| 动态引用准确率 | itemId 绑定 | 3/3 全部 `from_step=create_1` / `$.id` | 重放/测试 |
| 合法计划构造率 | M2（IR 路径） | 1/1 | 重放 |
| 假环数 | M2（IR 路径） | 0 | IR DAG |
| 真环检测 | 构造用例（`test_true_cycle_is_rejected`） | 1/1 拒绝并报告 | `tests/test_planning_ir.py` |
| Executor 执行率 | M2 离线 E2E | 4/4 请求执行 | 产品 E2E |
| E2E CRUD 通过率 | M2 离线 MockTransport | 1/1 PASS | 产品 E2E |
| 模型调用/ tokens | 本轮 | 0 / 0（未调用真实模型） | 本记录 |
| 失败路径用量保留 | 归档 M2 的已知调用 | 2 次 / 22574 tokens | 单测 |

## 11. 仍不能自动决定的问题

1. **一个子句多个 action**：当前保守地视为约束，不产生实例，避免擅自拆成 N 个请求；若
   原文确实在一次子句里请求多个操作，需要更明确的“动词-操作”对齐或人工确认。
2. **无状态码、无 selector 的“确认成功”**：程序无法知道成功标准，仍应
   `NEEDS_REVIEW`；不能凭“下一条是 200”推断。
3. **同一资源 vs 新资源**：没有显式“同一个/新的”或 approved policy 证据时，程序不合并
   两个 CREATE 的资源身份，必要时报歧义。
4. **重复表述的边界**：已支持“连续读取同一资源 3 次”，但“读三次”“重复查询两遍”等
   变体尚未全部覆盖；不能靠 `operation_id` 去重来“猜”。
5. **多 CREATE / UPDATE / 替换的生命周期**：IR 目前覆盖 create/get/delete/readback
   主链；update/replace 与多资源槽位仍主要走旧路径。
6. **负向用例与清理**：带 `negative_test` 的策略、自动 cleanup 的完整编排仍走旧路径；
   IR 启用条件刻意排除它们。
7. **分组规划**：一个任务多个 Design 组时，只有组覆盖全部 IR 实例才启用编译器；部分覆盖
   仍走旧路径，避免跨组丢实例。

## 12. 下一步真实模型验收建议（需单独授权）

本轮没有调用真实模型，也没有消耗 M2/F1。建议下一轮作为**独立验收**，不要复用 M2/F1：

1. 新建干净 HEAD，先跑 preflight，确认目标夹具、预算、approved operations/literals 一致；
2. 选 1 个 M2 类 4 步 CRUD + 1 个 F1 类 6 步生命周期，使用**新位置**（不复用已消耗的
   M2/F1），每个 case 最多 2 次模型调用、12 个请求、150 秒；
3. 真实模型只用于 Focus/Design；构造、依赖、参数绑定、排序全部走新 IR/编译器；
4. 记录 `planning_ir.public_summary()`、`edge_reasons`、实例覆盖、每个请求的方法/路径/
   状态码、动态 id 来源、用量；失败时保留原始输出与诊断，不自动重试；
5. 通过标准：4/4 实例、0 多余请求、0 假环、动态 id 全部来自 CREATE、删除后回读 404、
   E2E PASS；任何歧义转 `NEEDS_REVIEW`，不追求“凑 PASS”；
6. 结束时保持 M2/F1 历史归档、rubric、评分、原始证据不变；报告中明确“真实 LLM E2E”
   与“MockTransport 离线 E2E”是两类指标。

## 13. 复现命令

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_planning_ir.py -q
.\.venv\Scripts\python.exe scripts/rw_lifecycle/planning_replay.py
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe scripts/check_full.py --jobs 4
```
