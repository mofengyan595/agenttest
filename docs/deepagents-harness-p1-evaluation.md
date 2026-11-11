# Deep Agents Harness P1 评估与自主测试架构升级

日期：2026-11-11 · 基线：`1a8c1b9`（master）· 目标仓库：`mofengyan595/agenttest`

本文件是 P1 的正式交付说明：五个阶段的实测结果、真实模型与真实 HTTP 证据、仍然存在的失败、
以及 ADAPT 决策和迁移计划。所有数字都来自本仓库 `.eduwork/runs/p1/` 下的证据文件；未运行的检查
在文中明确标注为“未运行”，没有用推断替代实测。

---

## 0. 结论摘要

| 问题 | 结论 |
|---|---|
| `ecnu-max` 能否通过 LangChain ChatModel 使用**原生** Tool Calling？ | 能。9 项 LangChain 适配探测全通过，工具调用来自 `tool_calls` 字段而非 JSON 文本，见 §1。 |
| Deep Agents 能否只保留 8 个逻辑工具？ | 能，但必须**实测**：profile 只过滤“模型视图”，执行器仍注册默认工具；窄化后用探针验证才可信，见 §2.3。 |
| 默认工具能否可靠禁用？ | 能禁用 9 个默认工具与 general-purpose subagent；但 `FilesystemMiddleware`/`SubAgentMiddleware` 是构造期硬依赖，不能剔除，见 §2.2。 |
| P0 的三个正确性缺陷是否修好？ | 3.1 与 3.2 已修复并有真实 HTTP 回归（S1–S5）；3.3 改为“有 IR 就用 IR，无 IR 必须留痕”，见 §3。 |
| A/B 谁更好？ | 见 §4。B 的终止性与自主适应更强，A 的请求更省、覆盖更稳。**严格 `complete` 在 A 上 4 个目标仍全部为 false**，这一事实未被 harness 更换掩盖。 |
| 最终决定 | **ADAPT**：把 Deep Agents 作为可选循环层接在确定性执行器之上，不作为产品默认路径，见 §5。 |

---

## 1. 阶段一：原生 Tool Calling 准入测试

### 1.1 环境与版本

| 环境 | 用途 | 关键版本 |
|---|---|---|
| `.venv` | 生产（A 组、全部 pytest） | langgraph 1.2.14、langchain-core 1.6.9、httpx 0.28.1、pydantic 2.14.0、pytest 9.1.1；**未安装** langchain / deepagents |
| `.venv-deepagents` | P1 隔离实验环境 | deepagents 0.7.35、langchain 1.4.10、langchain-core 1.7.7、langgraph 1.2.20、pydantic 2.14.3 |

两个环境互不污染：生产依赖没有因为实验而升级，`.venv-deepagents` 也不是产品默认环境。

### 1.2 网关协议探测（不经 LangChain）

`.eduwork/runs/p1/langchain-adapter*.json` 之前还有一次裸 HTTP 探测，结论是网关的四处非标准行为，
适配层必须显式处理，否则会把“JSON 文本决策”误当成工具调用：

1. `assistant` 消息带 `"tool_calls": []` 会 HTTP 500；省略该键返回 200。适配层只在真有调用时才写这个键。
2. `tool_choice` 必须是对象形式；字符串 `"any"` 不被接受，适配层把它映射成显式指定单一工具。
3. 同一请求里出现重名工具会 HTTP 500 且响应体为空，因此工具名去重是发送前的前置条件。
4. 成功响应带 `finish_reason="tool_calls"` 与 `usage`，并支持并行工具调用。

### 1.3 LangChain 适配探测（9/9）

`experiments/deepagents_p1/probe_langchain.py` 用真实 `ecnu-max` 经 `EcnuxChatModel` 跑 9 项断言，
证据在 `.eduwork/runs/p1/langchain-adapter-v3.json`：

