# P3-B2.2 义务驱动监督与可靠终止

起点 `master` 为 `83eea0fbee8f90c8b30247e1cace692faea4587d`，与 `origin/master` 一致；
工作区只有既有未跟踪的 `.venv-lock/`。上一轮结论为 NO-GO
（`docs/deepagents-hitl-reliability-p3b21.md`：最终源码两次独立运行 29/31 与 27/31）。
本轮只改三处：终止判据、剩余义务排程、必要的有界修复；没有新的 Agent 框架、模型适配器、
Web Console 重构，也没有放宽任何完成判据。

**结论先说：机制完成并通过确定性验证，但真实稳定性门槛未达到 —— 本轮 NO-GO。**
最终冻结源码 `6613b8cb937b30174048383b5867a267392c8b60969f7a80c5bc0bc7d84f96e4`
上五次完整发版为 2/5 达到 31/31，低于 4/5；逐轮归因见 `## 四次真实批次`。

## 遗留问题与复现

| 上一轮证据 | 原因 | 本轮最小复现 |
| --- | --- | --- |
| `release-final` 29/31，`stop_reason=NO_PROGRESS`，`web_rule_7` 四项负向没有执行证据 | 框架把“模型不再行动/预算停止”当成结束。没有任何判据在结束前检查已批准义务账本，模型的一句总结即可结束整轮。 | 29/31 时模型直接说结束 → `test_a_closing_answer_is_refused_while_two_approved_checks_are_missing` |
| `release-r4` 27/31，`stop_reason=ANSWER`，`web_rule_7` 两次 `JSON_SYNTAX` 后批次被放弃 | 同上；且 `JSON_SYNTAX` 只有代码、没有位置和原因，模型只能整段重写。 | 同一用例 + `test_a_persistent_json_syntax_failure_is_repaired_with_precise_feedback` |
| `negative_batch_7` 第二次 `CONSTRUCTION_REFERENCE_INVALID` 只给空 `fields` | 构造期拒绝没有指出是哪个步骤、哪一段引用、允许哪些 `from_step`。 | `test_a_reference_to_a_nonexistent_step_names_the_allowed_ids`、`test_a_construction_reference_failure_carries_the_step_and_allowed_ids` |
| `negative_batch_5` 的 `MODEL_SCHEMA_EXTRA_FIELD` 只有 `fields:["*"]` | 结构化回显把字段名整体遮蔽，模型无法知道该删哪一个键。 | `test_an_extra_schema_field_is_named_so_the_repair_can_remove_it` |

## 终止判据

新增 `src/agenttest/obligation_gate.py`（确定性、无框架依赖）。它以**实测回执**为唯一计数口径
（`executed_evidence(source summary)`：一个被计划但没发送的步骤不是证据），在模型请求结束时给出
判决，并把终态单独记为 `agenttest.obligation-termination.v1`。

| 条件 | 动作 | 终态与代码 |
| --- | --- | --- |
| 全部已批准义务都有实测回执，且每个写入都有清理结论 | END | `COMPLETE` / `OBLIGATIONS_COMPLETE` |
| 义务齐备但仍有写入结果未定 | END | `BLOCKED` / `WRITE_OUTCOME_REQUIRES_REVIEW` |
| 模型服务持续不可用（明确了 429/500/502/503/504 用量或网关停止） | END | `BLOCKED` / `MODEL_SERVICE_UNAVAILABLE` |
| 运行时被外部条件终止（授权变更、暂停、人工审批、运行时异常） | END | `BLOCKED` / `STOPPED_<reason>` |
| 剩余业务请求或模型/工具调用不足以派发下一组 | END | `BLOCKED` / `OBLIGATION_REQUEST_BUDGET`、`OBLIGATION_MODEL_CALL_BUDGET`、`OBLIGATION_TOOL_CALL_BUDGET`、`OBLIGATION_DEADLINE` |
| 没有可派发的组（所属批次缺失、接口未就绪） | END | `BLOCKED` / `OBLIGATION_AUTHORITY_LIMITED` |
| 该组尝试次数用尽 | END | `PARTIAL` / `OBLIGATION_ATTEMPTS_EXHAUSTED` |
| 连续两次拒绝结束但没有任何新实测义务 | END | `PARTIAL` / `OBLIGATION_STALLED` |
| 拒绝次数达到上限（6） | END | `PARTIAL` / `OBLIGATION_CONTINUATION_LIMIT` |
| 上述都不成立 | CONTINUE | 把同一批准范围、下一组 id、剩余预算送回模型 |