- 工具调用出现在 `AIMessage.tool_calls`，结构化解析，**不是**从 `content` 里抠 JSON；
- 多轮 `tool_calls → ToolMessage → 再决策` 闭环成立；
- 工具报错作为 `ToolMessage` 回灌后模型会改变策略，而不是复读同一调用；
- 工具选择（`tool_choice` 显式指定）生效；
- 并行调用在同一条 `AIMessage` 内返回并被逐个回填；
- 未出现空 `tool_calls` 键导致的 500（适配层已规避）。

结论：**原生 Tool Calling 验证通过**，不是 JSON 文本模拟。

---

## 2. 阶段二：Deep Agents 最小自主循环

### 2.1 结构

新增包 `src/agenttest/deep_harness/`：

| 文件 | 职责 |
|---|---|
| `chat_model.py` | `EcnuxChatModel`：把网关包成 LangChain `BaseChatModel`，处理 §1.2 的四个非标准点，逐次记录 `ModelCallStats` |
| `loop.py` | 装配：`create_deep_agent(model, tools, system_prompt)`、9 个默认工具的窄化与**实测**校验、`run_goal()` 的有界循环（`recursion_limit`）、`HarnessUnavailable` 失败即拒跑 |
| `product_tools.py` | `ProductSession`：8 个逻辑工具的真实实现，全部落在既有确定性组件上（`design_groups`/`propose_design`/`validate_design_case`/`Workflow`/`ReportStore`），HTTP 只经 `Workflow` 的 Executor |

8 个逻辑工具：`get_contract`、`get_approved_policies`、`get_test_coverage`、`design_test_cases`、
`validate_test_plan`、`execute_approved_test`、`read_test_report`、`investigate_failure`。

### 2.2 被拒绝的能力与实测

`DENIED_DEFAULT_TOOLS = (ls, read_file, write_file, edit_file, delete, glob, grep, execute, task)`，
外加 `GeneralPurposeSubagentProfile(enabled=False)`。

实测（`.eduwork/runs/p1/harness-audit.json`，5 组配置）：

- `A_default`：模型视图含 10 个工具（9 默认 + 2 域工具）——**默认确实是全开**；
- `B_fs_excluded`：文件系统工具全部消失，只剩 `task` 与域工具；
- `C_required_middleware`：试图 `exclude_middleware` 掉 `FilesystemMiddleware` 直接 `ValueError`
  ——`FilesystemMiddleware`/`SubAgentMiddleware` 是构造期依赖，**不能剔除**，只能用 profile 收窄；
- `D_denied_write`：被拒的写调用以 `Error: permission denied for write ...` 回到模型，
  目标文件**未创建**（`file_created: false`）；
- `E_narrowest`：收窄后模型只看到 `[get_contract, get_test_coverage]`。

### 2.3 一个必须记录的安全发现：静态目录检查是假的

第一版用“读取 `agent.nodes["tools"].bound.tools_by_name` 是否只含 8 个工具”来证明收窄，结果失败。
真实机制是：

- `_ToolExclusionMiddleware` 在**模型请求**里过滤 `request.tools`（模型看不到）；
- 同时在**调用边界**拒绝被排除的工具名，返回 `Error: {name} is not available.`；
- 但 `ToolNode` 仍然注册这些工具（执行器层可 dispatch）。

因此静态读目录**不能**证明模型视图。改为离线对抗探针 `verify_enforcement(model)`：
给探针模型一个 `probe_marker` 工具，要求模型视图**恰好**是 `{probe_marker}`，并尝试让模型调用
`write_file`，同时验证边界拒绝与“文件确实没被写”。证据（真实运行，非 mock）：

```
{"model_view": ["probe_marker"], "boundary_rejected": true,
 "tool_outputs": ["Error: write_file is not available."], "files_written": []}
```

**第二个安全发现（本轮修复）**：profile 的注册键来自 `ls_provider[:model_name]`。基线
`BaseChatModel._get_ls_params` 用**类名**推导 provider，于是任何包装子类（记账模型、探针模型）
都会静默指向另一个 profile，收窄形同虚设——这正是 §4 记账改造时踩到的坑（探针看到 8 个默认工具）。
修复：适配器显式实现 `_get_ls_params()` 返回 `agenttest` + 配置中的模型名，并暴露 `model_name`
属性；`install_narrow_profile()` 同时注册 `provider` 与 `provider:model` 两个键并**双键回读**校验。
修好后同一探针重新通过。这条写进报告的原因很直接：**基于类名的安全键不可信**。