- 合法终止保持可达：义务全部完成、授权不足、预算耗尽、模型服务持续不可用、无法构造合法
  测试（形式/语义修复上限）、有界修复与无进展上限，各自对应上表的终态。
- **模型总结永远不是结论。** 门禁要么拒绝结束（继续派发），要么放行并由确定性判据给出终态；
  `stop_reason` 仍是循环自身的事实（`ANSWER`、`NO_PROGRESS`、`MODEL_ERROR`…），完成语义仍由
  `agenttest.task-completion.v1` 给出，三者互不替代。
- 真实业务断言失败按“已测量”计入，不重试：`readAvailability` 的真实 503 记为
  `ASSERTION_FAILED`，既不阻塞其他义务，也不会被当成未完成而无尽重跑。
- 上限：单次运行最多 6 次拒绝结束；连续 2 次无新义务即停；每组最多 3 次运行，
  第三次只对形式错误开放；运行时自著修复约束每组最多 1 次。计数器随
  `group_runs`/`group_notes` 一起进入持久化状态，恢复不会重置预算或上限。
- 接入点在 `AgentSession` 的 `after_model` 钩子（`@hook_config(can_jump_to=["model","end"])`，
  返回 `{"jump_to": "model", "messages": [...]}`）。钩子排在中间件列表最后，使
  `ModelCallLimitMiddleware` 的 `jump_to="end"` 仍然最后生效——预算用尽不能被门禁的“继续”
  覆盖；`jump_to` 是粘性状态，非拒绝时一律显式清空为 `None`，否则一次重定向会永久生效。
  门禁通过 `run_agent(..., obligations=dict())` 显式开启，未开启时保持原框架停止规则，
  因此既有 86 个运行时/持久化用例语义不变。
- **已知结构边界（本轮实测）**：预算侧的“无进展上限”在图的执行层生效，此时没有新的
  模型结束请求可供拒绝，门禁只能在事后把终态记为 `PARTIAL` + `OBLIGATION_NOT_DISPATCHED`
  （t4、t5、u3 就是这种形态）。它不会伪装完成，但也不会替模型补测那些义务。

## 剩余义务排程

- `dispatch_plan(project, summary, runs, attempt_limit)` 只从已批准范围与既有分组/批次生成，
  不会为凑数新建组；排序为（已运行次数、普通组优先、待测数、`group_id`），完全确定。
- 已完成义务不再派发；半批次只请求缺的检查（`policies` 只含未测项，`minimum_requests`
  只按缺的重算）。批次结束后未完成的义务留在账本里，下一轮按同一 id 继续。
- 负向检查只由**所属批次**派发。普通分组即使共享同一操作也不得接管；所属批次缺失时
  报 `OBLIGATION_AUTHORITY_LIMITED`，而不是把同一义务映射到新资源或新分组
  （`test_an_obligation_no_group_owns_is_blocked_instead_of_widening_the_scope`）。
- 计划、资源身份、依赖、授权与预算都从原 `summary`/`Planning IR` 读取，不重新规划。

## 有界修复

### 设计批次（上一轮的两处致命失败）

| 诊断 | 本轮补充 | 用途 |
| --- | --- | --- |
| `JSON_SYNTAX` | `detail.position/line/column/reason`，`reason` 取闭集（`MISSING_COLON` 等 8 种），错误原文从不序列化 | 模型能直接改指针位置 |
| `MODEL_SCHEMA_*` | 标识符形状的未知/缺失键回显到 `fields` 与 `detail.unknown_fields`（≤4），敏感值仍遮蔽 | 只删/补真正的字段名 |
| `CONSTRUCTION_REFERENCE_INVALID` | `detail.reference`（`SELF_REFERENCE`/`SOURCE_UNKNOWN`）、`step_id`、`allowed_from_steps`，`fields` 精确到 `plan.steps.<i>.arguments.<j>.from_step` | 修复引用而不改批准内容 |

运行时可以在第二轮把**同一批准批次 + 结构化诊断**原样重发（`MAX_RUNTIME_REPAIRS=1`），
约束文本 ≤900 字符，明确要求“保留全部策略及其已批准预期；不得新增、改名、只读化或删减；
`from_step` 只能引用本方案已声明步骤”。
语义类拒绝（范围、预期、未批准取值）不自动修复，仍需模型给出新的约束。
进度一律按**新增实测义务**计量，不看新增文本或新增证据条数。

### Focus 提案（真实批次暴露的三处缺陷，均已修）

| 缺陷 | 真实证据 | 修复 |
| --- | --- | --- |
| `FOCUS_SOURCE_CLAUSE_MISSING` 不指出是哪个键 | `release-s1/s2`、`release-t2`、`release-u4`：0 次业务请求即终态 | `decode_requirements` 现在把越界键写进 `fields`（`requirement_map.request_2`）并给出 `expected_requirement_keys`/`trusted_scope_clauses` |
| Focus 修复提示要求“每条原文子句各给一项”，把受信任范围条款也列为必填 | `release-s1/s2` 的修复轮次必然重犯同一拒绝（解码器要求 `set(atoms) - scope`） | `focus_repair_payload(..., scope_sources=...)` + `focus_scope_sources(project, summary)`：`requirement_keys` 排除范围条款，并显式说明“不要为它们填条目” |
| 诊断自带 `detail` 时，修复载荷丢掉 `allowed_operations` 与提示（本轮自引入，已由冻结回归捕获） | `release-t2` 的修复轮次改成 `FOCUS_SOURCE_REFERENCE_INVALID`（凭空造 source_ref） | 载荷改为“服务端词汇 + 兜底提示 + 诊断自有键覆盖”，验证器自带的 `hint`（如 `*_source_id` 用法）保持不被覆盖 |

## 测试入口

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_obligation_gate.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_product_focus.py tests/test_mixed_planning_round3.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_diagnostics.py tests/test_plan_construction.py `
  tests/test_json_literals.py tests/test_batch_diagnostics.py tests/test_reference_diagnostics.py `
  tests/test_product_design.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_product_agent.py tests/test_product_durability.py `
  tests/test_step_recovery.py tests/test_task_completion.py tests/test_scope_ledger.py `
  tests/test_product_supervisor.py tests/test_negative_post_observation.py