### 2.4 失败即拒跑

`run_goal()` 在启动前执行 `verify_enforcement`，任一条件不满足就 `HarnessUnavailable`，
不进入模型循环。集成路径因此没有“先跑起来再收窄”的窗口。

---

## 3. 阶段三：P0 正确性缺陷（硬验收）

### 3.1 删除-404 的前置条件（`product_preconditions.py`）

P0 的真实现象：设计模型提出“读某个包裹、期望 404”，但计划里**根本没有删除**该包裹；实际请求
拿到 200/其它状态，却被记成业务断言失败。修复要点是**双向**的相位规则：

| 方向 | 何时成立 | 违反时 |
|---|---|---|
| `absence`（期望 404/410） | 计划内存在对**同一资源身份**的删除，且该删除在预检时**计划正确** | 预检拒绝：`DESIGN_ABSENCE_PRECONDITION_MISSING` |
| `existence`（期望 2xx） | 该资源没有被本计划的删除操作消费 | 预检拒绝（镜像缺陷：删完再期望可读） |

资源身份用 `ResourceHandle`/`ResourcePhase`/`DependencyEdge`/`Constraint` 表达（Planning IR），
身份由“产生它的步骤 + selector”决定，删除必须命中同一身份。字面 id 的存在性断言被跳过（它不是
产生出来的资源），字面 id 的缺失断言标记 `UNPROVEN`。

运行时（执行后）的三分法，严格区分“不是业务缺陷”和“是业务缺陷”：

1. **前置条件在执行前就不成立** → 预检拒绝/挂起，不发请求；
2. **删除失败或从未建立** → 后续观测永远不判为独立业务缺陷，降级 `NEEDS_REVIEW`，理由
   `PRECONDITION_NOT_ESTABLISHED` / `PRECONDITION_UNPROVEN`；
3. **前置条件已建立，响应仍违反期望** → 这才是合法的 `ASSERTION_FAILED`。

不会把 `ASSERTION_FAILED` 一刀切改成 `NEEDS_REVIEW`：`reclassify_unestablished()` 只降级、从不升级，
并且只降级“方向为 `absence` 且相位未被证明”或“方向为 `existence` 且存在对该资源的删除”的检查。

### 3.2 真实回归（`experiments/deepagents_p1/regression.py`，真实 HTTP）

实验用一个**操作员批准**的 404 期望策略（lab 项目原本一个都没有，见 §6.3），对 4 个 isolate 目标跑：

| 场景 | 预检 | 运行结果 | HTTP 请求 | 结论 |
|---|---|---|---|---|
| S1 期望 404 但计划里没有删除 | **REFUSED** `DESIGN_ABSENCE_PRECONDITION_MISSING` | — | 0 | 缺陷 3.1 被预检挡住 ✅ |
| S1 镜像：删完再期望可读 | **REFUSED** 同码 | — | 0 | 双向规则生效 ✅ |
| S2 删除被 503 打断 | ACCEPTED | `ASSERTION_FAILED`（就在 delete 步） | 2 | 见下方说明 |
| S3 删除成功后回读 404 | ACCEPTED | **PASS** | 3 | 可信路径仍然判缺陷 ✅ |
| S4 删除返回 204 但没真删 | ACCEPTED | **`ASSERTION_FAILED`，未被降级** | 3 | 真缺陷没有被“修复”掩盖 ✅ |
| S5 字面 id 缺失探针 | ACCEPTED | `NEEDS_REVIEW`（`POLICY_DEPENDENCY_MISMATCH`，0 请求） | 0 | 批准策略要求绑定，产品更早拒绝 |

S2 需要如实说明：Executor 在第一个非 PASS 步骤就 `break`（`executor.py:266`），所以删除失败后
**后续回读根本不会执行**；此刻唯一的失败是 delete 自身“期望 204 得到 503”，它既不是对 404 的
误判，也不需要降级。也就是说，**在“删除先于回读”的计划形态里，情形 2 无法产生误报**，
真正会产生误报的是 S1 那种“根本没有删除”，而那已被预检拦死。`reclassify_unestablished()` 因此
是一个防御性分支：它对“报告里同时存在失败的删除与失败的缺失断言”仍然生效（单元测试
`test_a_readback_whose_deletion_failed_is_downgraded_to_review` 等 12 项覆盖），一旦产品将来允许
非致命失败或引入 cleanup 步骤，规则不需要重写。

### 3.3 重复重测抑制（`product_fingerprint.py`）

`scenario_shape`/`scenario_fingerprint`/`failure_signature`/`duplicate_attempt`：同一形状+同一
已执行历史的重复计划在 `execute` 前判定 `DUPLICATE`，返回 `skipped: DUPLICATE` 与原始 `run_id`，
不消耗请求预算。真实运行里出现过一次（见 §4.3 G4 早期版本），抑制生效且模型据此换了策略。

### 3.4 监督轮次与额外轮次的 Planning IR（`planning_ir.py` + `product_design.py`）

规则：监督轮次与额外设计轮次**能拿到 IR 就用 IR**（把计划范围限定在本轮目标上），拿不到就
**显式留痕**，不允许静默降级。真实 A 组记录（`a-goal1.json`）：

```json
{"group_id": "supervision_2_group_1", "planning_engine": "LEGACY",
 "planning_engine_reason": "NO_PLANNING_IR",
 "supervision_round": 2, "supervision_target": "design:web_rule_2",
 "supervision_planning_ir": {"code": "SUPERVISION_IR_UNAVAILABLE",
   "reason": "本轮原始需求的 Planning IR 不可用，补测只能走既有 Legacy 设计路径。"}}
```

即：这个 lab 项目（`parcels`）没有 IR，走 Legacy 是**被记录**的选择，`NO_PLANNING_IR` 与
`SUPERVISION_IR_UNAVAILABLE` 都在报告里，审计者能看到“为什么不是 IR 路径”。

---

## 4. 阶段四：A/B 实测

### 4.1 口径

- A = 生产 LangGraph Round Supervisor（`.venv`，langgraph 1.2.14）；
- B = Deep Agents 最小自主循环（`.venv-deepagents`）；
- 同一 4 个自然语言目标、同一 lab 目标（`scripts/serve_colleague_targets.py`，loopback，隔离数据）；
- 请求数、token 数、工具调用数、终止状态、严格完成标志、误报/漏报、重复抑制全部逐项记录；
  汇总脚本 `experiments/deepagents_p1/summarize_ab.py`，原始数据 `.eduwork/runs/p1/{a,b}-goal{1..4}.json`。

### 4.2 结果总表

| 目标 | A 用例（执行/通过/拒绝） | A 严格完成 | A 请求 / 调用 / token | B 用例（执行/通过） | B 终止 | B 请求 / 调用 / token |
|---|---|---|---|---|---|---|
| G1 测包裹管理 | 3（2/1/1） | **false** | 3 / 4 / 20,561 | 1（1/0） | ANSWER | 1 / 11 / 35,298 |
| G2 删除回归 | 4（1/1/3） | **false** | 2 / 4 / 21,131 | 1（1/1） | ANSWER | 2 / 8 / 21,937 |
| G3 上线前测一轮 | 1（1/1/0） | **false** | 2 / 4 / 19,834 | 4（4/3） | LIMIT | 6 / 20 / 93,166 |
| G4 偶发报错排查 | 3（2/1/1） | **false** | 3 / 4 / 20,716 | 3（3/3） | ANSWER | 8 / 16 / 52,138 |

（B 的 token 按“每次调用统计累加”记录；适配器本身每次调用会重置 `model_stats`，A/B 记账因此
在 `ab_runner.accounting_model` 里累加，产品侧记账未改。）