.\.venv\Scripts\python.exe scripts/run_frozen_regression.py --out .eduwork/p3b22-frozen-final4 --exclude-local-environment
```

定向结果：门禁 28 passed；Focus/混合规划 80 passed + 1 xfailed；
诊断/构造/JSON/设计 120 passed；运行时/持久化/完成/范围 118 passed；
request-requirements / requirement-ledger / planning-ir / focus 163 passed + 1 xfailed；
`ruff check src tests` 通过。

## 全量离线回归（最终冻结源码）

`scripts/run_frozen_regression.py --out .eduwork/p3b22-frozen-final4 --exclude-local-environment`：

- **2897 passed, 15 skipped, 1 xfailed, 0 failed**，2887.35 s。
- 收集 2913 个 ID，漏项 0、重复收集 0、额外 0、重复执行 0。
- `before.json` 与 `after.json` 的工作区摘要一致
  （`15f2d717de5232b4af2e6a6c838235a6a5958b2a9b44c9be86f752a1371ceaf4`），
  `same_checkout=true`、`exit_code=0`。

## 四次真实批次

真实 ecnu-max、真实 HTTP、隔离靶场，每轮独立靶场、独立任务、严格顺序执行，没有跳过、
没有挑选、没有重跑同一轮。四次批次分别在四个源码摘要上执行（每次改动都有测试与冻结回归）：

| 批次 | 源码摘要 | 批内结果 | 说明 |
| --- | --- | --- | --- |
| `release-r1…r5` | `f30633bd…` | **5/5 达到 31/31** | 门禁/排程/有界修复的首个冻结版本 |
| `release-s1…s5` | `943a4a52…` | 3/5 达到 31/31 | 修正终态归属顺序后；两轮在 Focus 提案阶段 0 请求终态 |
| `release-t1…t5` | `65454ca1…` | 0/5 达到 31/31（7、0、30、23、28） | 加入 Focus 精确诊断与修复提示后；同批出现未知用量与设计覆盖不足 |
| `release-u1…u5` | `6613b8cb…`（最终） | **2/5 达到 31/31** | 修复载荷合并缺陷后；两轮完成，两轮提前终态，一轮 Focus 终态 |

**门槛只认最后一批**（最终冻结源码）：2/5 < 4/5 → NO-GO。前三次批次如实披露，
不用来替代或拼凑结论。

| 轮次 | 义务 | 未覆盖负向 | 清理凭据 | 业务 HTTP | 模型调用 | Token | 停止原因 | 门禁终态 |
| --- | --- | --- | --- | ---: | ---: | ---: | --- | --- |
| `release-u1` | 21/31 | 10 | 5 清理 + 10 无副作用，未决 0 | 54 | 18 | 未知（用量不可知） | `MODEL_ERROR` | `BLOCKED / MODEL_SERVICE_UNAVAILABLE` |
| `release-u2` | **31/31** | 0 | 15 + 10，未决 0 | 94 | 28 | 235,127 | `ANSWER` | `COMPLETE / OBLIGATIONS_COMPLETE` |
| `release-u3` | 17/31 | 14 | 1 + 10，未决 0 | 38 | 18 | 未知（用量不可知） | `NO_PROGRESS` | `PARTIAL / OBLIGATION_NOT_DISPATCHED` |
| `release-u4` | 0/31 | 24 | — | 0 | 2 | 29,364 | Focus 拒绝 | 无（未进入规划） |
| `release-u5` | **31/31** | 0 | 15 + 10，未决 0 | 95 | 34 | 266,889 | `ANSWER` | `COMPLETE / OBLIGATIONS_COMPLETE` |

前三次批次逐轮：

| 轮次 | 义务 | 清理凭据 | HTTP | 模型调用 | Token | 停止 | 终态 |
| --- | --- | --- | ---: | ---: | ---: | --- | --- |
| `release-r1…r5` | 各 31/31 | 各 15 + 10 | 94/95/95/94/95 | 28/27/33/31/31 | 240,723 / 219,355 / 278,403 / 270,536 / 263,714 | `ANSWER` | 各 `COMPLETE` |
| `release-s1` | 0/31 | — | 0 | 2 | 29,469 | Focus 拒绝 | 无 |
| `release-s2` | 0/31 | — | 0 | 2 | 27,685 | Focus 拒绝 | 无 |
| `release-s3…s5` | 各 31/31 | 各 15 + 10 | 95/95/94 | 38/36/28 | 324,270 / 294,808 / 249,829 | `ANSWER` | 各 `COMPLETE` |
| `release-t1` | 7/31 | 1 + 0 | 8 | 13 | 105,069 | `NO_PROGRESS` | `PARTIAL` |
| `release-t2` | 0/31 | — | 0 | 2 | 28,518 | Focus 拒绝 | 无 |
| `release-t3` | 30/31 | 15 + 9 | 92 | 40 | 335,793 | `ANSWER` | `PARTIAL / OBLIGATION_ATTEMPTS_EXHAUSTED` |
| `release-t4` | 23/31 | 9 + 8 | 65 | 26 | 218,637 | `NO_PROGRESS` | `PARTIAL` |
| `release-t5` | 28/31 | 12 + 10 | 83 | 37 | 309,992 | `NO_PROGRESS` | `PARTIAL` |

二十轮合计 1286 次业务 HTTP、474 次模型调用；除 `u1`、`u3` 两轮明确记为用量不可知外，
其余 Token 全部已知。**没有任何一轮把未测完的义务报成完成**：所有提前结束的轮次都是
`PARTIAL` 或 `BLOCKED`，且逐条列出未覆盖策略。

## 逐项归因

按本轮固定分类统计 20 轮：

| 类别 | 轮次 | 证据 |
| --- | --- | --- |
| 规划失败 | 0 | 没有 `PLANNING_*` 终止；Focus 拒绝均属模型提案不合法，不是规划器缺陷（三处产品缺陷已修，见上） |
| 模型生成失败 | 9 | `s1`、`s2`、`t2`、`u4`（Focus 提案两次不合法，0 请求）；`t1`、`t3`、`t4`、`t5`、`u3`（负向批次设计覆盖不足：`web_rule_3_negative_5`、`web_rule_7_negative_4…9` 反复构造失败，达到组尝试上限或无进展上限） |
| 监督调度失败 | 0 | 门禁在 20 轮里的终态全部与证据一致；`u2`、`u5` 是单次 `OBLIGATIONS_COMPLETE`；`t3` 正确给出 `OBLIGATION_ATTEMPTS_EXHAUSTED`；没有无效派发、没有丢失义务、没有重复派发 |
| 业务断言失败 | 20 轮均保留真实 503 | `web_rule_1`（`readAvailability`）每轮都是 `ASSERTION_FAILED`（`http_status [200] vs 503`、`in.$.state ["ready"] vs "unavailable"`），从未被重试或吞掉 |
| 外部网关/模型服务失败 | 2 | `u1` 以 `MODEL_ERROR` 结束（`ledger.usage_known=false`），`u3` 也出现未知用量后 `NO_PROGRESS`；`t` 批次同夜亦出现未知用量 |
| 授权或预算阻塞 | 0 | 没有 `OBLIGATION_*_BUDGET`、`OBLIGATION_AUTHORITY_LIMITED` 或越权请求；越权探针每轮 5/5 被拒 |

时间上，`r`（17:40 前后）5/5、`s`（19:00 前后）3/5、`t`（20:50 前后）0/5、`u`（22:40 前后）
2/5，与同一晚模型服务逐步变差、并出现“未报告用量”的响应一致；这也是把主要失败归为
模型生成/模型服务而非调度机制的实测依据。安全与清理没有随质量下降而恶化。

## 安全计数（20 轮合计）

- 负向策略重复测量 0；同一策略每轮最多一次，`min = max = 1`。
- 写入重放 0；`uncertain_dispatches` 每阶段 0；`WRITE_OUTCOME_UNKNOWN` 0。
- 清理：每个创建都有 `CREATED_AND_CLEANED` 回执，被拒写入为 `NO_SIDE_EFFECT_PROVEN`，
  `CREATION_UNPROVEN`/`CREATED_CLEANUP_FAILED`/`CLEANUP_UNPROVEN` 全 0；靶场库存每轮归零。
- 越权探针：每轮 5 项全部被拒（`boundary_rejected=true`）。
- 预算：跨人工审批续跑不重置；`u1`/`u3` 的未知用量被如实记为不可知，没有估算或替换。

## 未纳入本轮范围

- Web Console、模型适配器、Agent 框架、Tracing/Evals 扩建、无关重构：均未改动。
- 完成判据没有放宽：`complete`/`checks_passed`/`strict_complete` 三条口径与上一轮一致，
  断言失败仍不重试、不自动修改。
- 负向批次的**设计质量**（`web_rule_3_negative_5`、`web_rule_7_negative_4…9` 的构造成功率）
  与 Focus 提案一次成型率属于模型能力问题，不在本里程碑声明的“监督终止/剩余义务排程/
  必要有界修复”范围内；本轮只保证它们失败时不会被伪装成完成。

## 结论与下一步

**NO-GO。** 判定依据是里程碑自己写的门槛：最终冻结源码 5 轮完整发版只有 2/5 达到 31/31
（要求 ≥4/5 且清理证据可信）。清理证据、安全计数与真实 503 检测在 20 轮里全部达标，
机制层面的目标（义务驱动的终止判据、剩余义务排程、有界修复）已实现并通过确定性与真实
双重验证。

门槛未达的直接原因不是监督机制，而是模型侧：20 轮里有 4 轮（`s1`、`s2`、`t2`、`u4`）
在 Focus 提案阶段就 0 请求终态，负向批次设计在夜间的成功率明显下降，并出现两次
“用量不可知”的模型响应。
下一步（建议直接进入，不需要再改本轮代码）：

1. 在模型服务健康的时间窗，用同一最终冻结源码 `6613b8cb…` 重跑五次发版批次；
   代码已冻结、回归已通过，重跑只消耗模型调用。
2. 若重跑仍 <4/5，则把“负向批次设计覆盖”与“Focus 提案重试”作为下一个里程碑
   （有界修复的延续），而不是继续在本里程碑内扩大范围。

验收证据：`.eduwork/p3b22-frozen-final4/`（最终源码全量回归与 ID 审计）、
`.eduwork/p3b22-live/release-{r,s,t,u}1…5/`（每轮 `manifest.json`、`phase-*.json`、
`target-events.jsonl`、`model-outputs.jsonl`）。

## 2026-11-17 优化版冻结复验

**NO-GO。** 这批有 3/5 轮覆盖 31/31 批准义务，低于预先约定的 4/5。P3-B3 未获准进入。
已完成的优化后离线回归共收集 2,914 个 ID。漏项、额外项、重复收集和重复执行
均为 0。2,898 passed、15 skipped、1 xfailed、0 failed、1 warning。
运行耗时 2,308.40 秒，历史记录为 2,887.35 秒，差值为 578.95 秒。

原始证据在 `.eduwork/frozen-regression-performance/`，含 `tests.json`、
`pytest.log`、`before.json`、`after.json` 和 `result.json`。回归前后快照
逐字节一致。更新报告前，当前工作区重算的完整摘要也与归档快照一致。

| 复验基线 | 标识 |
| --- | --- |
| 基础提交 | `ee01cf6393cd34b5427382ed2a2243c28d83c679` |
| 回归时工作区 SHA-256 | `fb04fea47355ba45a3130e1142f9ef76b1d4054963b0978cc8f311f9bc4e23ed` |
| Python 源码 SHA-256 | `104211aa39ea9edb0be8b45b4cafc80a5c97d8bb44e5037219c15af884e2b0d3` |
| 用例账本 SHA-256 | `64d3a32533b50fb92c3b7338b55ae4f756b6968ee6df3e54b2883273f20c43e2` |
| 复验公开配置指纹 | `cecf6dff2dccf853e1da5327457631243d9d9271f0575d1c6a823261aca52172` |

回归时补丁保存在 `.eduwork/p3b22-retest-regression.patch`。五处原有工作区
修改覆盖冻结核验、根目录忽略规则、回归日志、核验专项测试和进度文档。
本节与进度记录为回归后更新的文档。历史 u1–u5 的结果仍为 **NO-GO（2/5）**。
历史 u4 在 Focus 提案阶段退出，业务 HTTP 为 0，因而该轮没有 503 断言证据。

普通 PowerShell 已确认 `socket.socketpair()` 和 `asyncio.new_event_loop()`
正常，受限工具会话连接同机 TCP 回环地址仍返回 `WinError 10013`。普通终端
首试在健康探针前被 Windows 虚拟环境父进程误拦。启动器只排除本次进程及其
匹配的虚拟环境父进程后，复验在普通终端完成。使用的命令如下。

```powershell
.\.venv\Scripts\python.exe -X utf8 -u .eduwork/p3b22-stability-retest.py --run
```

入口核验了源码、回归账本和原模型配置。三次 ecnu-max 探针均在 1 秒内返回
HTTP 200，明确用量分别为 147、65、147 tokens。正式批次于北京时间
13:20:50 至 13:44:57 逐轮运行，共 24 分 07 秒。五份 manifest 的提交、源码摘要、
验收目标和 HITL 配置一致，各有独立任务 ID、提交 ID、靶场实例和端口。
归档的复验时工作区摘要为 `c55c11bba4fb2a3d9e32fdd9b51bbf14b066b518d77f91c8e31eddd0ce279a7c`。
相对通过全量回归的工作区，启动前只更新了本报告和进度文档。环境使用
Python 3.11.15、Deep Agents 0.7.35、LangChain 1.4.10、LangGraph 1.2.20。

| 轮次 | 批准义务 | 负向策略 | 业务 HTTP | 清理 / 库存 | 终态与归因 | 终态文件 |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| v1 | 31/31 | 24/24 | 95 | 15 / 0 | `COMPLETE`，第 7 个负向批次曾因引用未知前序步骤被拒，后续覆盖齐全。 | `release-v1/phase-09.json` |
| v2 | 14/31 | 7/24 | 29 | 1 / 0 | `BLOCKED`，第 3 个负向批次曾返回缺冒号的 JSON，第 4 个批次设计请求 60.016 秒超时，用量未知。 | `release-v2/phase-04.json` |
| v3 | 0/31 | 0/24 | 0 | 0 / 0 | `NEEDS_REVIEW`，首次 Focus 请求 60.031 秒超时，用量未知。 | `release-v3/phase-00.json` |
| v4 | 31/31 | 24/24 | 95 | 15 / 0 | `COMPLETE`，第 5 个负向批次曾返回缺冒号的 JSON，后续覆盖齐全。 | `release-v4/phase-08.json` |
| v5 | 31/31 | 24/24 | 95 | 15 / 0 | `COMPLETE`，全部批准义务有执行证据。 | `release-v5/phase-09.json` |

- 逐轮以冻结目录中的终态 JSON、模型轨迹、SQLite 请求账本、审批和靶场事件
  对账。314 条业务 HTTP 均落在批准的靶场操作内，四个运行到执行阶段的任务
  均拒绝五项越权探针。83 条 POST 命中已批准的同组 HITL 记录，共有 30 条批准记录。
  所有 HTTP 账本条目为第 1 次尝试且有提交证据，没有未知写入自动重放。
- 79 条负向策略执行记录均为各轮内单次派发。46 次 HTTP 201 创建均有同资源
  删除和 404 回读证据，37 次负向 HTTP 422 均有无副作用证据。五个靶场库存均为 0。
- 实际 `readAvailability` HTTP 503 共 11 次（含调查复测）。执行到该操作的
  四轮都留有 `ASSERTION_FAILED`，预期 HTTP 200、实际 503。v3 未执行该操作。
  三个 `COMPLETE` 仅表示批准义务覆盖完毕，三个终态的 `strict_complete` 和
  `checks_passed` 均为 `false`。v2、v3 的 `complete` 均为 `false`。
- 105 次正式模型调用中有 103 次用量已知，共 827,995 tokens。v2、v3 的两次
  `MODEL_TIMEOUT` 以 SQLite 账本的 `UNKNOWN` 保留。v2 后继停止和 v3 的零业务
  请求符合未知用量门槛。现有证据可确认客户端超时和生成错误，尚无法定位服务端
  超时原因，也没有发现需要修改安全门槛的证据。

原始证据目录为 `.eduwork/p3b22-retest-20261117T052048Z-d4a1f9a3/`。目录根包含
`batch.jsonl`、`health.jsonl` 和 `positions.jsonl`，各轮目录含 `manifest.json`、
`phase-*.json`、`model-outputs.jsonl`、`target-events.jsonl` 与
`lab/workspace/tasks.sqlite3`。后续应先核对这两次超时对应的服务端记录和用量，
再用已存 JSON 与引用失败样本离线检查现有约束生成和有界修复的覆盖。