### 4.3 观察到的事实（包括对 B 不利的）

**B 更强的地方**

- 终止性与自述质量：4 个目标里 3 个给出带证据引用的最终答复；G3 触顶 `recursion_limit` 但已执行
  4 个计划、3 个 PASS，属于“把活干完了但没来得及总结”。
- 会**把拒绝当反馈**：G1 里预检拒绝 delete-404 计划后，B 立刻改成“不删除、不臆造负向断言”的
  4 步生命周期计划并拿到 PASS；A 的同一条拒绝只会让该用例 `REJECTED`，监督层没有把它变成
  新设计指令（G2 甚至 3/4 用例被拒）。
- 工具选择**最终**正确率 7/8（§4.4）。

**A 更强的地方**

- 请求与 token 效率：A 每目标 2–3 个真实请求、约 2 万 token；B 2–8 个请求、2.2–9.3 万 token，
  G1 一个请求就花了 35,298 token（11 次模型调用里有 3 次读事实、1 次被拒计划、几轮重设计）。
- 覆盖稳定性：A 的用例形状由确定性分组给出，G2 仍尝试了 4 个用例（虽然 3 个被拒）；B 在 G1
  只执行 1 个用例，只看 `readAvailability`，对“包裹管理功能”来说**覆盖偏浅**——这是 B 的真实短板，
  不能因为终止性更好就忽略。
- A 的 `supervision` 记录完整（动作枚举、目标、理由、每轮模型调用指纹），可审计性目前仍优于 B
  的自由文本轨迹。

**两边都不好的地方**

- A 的严格 `complete` 在 4 个目标上**全部为 false**（P0 已记录的“不是 4/4 严格完成”在这一轮
  更差），`checks_passed` 也为 false；更换 harness **没有**解决这个问题，报告不掩饰。
- B 的 token 成本高，且 G3 触顶未总结；B 的自动记账（`model_stats`）本身不适用于总量统计。
- 误报/漏报对照：S4 证明修好的规则不会把真缺陷降级；G1 的 `readAvailability` 503 在 A、B 两侧
  都被记为 `ASSERTION_FAILED`，而 B 的 `investigate_failure` 明确给出 `cause_confirmed=false`
  ——两侧都没有把它宣称为已确认缺陷，这点是好的。

### 4.4 工具选择任务（真实模型，8 个 needs）

`experiments/deepagents_p1/tool_selection.py`，证据 `.eduwork/runs/p1/tool-selection.json`：

- **严格“第一个工具就选对”**：3/8。失败模式高度一致——模型总是先读 `get_contract`
  （Deep Agents 默认提示词倾向“先调查再动手”），对“我只想知道已批准断言”这类窄需求属于多余调用。
- **“本次运行内是否用到正确工具”**：7/8。唯一的真失败是“预检 design_1”一例：模型没有
  `validate_test_plan`，而是重新 `design_test_cases`，暴露了**参数可发现性**问题——工具描述没有
  说明 `design_id` 从哪来，模型只能靠猜。

这两条都写进迁移计划（§5.2）：提示词要显式给出“何时可以跳过事实读取”，工具描述要自带参数来源。

---

## 5. 阶段五：决定 —— ADAPT

### 5.1 为什么不是 ADOPT，也不是 REJECT

- **不能 ADOPT**：B 的 token 成本 2–4 倍、G1 覆盖偏浅、G3 未总结、严格首工具选择 3/8；把产品默认
  循环换成自由 agent 会牺牲可审计性与可预测成本，而 A 的覆盖与预算控制仍明显更好。
- **不能 REJECT**：B 在“把确定性拒绝转成下一步设计”这一点上明显更强（G1/G2），而这正是 P0
  反复卡住的地方；权限收窄在实测下成立（§2.3）；8 个逻辑工具足以完成真实目标。
- **ADAPT**：保留确定性骨架与 Executor 为唯一出网口，把 Deep Agents 作为**可选**循环层，
  由操作员按项目开启，默认关闭。

### 5.2 迁移计划（增量、可回退）

1. **接口冻结点**：`ProductSession` 已是纯 Python 对象，产品侧不依赖 deepagents；把
   `deep_harness` 变成可选依赖组（`pip install agenttest[deepagents]`），生产默认不装。
2. **接入开关**：项目级 `loop_engine = LEGACY_SUPERVISOR | DEEP_AGENTS`，默认前者；切换必须留下
   `loop_engine_reason`，与 §3.4 的 `planning_engine_reason` 同一套留痕规范。
3. **把 A 的优点搬给 B**：B 的执行入口前加“确定性覆盖建议”工具（复用 `design_groups`），
   直接治 G1 覆盖偏浅；工具描述补 `design_id`/`plan_id` 的来源说明，治 §4.4 的参数可发现性。
4. **把 B 的优点搬给 A**：A 的监督层在收到 `DESIGN_ABSENCE_PRECONDITION_MISSING` 时，把拒绝理由
   作为下一轮设计指令（现在是丢弃），治 G2 的 3/4 拒绝。
5. **成本闸门**：B 路径默认 `recursion_limit=24`、`max_requests` 从项目预算推导，并在触发
   `LIMIT` 时仍输出“已执行计划 + 未总结”的结构化结论（G3 的体验问题）。
6. **记账**：把 §4.2 的累加逻辑上移为适配器的可选 `totals`，或由会话层累计，避免上层各自实现。

### 5.3 未决风险

1. lab 目标没有原生缺失断言（§6.3），S1–S5 依赖实验脚本注入的批准策略；真实项目要表达
   “路径资源必须消失”，需要一个 `AbsenceApproval` 模式，否则只能靠实验性配置。
2. `FilesystemMiddleware`/`SubAgentMiddleware` 不可剔除——上游若改变这一约束，收窄方案要重测。
3. profile 键仍依赖适配器正确实现 `_get_ls_params`/`model_name`；漏实现会静默失去收窄。已用
   双键回读 + 对抗探针兜底，但这是“必须每次运行都验证”的项，不能一次性配置完就相信。
4. Deep Agents 的默认提示词随版本变化，§4.4 的 3/8 严格选择率可能随升级漂移；升级必须重跑
   `tool_selection.py`。
5. A 的严格 `complete=false` 4/4 是 P0 遗留问题，本轮**未**解决；它需要单独的里程碑。

---

## 6. 复现与验证记录

### 6.1 本轮实际运行的命令

```powershell
# 预检回归（真实 HTTP，4 个 isolate 目标）
$env:PYTHONUTF8='1'; $env:PYTHONPATH='src'
.\.venv\Scripts\python.exe experiments/deepagents_p1/regression.py --out .eduwork/runs/p1/precondition-regression.json

# A/B（A 用生产环境，B 用隔离环境；端口分离避免互相干扰）
.\.venv\Scripts\python.exe experiments/deepagents_p1/ab_runner.py --arm A --goal "帮我测一下包裹管理功能。" --port 18992 --out .eduwork/runs/p1/a-goal1.json
.\.venv-deepagents\Scripts\python.exe experiments/deepagents_p1/ab_runner.py --arm B --goal "帮我测一下包裹管理功能。" --port 18991 --out .eduwork/runs/p1/b-goal1.json

# 工具选择（真实模型，8 个 needs）
.\.venv-deepagents\Scripts\python.exe experiments/deepagents_p1/tool_selection.py

# 汇总
.\.venv\Scripts\python.exe experiments/deepagents_p1/summarize_ab.py
```

### 6.2 检查结果

| 检查 | 命令 | 结果 |
|---|---|---|
| 目标测试 | `pytest -q tests/test_product_preconditions.py tests/test_product_fingerprint.py tests/test_supervision_planning_ir.py tests/test_product_tasks.py tests/test_product_supervisor.py tests/test_resource_lifecycle.py tests/test_planning_ir.py tests/test_mixed_planning_p0.py` | **144 passed**（69.86s） |
| 全量离线 | `pytest -q` | **2704 passed, 15 skipped, 0 failed**（2411.55s，见 §6.4） |
| 静态检查 | `ruff check src/ tests/ experiments/ scripts/` | **All checks passed!** |
| 前端兼容 | 未在本轮改前端；`python -m agenttest.web` 的既有离线用例包含在全量离线中 | 未单列 |
| GitHub Actions | **未运行**——本轮不 push，无法触发远端工作流 | 未运行，非通过 |

### 6.3 诚实标注

- **新增实验开关**：`scripts/serve_colleague_targets.py` 增加 `--delete-fault-status`
  （503/500）与 `--delete-noop`（返回 204 但不删除）。这是**为了测出 S2/S4 才加的**，只影响
  lab 脚本，不影响产品路径；但它确实是本轮新增的测试能力，必须记录。
- **lab 无原生缺失断言**：`parcels`/`reservations`/`directory` 三个 lab 项目的
  `absence_expectations` 都是空的（`probe_absence_policies.py` 证据）。S1–S5 用的是实验脚本
  注入的批准策略 `web_rule_7`，因此“操作员能否在真实项目里表达缺失期望”仍是未验证项。
- **B 的 G1 覆盖偏浅**：只执行 1 个用例（`readAvailability` 503），没有覆盖 CRUD 主流程。
  这是真实结果，未重跑挑选更好的一次。
- **B 的 G3 无最终答复**：`recursion_limit` 触顶，仍执行了 4 个计划；按“未总结”记录。
- **A 的 4/4 `complete=false`**：与 P0 的“不是 4/4 严格完成”一致，未因换 harness 而改变。
- **本轮发现的一个环境交互（已修）**：`checkout_identity()` 用
  `git ls-files --cached --others --exclude-standard` 列出文件并逐个哈希，因此**未被
  `.gitignore` 排除**的本地目录会进入评估冻结。新加的 `.venv-deepagents/` 与 `.eduwork/`
  一度让 `test_cancel_preserves_reservation_and_report_without_sealing_success` 在 20 秒
  内等不到第一个 HTTP（冻结哈希还没跑完）。已把两者加入 `.gitignore`，该测试恢复通过
  （10.30s）。这也解释了为什么“同样的测试在基线源码上也失败”——失败源于工作区里的实验产物，
  不是源码回退。任何把大目录放进仓库根目录的实验都必须先加 ignore。

### 6.4 全量离线回归

```
2704 passed, 15 skipped, 1 warning in 2411.55 s (0:40:11)
```

（`.venv`，`.eduwork/suite-final.log`；2719 项收集，跳过项为既有的环境/可选依赖跳过，
警告为既有的 `TestDesign` 采集提示。第一次跑这套用例时
`test_cancel_preserves_reservation_and_report_without_sealing_success` 超时失败，原因与修复
见 §6.3 最后一条：**不是源码回归**，把 `.venv-deepagents/`、`.eduwork/` 加入 `.gitignore`
后同一测试 10.30 s 通过，全量随后 0 failed。）

---

## 7. 变更清单

新增：

- `src/agenttest/deep_harness/{__init__,chat_model,loop,product_tools}.py`
- `src/agenttest/product_preconditions.py`、`src/agenttest/product_fingerprint.py`
- `tests/test_product_preconditions.py`（12）、`tests/test_product_fingerprint.py`、
  `tests/test_supervision_planning_ir.py`
- `experiments/deepagents_p1/*`（探测、A/B、回归、工具选择、汇总）

修改：

- `src/agenttest/planning_ir.py`（资源身份/相位/边/约束）
- `src/agenttest/product_design.py`（预检接入、监督轮 IR 留痕）
- `src/agenttest/product_tasks.py`（重分类、`precondition_rule`、失败解释）
- `scripts/serve_colleague_targets.py`（两个 lab 开关，见 §6.3）
- `.gitignore`（排除 `.venv-deepagents/`、`.eduwork/`，理由见 §6.3 最后一条）

历史冻结证据（M2/F1 报告与归档）未被修改。
