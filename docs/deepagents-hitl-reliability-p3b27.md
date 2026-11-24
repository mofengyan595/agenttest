# P3-B2.7 v5 单项未完成与分组运行限制诊断

2026-11-23 · 开发模式 · 基线 `47046640fcd7912dd51f5c8a6b8c67fb29e51544`

v5 只剩 `createParcel / web_rule_3_negative_9` 未执行。第一次 Design 的 JSON 无法解析。第二次 Design 可解析，组内另一项通过预检并执行，这一项因未知请求体字段被拒。可信案例诊断到了 Agent 的工具结果。第三次名义上的组运行机会仅供符合条件的形式修复使用，实际入口根据最近一条整体 Design 的 `PROPOSED` 记录拒绝派发，因而没有第三次 Design 请求。

在原冻结目录上离线复放，分组视图和义务门禁都仍推荐派发，而整组及委派的单项调用都返回 `GROUP_RUN_LIMIT`。这是可确定性复现的候选资格不一致。剩余单项有合法方案，仍须独立取得与新预检请求绑定的 HITL 批准。原评分复算仍为 **3/5，NO-GO**。 [原验收审计](../.eduwork/p3b26-acceptance-4704664/audit.json) · [回放结果](../.eduwork/p3b27-diagnosis-4704664/replay.json)

## 最后一项义务

| 字段 | 归档证据 |
| --- | --- |
| 分组 | `negative_batch_3`，组内仅 `web_rule_3_negative_8`、`web_rule_3_negative_9` |
| 操作与身份 | `createParcel / web_rule_3_negative_9`，`POST /parcels`，匿名身份 |
| 批准的负向输入 | `required_missing`，变异目标 `body.meta`。由已有规则从合法基线移除 `meta`，其余字段保持批准形态 |
| 预期与副作用证明 | POST 返回 422。用同一关联标识调用 `observeParcelCreation / web_rule_2`，POST 前后分别观察 `$.count` 为 0 |
| 请求级映射 | v5 的 Focus 修复后已显式提交该项，`operation_id=createParcel`、`check_ref=check_10`、`source_ref=source_3`。映射已建立，执行证据缺失 |
| 最终状态 | `negative_batch_3_case_2` 为 `REJECTED`，批组覆盖记录为 `PREFLIGHT_REJECTED`，终止时义务账本为 `NOT_EXECUTED`。该项没有运行 ID、业务 HTTP 或 HITL 批准回执 |

`executed_evidence` 仅从实际执行回执计入已测义务。Focus 映射和案例预检拒绝各自保留，但不会增加已测数。v5 最终为批准义务 30/31、负向策略 23/24、请求级 23/24。 [P0/P1 审计](../.eduwork/p3b26-acceptance-4704664/audit-p0-p1.json) · [执行证据](../src/agenttest/product_obligations.py#L144) · [义务状态](../src/agenttest/obligation_gate.py#L63)

## 三次机会的时间线

| 机会 | Design 与预检 | 执行、反馈与计数 |
| --- | --- | --- |
| 第一次，17:52:43.161–17:52:47.687（北京时间） | Design 日志第 13–14 行，监督序号 6，模型调用 `98810a70f4f848feb6833027da689e31`，7,655 已知 tokens。请求包含两项。输出在字符偏移 1329（第 1 行第 1330 列）触发 `JSON_SYNTAX / MISSING_COMMA`。`_9` 原文片段出现 `check_meta_missing`、关联头及 `value/quantity/meta` 字段记号。整份输出无有效 JSON，无法把该片段当作已验证的独立用例。 | 无案例进入预检，无 HTTP，义务账本无新增。工具调用 `call_17cb81c7f454438eb6e3a653` 将错误原因和位置记入 Checkpoint `1f1e723a-0a57-6f01-8041-a5dfb7bc3c4d`，进入后续 Agent 检查点 `1f1e723a-3776-6df1-8044-35a7dc5399c4`。已消耗一次组运行。 |
| 第二次，17:52:50.523–17:52:54.435 | 日志第 15–16 行，监督序号 8，模型调用 `e8ccbbb3feff4b43b76e1934cd078756`，7,821 已知 tokens。请求仍包含两项，因为当时均未执行。实际请求携带 Agent 提出的补逗号和保留用例集合的备注，未携带上次结构化 `JSON_SYNTAX / MISSING_COMMA` 字段。整体记录为 `PROPOSED`。`_9` 的 `check_meta_missing` 无自写 `before/after`，请求体把 `name` 写成 `value`。预检拒绝 `OPENAPI_UNKNOWN_BODY_FIELD`，路径 `plan.steps.0.arguments.1.name`，未知字段索引 0，允许字段为 `meta/name/quantity`。 | `_8` 通过预检。`phase-03` 的 HITL 只涵盖 `_8` POST 及配套观察，恢复后 `phase-04` 写入 `negative_batch_3_case_1` 的 PASS。SQLite 三条已提交执行证据为 GET 200 → POST 422 → GET 200，匹配关联标识的观察证明无副作用。`_9` 无派发。工具调用 `call_00_vgEGs5Rux2taFOYVJ4Qj9102` 在 Checkpoint `1f1e723a-50a0-61dd-8049-42fb2bcafd25` 持久化工具返回，后续 Agent 检查点 `1f1e723a-d653-6745-804c-b930e68fa9f7` 关联该结果。已消耗两次组运行。 |
| 第三次名义机会，未获准 | 首次拒绝来自 `call_00_4vIwAeSxyPSjbNF8Ne2z0926`／Checkpoint `1f1e723a-ebc0-690c-8051-861c07dc1dda`。共 6 次直接 `run_test_group` 和 2 次委派 `run_open_obligation` 收到 `GROUP_RUN_LIMIT`。没有第三份 Design Context、模型调用账本或新预检。 | 组运行计数停在 2，该组未记录 `runtime_repair`。委派单项会先增加单项工具计数，随后受同一组规则拒绝。无新增执行证据。门禁先后记录三次 `OBLIGATION_PENDING` 继续决定，最后因连续两次结束请求没有新增已测义务，返回 `PARTIAL / OBLIGATION_STALLED`。任务执行预算停止字段为空，`NO_PROGRESS` 未触发。 |

两份实际 Design 请求均按归档 Context 重编，SHA-256 与各自 SQLite 调用回执匹配。第二份输出能通过历史解码进入逐案例校验。按当时发送的 JSON Schema 单独复核，`_9` 的未知请求体字段也不符合 Schema。预检按既有规则拦截，未发送请求。`_8` 在 HITL 恢复后的运行 `ac26e0c9e988b81d44a15db5d7b71998` 完成，SQLite `http_execution` 第 33–35 行均为 `EVIDENCE_COMMITTED`。工具消息可从持久化 Agent 状态核对，原始 Agent POST 正文未归档。 [时间线与回执](../.eduwork/p3b27-diagnosis-4704664/timeline.json) · [原始 Design 与 Context](../.eduwork/p3b26-acceptance-4704664/release-v5/model-outputs.jsonl)

## GROUP_RUN_LIMIT 与根因判定

`run_group` 先检查 `runs >= MAX_GROUP_RUNS`，v5 的 `2 < 3` 未命中。实际触发下一条判断 `runs >= MAX_GROUP_RUNS - 1 and not formal_code(last_code)`。这一轮整体 Design 为 `PROPOSED`，案例级诊断存在于 `summary.cases`，整体记录没有拒绝码，所以拒绝消息显示 `UNKNOWN`。这里的 `UNKNOWN` 指整体记录的诊断码为空。即使直接把 `OPENAPI_UNKNOWN_BODY_FIELD` 抄入整体记录，当前 `formal_code` 仍不会让它获得第三次准入。P1 的可信引用修复条件也没有触发。 [分组入口](../src/agenttest/product_agent.py#L692) · [形式拒绝分类](../src/agenttest/obligation_gate.py#L56)

分组视图用 `runs < MAX_GROUP_RUNS` 标注可运行，义务门禁按 `3 - 2` 得到 `attempts_left=1`，并且给出 `design:web_rule_3_negative_9`。从归档状态复放时，调用两条实际入口均被拒绝。模型调用数保持 47，业务 HTTP 保持 92。账本确认组计数在通过入口校验、准备交给 `ProductTasks.run_group` 后才增加，监督序号以及 HITL 暂停与恢复分别记录，不能作为额外 Design 尝试。 [组视图](../src/agenttest/product_agent.py#L458) · [义务派发](../src/agenttest/obligation_gate.py#L109) · [单项委派](../src/agenttest/product_agent.py#L913)

| 假设 | 判定 |
| --- | --- |
| A · 模型反复生成同一非法方案 | 第一次原文和第二次已解码方案均出现疑似用于 `_9` 请求体的 `value`，只有第二份能确定到具体字段路径并触发预检拒绝。无第三份可用于确认模型在收到字段提示后仍提交相同错误。 |
| B · 可信诊断没有进入下一次 Design | 第一次结构化诊断进入 Agent，第二份实际 Design 仅携带 Agent 的语法修复备注；JSON 随后可解析。第二次案例诊断进入 Agent 工具返回，整组与单项的后续请求均被拦在 Design 前。该批组走 legacy 负向 Design 路径，不能把实例路径的 `instance_feedback` 或 P1 引用反馈当成 `_9` 已有的请求级修复通道。 |
| C · 重复派发已完成项 | 排除。前两次设计时 `_8` 均尚未执行。它只发生一次 POST，两个配套 GET 各一次，三条回执均为首次尝试。HITL 恢复沿用审批绑定方案和回执。其后的调用没有派发。 |
| D · 剩余集合表达错误 | 集合正确，仅有 `_9`。错误在于入口与候选展示的资格判断不一致。离线单项请求只要求 `_9`，向同一请求注入已完成的 `_8` 会被精确 `required_cases` 集合校验拒绝。 |
| E · 模型收到完整字段反馈后仍生成非法 Design | 案例字段提示持久化到 Agent 状态。没有一次携带该诊断的实际后续 Design，因此无法判断模型能否据此修正。 |
| F · 批准或依赖不足 | 排除为预检阻塞原因。冻结目录与身份满足相关约束，已有归档提供可复用的合法方案形态。未来执行仍须针对该方案重新获取 HITL 批准。 |
| G · 现有上限规则正常终止 | 组入口按保留形式修复机会的现行规则正确拒绝。已用两次，第三次无准入资格。候选和门禁持续显示该路径可用是另一个缺陷，造成了额外工具往返，而案例仍然未测。 |

## 剩余单项的离线合法性

从 v5 冻结的批准项目和原始需求读取策略、身份和有效限制。使用同提交 v3 的 `negative_batch_3_case_2` 归档提案，只取 `_9`。复放走历史批组的原有批准负向约束，原分支未运行 normalized Planning IR，无伪造的 IR 或义务绑定。单项 Context 的 Schema、真实 Design 解码、DSL、Policy、匿名身份和请求预检全部通过。`validate_design_case` 生成一个用例，顺序为 GET `/parcel-observations` → POST `/parcels` → GET `/parcel-observations`。前后 GET 都使用 `observeParcelCreation / web_rule_2`，匹配同一关联标识并从 `$.count` 判断副作用。预检没有允许创建并遗留资源的生命周期，也无缺失清理步骤。历史 v3 同一用例的真实回执是 200 → 422 → 200，证明了该次运行的 `NO_SIDE_EFFECT_PROVEN`。 [预检与生命周期入口](../src/agenttest/product_design.py#L961) · [单项 Mock 回放](../.eduwork/p3b27-diagnosis-4704664/replay.json)

回放生成原有 HITL 所需的三步请求绑定和摘要。v5 归档中批准 `_8` 的清单与 `_9` 不匹配。回放的 Mock transport 若收到任何请求立即失败，观察到 0 次 HTTP。v5 的 `_9` 仍无真实状态码、无副作用观察或清理完成证明。

原有剩余策略机制会从组内减去 `executed_evidence`，并用封闭的 `required_cases` 约束 Design。一次获准组运行可推进组内多项剩余义务，这次仅需推进 1 项，最低 3 次 HTTP。v5 终态还剩 9 次模型调用、196,359 tokens 和 28 次 HTTP，原门禁的资源判断为 `AFFORDABLE`。完成方案所需的预算可用，但当时的准入规则不允许使用第三次机会。未来改动仍须保留策略归属、已测过滤、审批绑定和执行回执去重，特别是带写入的未知结果必须继续禁止自动重放。

## v2 首次 Focus 超时

`focus` 首次请求 `733260ae4fb64dd8a7c623db68bf291f`，北京时间 17:38:50.127 发起，17:39:50.190 结束。冻结配置为 60 秒，调用账本记录 60,063 ms、`DEADLINE / MODEL_TIMEOUT`，无 HTTP 状态、服务端请求 ID、响应 ID 或可信 token 用量，也无后续对账回执。运行依照现行规则保留 `UNKNOWN`、未重试、0 次业务 HTTP、终态 `NEEDS_REVIEW`。证据无法判明请求是否到达服务端或区分服务、网络与其他等待来源。v2 仍保留在五轮分母内。 [v2 归档清单](../.eduwork/p3b26-acceptance-4704664/release-v2/manifest.json) · [账本摘录](../.eduwork/p3b27-diagnosis-4704664/timeline.json)

## 下一轮最小开发建议

1. 让分组视图、义务门禁和实际派发共享准入判断。保留未通过义务和确切阻塞原因，避免持续推荐已确定不可派发的入口。仅修这一点会减少无效循环，v5 同类情况仍需单项修复通道才能推进。
2. 在已有上限内考虑一条窄修复路径。只处理批准批组中已有可信预检拒绝、尚无派发回执且可纠正的剩余项，先覆盖 `OPENAPI_UNKNOWN_BODY_FIELD`。把经过投影的代码、用例与步骤标识、字段路径、允许字段和应用生成的提示带到实际 legacy Design 请求。继续用已测集相减与封闭 `required_cases`，采用可信修复时消耗现有一次 `runtime_repair`；第三次 Design 仍须通过原有预算和逐项 HITL。风险在于把语义分歧误当形式修复、让旧写入重新出现或把未经审核的值带入模型。应以实际编码请求、无新审批不派发、已测项无法进入修复输入、耗尽后双入口一致拒绝进行定向验证。

无证据支持增加 `MAX_GROUP_RUNS`、修复次数或预算，v2 无证据支持变更超时。原有 4/5 门槛、v1/v3/v4 达标、v2/v5 未达标及 P3-B2.3 至 P3-B2.6 历史 NO-GO 均保持。

## 验证与证据索引

- 原评分器在只读归档上复算五轮，原义务与请求级覆盖、完成状态、逐轮达标和批次结论一致。真实 503 的 `ASSERTION_FAILED` 仍保留，未补记 `_9`。
- 义务门禁、案例诊断、负向 POST 观察及 HITL 四个相关文件，**58 passed**。Ruff 和 `git diff --check` 通过。真实模型与真实业务 HTTP 均为 0，未执行完整回归。
- 四批历史验收的 1,266 个文件和两个历史评分脚本摘要一致。诊断开始时已有的 P3-B2.6 报告零变化，`progress.md` 仅前置本阶段记录。 [文件摘要核对](../.eduwork/p3b27-diagnosis-4704664/integrity.json)
- 原始 v5 证据包括 [manifest](../.eduwork/p3b26-acceptance-4704664/release-v5/manifest.json)、[HITL 暂停](../.eduwork/p3b26-acceptance-4704664/release-v5/phase-03.json)、[恢复后阶段](../.eduwork/p3b26-acceptance-4704664/release-v5/phase-04.json)、[终态报告](../.eduwork/p3b26-acceptance-4704664/release-v5/phase-08.json)、[SQLite 账本](../.eduwork/p3b26-acceptance-4704664/release-v5/lab/workspace/tasks.sqlite3) 和 [Checkpoint](../.eduwork/p3b26-acceptance-4704664/release-v5/lab/workspace/tasks.checkpoints.sqlite3)。诊断产物位于独立的 `.eduwork/p3b27-diagnosis-4704664/`。

## 2026-11-23 · 分组准入与剩余单项修复

原准入矛盾来自 `AgentSession._group_view`、义务候选及 `dispatch_plan` 只看数量，而 `run_group` 对第三次另查整体诊断。现在 `AgentSession._group_admission` 统一判断组状态、次数和可用修复资格。组视图分别报告剩余义务、名义次数、`design_blocker` 和剩余项修复资格。候选在截取列表前去掉不可派发组及已测批组策略。义务门禁和整组入口使用同一判断，单项候选仍委派给所属批组。执行会重新核对当前授权和预算，模型看到的候选不替代 HITL。 [Agent 准入](../src/agenttest/product_agent.py) · [义务门禁](../src/agenttest/obligation_gate.py) · [候选生成](../src/agenttest/product_obligations.py)

| 状态 | 原行为 | 修复后 |
| --- | --- | --- |
| 两次已用，第三次无合格修复 | 视图和义务门禁给出下一步，实际两条入口均拒绝 | 保留未测义务和名义余量，显示 `GROUP_RUN_LIMIT` 并撤下下一步。门禁不再为该组续派；无其他候选时如实结束 |
| 两次已用，有同轮可验证的未派发案例修复 | 最近整体 Design 为 `PROPOSED`，实际入口拒绝 | 整组和单项指向同一个第三次机会，实际 Design 只接收剩余策略 |
| 修复额度耗尽或第四次运行 | 已有上限 | 两个入口和展示均遵守原有 `MAX_GROUP_RUNS=3`、`MAX_RUNTIME_REPAIRS=1` |

### 第三次准入与审批

范围限于同一已批准负向批组仅剩一个 `required_missing` 请求体策略的情形。诊断必须为同一案例、步骤和策略的 `OPENAPI_UNKNOWN_BODY_FIELD`，并有可定位的参数下标。可信预检还要见证一个同操作、同身份、同批准基线的兄弟草稿可承载剩余策略，再用原 `validate_design_case` 验证其合法性。候选准入核对该兄弟在同一次 Design 中已有执行回执，剩余项无运行 ID、业务请求或执行证据，且当前仍获授权、没有未解决写入或清理。见证方案仅在本地预检，运行时不会把它注入 Design 输出或派发它。`OPENAPI_UNKNOWN_BODY_FIELD` 没有加入全局形式错误集合。 [预检见证](../src/agenttest/negative_batch_repair.py) · [案例回执](../src/agenttest/product_tasks.py)

下一次实际编码的 Design 请求带一个 `negative_repair` 对象，含 `obligation_id`、`operation_id`、`policy_id`、身份、步骤、字段路径、已批准字段、基线必填集合和原始变异。已知 DSL 槽名 `value` 可明示。其他模型自拟未知名以字段路径定位，原名及草稿正文值不会传入。可信约束替代冲突的 Agent 备注。以 v5 同形样本为例，模型提交符合 Schema 的基线，执行器按原批准策略在实际 POST 中移除 `meta` 并检查 422。修复提案限制为单个原身份检查，仍走 Design 解码、原有预检、独立 HITL 和副作用观察。 [Design 请求](../src/agenttest/product_design.py) · [离线编码与执行测试](../tests/test_p3b27_negative_batch_repair.py)

夹具捕获三份真实编码的 Model 请求，第三份只要求 `_9`，修复计数为 1。合法提案在进程内发送同关联键的 GET → POST → GET，`_8` 和 `_9` 各有一次 POST。非法提案仍被拒，已测策略未重复派发。计数在已证实没有发出 Design 时不会记作使用修复。缓存的审批恢复仍重新核对预检计划摘要和原审批，不调用模型。预算、清理、安全、未知写入、`NO_PROGRESS` 和原评分标准保留。

该资格依赖同轮可信预检证据。旧归档没有新增见证，历史 v5 仍未测，原批 **3/5、NO-GO**；v2 的服务超时来源仍无法从现有证据确认。没有可验证兄弟基线、多个待测或尚有未知写入的情况继续如实阻塞。离线复放无法验证真实模型会如何利用反馈。

### 开发验证结果

- 最终改动分两组验证，共 **265 passed**。Agent、义务门禁、P3-B2.6 P0/P1 与本轮 17 项新增测试共 96 项通过。Design、ProductTasks、预算、脱敏、清理、HITL 与选定的批准恢复和进程退出恢复共 169 项通过。此前包含完整进程退出恢复文件的定向回归为 280 项通过，与最后两组有重叠。 [Agent 定向日志](../.eduwork/p3b27-diagnosis-4704664/development-final-agent.log) · [安全与恢复日志](../.eduwork/p3b27-diagnosis-4704664/development-final-safety.log) · [较广定向日志](../.eduwork/p3b27-diagnosis-4704664/development-pytest.log)
- Ruff、`git diff --check` 通过。真实模型和真实业务靶场调用为 0。新增测试使用 Mock 和进程内靶场，进程退出恢复回归使用测试专用回环服务。四批历史验收的 1,266 个文件及既有 P3-B2.6 报告摘要一致。

```powershell
& .\.venv\Scripts\python.exe -X utf8 -B -m pytest -q tests/test_p3b27_negative_batch_repair.py
```

## 2026-11-23 · 完整离线冻结回归

**离线 PASS**。在 `master` 的候选工作区执行一次默认完整测试集。`HEAD` 与本地 `origin/master` 同为 `47046640fcd7912dd51f5c8a6b8c67fb29e51544`。项目 `.venv` 使用 Python 3.11.15、pytest 9.1.1，冻结前 Windows Git 环境恢复探针通过，无并行回归或验收进程。既有测试网络保护保留，临时回环恢复样例只使用测试专用服务。 [启动环境](../.eduwork/p3b27-freeze/run-20261123T115951Z/environment-before.json) · [实际命令](../.eduwork/p3b27-freeze/run-20261123T115951Z/launch.json)

| 项目 | 结果 |
| --- | --- |
| pytest | 退出码 0，3,049 passed、0 failed、0 errors、15 skipped、1 xfailed |
| 收集与报告 | 3,065 / 3,065，`missing`、`extra`、`repeated`、`nonunique` 均为 0 |
| 新增与兼容性 | P3-B2.7 新增 17/17，P3-B2.6 P0 14/14、P1 5/5。上一轮 3,048 项的 ID 与终态完全一致 |
| Windows Git | 环境恢复测试通过，`test_nl_usage.py` 5/5，首次 P3-B2.6 冻结中失利的原 46 项全部通过 |
| 耗时 | pytest 44 分 19.00 秒，含冻结清单和取证的总耗时 44 分 34.09 秒（北京时间 19:59:51–20:44:25） |
| 最慢测试 | `test_pocketbase_acceptance.py::test_full_denominator_is_cold_regraded_and_resealed_wrong_results_fail`，99.72 秒。其次是 `test_generic_arguments.py::test_new_tasks_archive_rescores_and_rejects_resealed_regrading`（66.62 秒）和 `test_generic_references.py::test_new_reference_tasks_use_shared_runner_and_cold_regrading`（66.45 秒） |

pytest 保留一条既有的 `TestDesign` 收集警告，来自测试模块导入带构造函数的产品模型类。上一轮的 15 个 skip 和 1 个 xfail 的测试 ID 与终态全部一致。 [pytest 日志及最慢 20 项](../.eduwork/p3b27-freeze/run-20261123T115951Z/regression/pytest.log) · [逐项终态](../.eduwork/p3b27-freeze/run-20261123T115951Z/regression/tests.json)

分组与单项委派 71 项、义务门禁与终止 53 项、预算 23 项、Design 与预检 113 项全部通过。HITL 相关 12 项、清理 33 项、恢复与写入防重放 33 项、模型输入脱敏 31 项全部通过。分类之间有重叠。真实编码的 Design Mock 用例仍验证可信诊断传递、第三次准入、既有修复次数、已测项排除和独立 HITL。 [回归分类与放行条件](../.eduwork/p3b27-freeze/run-20261123T115951Z/audit-summary.json)

冻结前后 **1,718 个 Git 可见文件零漂移**。清单包含 363 个源码和脚本文件、225 个测试文件、233 个配置文件、825 个文档文件和 72 个其他文件，另列实际 Git 工作区状态及 3 个未跟踪候选文件。checkout SHA-256 前后一致，为 `2c591aa40840d725d77dc2f251fa3b2ce63a2becd25c13ff4d76e6bf3b5ee98b`。历史验收与旧冻结归档摘要一致，第一次 P3-B2.6 离线 FAIL 及 P3-B2.3 至 P3-B2.6 历史真实验收 NO-GO 均保留。冻结结束后仅更新本报告和进度记录。 [冻结前清单](../.eduwork/p3b27-freeze/run-20261123T115951Z/identity-before.json) · [冻结后清单](../.eduwork/p3b27-freeze/run-20261123T115951Z/identity-after.json)

Ruff 和 `git diff --check` 通过。本地离线证据满足进入提交与 CI 核查的条件。本轮停在冻结回归和审计，未提交、推送、调用真实模型、访问真实业务靶场或进入 P3-B3。

## 2026-11-23 · 五轮真实稳定性验收

**NO-GO，1/5 达标，门槛为 4/5。** v1 达到原完整评分。v2、v3、v4 各有一次已确认的模型超时，v5 有一次缺少响应和错误分类的 UNKNOWN。四轮都按现行门禁结束，尚有批准义务待执行。原评分器未变，五轮全部保留。 [原口径评分与完整审计](../.eduwork/p3b27-acceptance-f3733c1/audit.json)

### 冻结边界与健康探针

候选 `f3733c14c5da6b33cfc7a9a70812566f72dd7c85` 开始前在本地 HEAD、`origin/master` 和 GitHub `master` 一致，工作区干净。运行前后 checkout SHA-256 同为 `09f116e204a46476ef0293ef6a400e054c405c383df5bba7d0ab393ca2c98a96`，源码与启动脚本 SHA-256 同为 `41a4a08f88ee9dbb42f5b9894ad31820cc708a8125f657eec950f48756949854`。离线冻结 3,049 passed、15 skipped、1 xfailed，准确提交的 Python 5/5 和 Web 2/2。 [Python CI](https://github.com/mofengyan595/agenttest/actions/runs/45178445552) · [Web CI](https://github.com/mofengyan595/agenttest/actions/runs/45178445448) · [冻结完整性](../.eduwork/p3b27-acceptance-f3733c1/integrity.json)

模型配置 SHA-256 仍为 `cecf6dff2dccf853e1da5327457631243d9d9271f0575d1c6a823261aca52172`。五个任务和靶场实例均独立，批准目录与上批同位置逐项对账一致。沿用原始发布目标、503 注入、60 秒模型超时配置、64 次模型调用、650,000 tokens、120 次业务 HTTP 和 900 秒任务上限。每组至多 3 次，可信修复至多 1 次。北京时间 21:31:31 至 22:12:30，共 2,459.328 秒。 [批次配置与状态](../.eduwork/p3b27-acceptance-f3733c1/batch.jsonl) · [位置与安全门禁](../.eduwork/p3b27-acceptance-f3733c1/positions.jsonl)

| 探针 | 终态 / 用量 | 耗时 | tokens | 服务端响应 ID |
| --- | --- | ---: | ---: | --- |
| 1 | READY / KNOWN | 1.313 秒 | 147 | `ca964ab8-63e7-4089-ac36-2bf21bbd39f0` |
| 2 | READY / KNOWN | 0.531 秒 | 65 | `7233ab00cfd54304bd2d3d8127e5f8ce` |
| 3 | READY / KNOWN | 1.062 秒 | 147 | `5aafce84-46a9-41b9-adf9-2e14c6c4c1a1` |

三次 HTTP 200 响应均可解析，网关未提供独立请求 ID。359 个探针 tokens 单独计量。 [探针请求与账本](../.eduwork/p3b27-acceptance-f3733c1/health.jsonl)

### 五轮原评分与安全证据

| 轮次 | 批准义务 | 负向策略 | 请求级 | 终止状态 / 原因 | strict_complete / complete / checks_passed | 模型调用 / 已知 tokens / UNKNOWN | 业务 HTTP | 耗时（秒） | 原评分 |
| --- | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: | --- |
| v1 | 31/31 | 24/24 | 24/24 | COMPLETE / OBLIGATIONS_COMPLETE / ANSWER | false / true / false | 27 / 227,518 / 0 | 94 | 490.875 | 达标 |
| v2 | 21/31 | 14/24 | 14/24 | BLOCKED / MODEL_SERVICE_UNAVAILABLE / MODEL_ERROR | false / false / false | 27 / 217,973 / 1 | 54 | 389.641 | 未达标 |
| v3 | 21/31 | 14/24 | 14/24 | BLOCKED / MODEL_SERVICE_UNAVAILABLE / MODEL_ERROR | false / false / false | 16 / 119,850 / 1 | 54 | 402.297 | 未达标 |
| v4 | 23/31 | 16/24 | 16/24 | BLOCKED / MODEL_SERVICE_UNAVAILABLE / MODEL_ERROR | false / false / false | 28 / 234,648 / 1 | 66 | 502.718 | 未达标 |
| v5 | 17/31 | 10/24 | 10/24 | BLOCKED / MODEL_SERVICE_UNAVAILABLE / MODEL_ERROR | false / false / false | 18 / 142,743 / 1 | 38 | 595.203 | 未达标 |

| 轮次 | HITL 批准记录 / POST 与审批绑定 | 实际创建 / 删除 / 同身份 404 | 实际 503 / 503 导致的 ASSERTION_FAILED |
| --- | ---: | ---: | ---: |
| v1 | 9 / 25 | 15 / 15 / 15 | 2 / 1 |
| v2 | 6 / 15 | 5 / 5 / 5 | 2 / 1 |
| v3 | 5 / 15 | 5 / 5 / 5 | 2 / 1 |
| v4 | 6 / 17 | 11 / 11 / 11 | 2 / 1 |
| v5 | 4 / 11 | 1 / 1 / 1 | 2 / 1 |

全部 306 条业务事件的身份与顺序和 SQLite 回执一致。83 条 POST 匹配已批准的工具调用、checkpoint、操作及授权。37 次实际创建均由逐资源的相同身份 DELETE 后 404 证明清理。零越权请求、重复负向策略或 HTTP attempt、未知写入、写入重放、缺回读或待复核写入。五轮分别保留一个 `readAvailability` 主用例的 503 断言失败。v1 满足原完整评分，并保留 `checks_passed=false`、`strict_complete=false`。

本批四条 UNKNOWN 均未调和、未豁免，也未填为 0。

| 轮次 | 模型请求与本地调用 ID | 原始账本证据 |
| --- | --- | --- |
| v2 | Design `negative_batch_5` 第 1 次，`b20ea1f7ddeb4c3e8649566f51d799b9` | `DEADLINE / MODEL_TIMEOUT`，60.062 秒。无 HTTP 状态或服务端响应 ID |
| v3 | Agent 第 10 次，`900d170e99384ae294cb2b2b3615c68c` | `READ / MODEL_TIMEOUT`，122.938 秒。此路径采用 HTTPX 读超时，60 秒是原超时配置，账本值是整条调用耗时。无 HTTP 状态或服务端响应 ID |
| v4 | Design `negative_batch_6` 第 1 次，`f98cc31b1b5f411b9a170e1ecd6cfad1` | `DEADLINE / MODEL_TIMEOUT`，60.062 秒。无 HTTP 状态或服务端响应 ID |
| v5 | Design `negative_batch_4` 第 1 次，`fa75b056a6b14e19a393e5842a7c81d6` | UNKNOWN，账本耗时 318.656 秒，无可核对的响应、错误分类或超时类型。外层记录 `MODEL_USAGE_UNKNOWN`，未计入已确认超时 |

上述证据能定位失败的调用阶段，无法进一步区分服务端与网络等待的来源。v2/v3/v4/v5 分别保留 10、10、8、14 项未测义务。

### P3-B2.7 实际触发与 P0/P1 兼容性

- `OPENAPI_UNKNOWN_BODY_FIELD` 出现 10 次，分布为 v1 三次、v2 三次、v4 四次。v2 的 `negative_batch_2` 为 `_negative_5` 留下一项合法基线见证，与同一次设计已执行的 `_negative_4` 对应。`_negative_5` 在同组第二次普通 Design 后实际执行一次，未使用新增第三次机会。五轮实际编码请求中 `negative_repair` 为 0，`runtime_repair` 为 0。**P3-B2.7 第三次可信修复路径未触发，真实传递率与修复成功率尚无样本。**
- 五轮实际同组最大 Design 次数分别为 2、2、1、3、3。v4 `negative_batch_5` 与 v5 `negative_batch_1` 在第二次出现 JSON 语法拒绝后实际发送了第三次 Design，沿用既有形式修复资格。`GROUP_RUN_LIMIT` 为 0。六次可读 scope 快照累计 48 条分组行，未发现已阻塞分组仍被推荐。现场未尝试第四次，各组均未越过上限。
- 五轮各执行一次 `createParcel / web_rule_3_negative_9`。五条实际请求的审计投影均确认 `body.meta` 缺失，按原批准 `required_missing / OPENAPI_BODY_FIELD_REQUIRED` 变异收到 422。每条 POST 有原绑定审批，本批没有通过额外写入来完成此策略。
- P0 在 v2、v5 各触发一次 `FOCUS_REQUESTED_CHECKS_MISSING`。下一次实际 Focus 请求的摘要均匹配账本，缺少的 24 项均补齐。v1、v3 首次提交完整映射。v4 首次提交 24 项但因 `FOCUS_INPUT_TEXT_CHANGED` 被拒，第二次合法接纳 24 项。五轮皆持久化 24 条批准范围内的显式映射，实测覆盖分别为 24、14、14、16、10 /24。
- 未出现 `CONSTRUCTION_REFERENCE_INVALID / SOURCE_UNKNOWN`，P1 引用反馈、合法生产者与选择器的真实修复路径未触发。51 条 Focus/Design 账本记录中，50 条有可重编码的请求并与账本摘要匹配，v5 的 UNKNOWN 缺完整请求上下文。已对账的 50 条请求和 15 条工具诊断中未发现凭据或禁止字段。原始 Agent 请求正文未归档。 [P0/P1 与请求对账](../.eduwork/p3b27-acceptance-f3733c1/audit-p0-p1.json) · [P3-B2.7、清理身份与实际变异](../.eduwork/p3b27-acceptance-f3733c1/audit-p3b27.json)

### 三批对比

| 观测口径 | P3-B2.5 | P3-B2.6 | P3-B2.7 |
| --- | ---: | ---: | ---: |
| 原完整评分达标 | 2/5，NO-GO | 3/5，NO-GO | 1/5，NO-GO |
| 批准义务 31/31 | 4/5 | 3/5 | 1/5 |
| 请求级实测 24/24 | 2/5 | 3/5 | 1/5 |
| 已确认模型超时 / UNKNOWN | 0 / 0 | 1 / 1 | 3 / 4 |
| Design 拒绝 / 案例预检拒绝 | 4 / 0 | 10 / 4 | 12 / 10 |
| `GROUP_RUN_LIMIT` 工具拒绝 | 0 | 8 | 0 |
| `NO_PROGRESS` / `OBLIGATION_STALLED` 终止 | 1 / 0 | 0 / 1 | 0 / 0 |
| HITL 批准记录 / 匹配 POST | 37 / 115 | 33 / 99 | 30 / 83 |
| 创建 / 同身份删除后 404 | 65 / 65 | 60 / 60 | 37 / 37 |
| 模型调用 / 已知 tokens / 业务 HTTP | 112 / 964,560 / 430 | 140 / 1,193,265 / 376 | 116 / 942,732 / 306 |

Design 拒绝按调用阶段计，Focus 拒绝和案例预检分开统计。P3-B2.7 多轮因模型用量证据缺失提前终止，吞吐与开销差异只能作为本批观测。没有现场样本证明新增第三次修复已改善成功率。原 P3-B2.3 至 P3-B2.6 的结论和档案均保留。此次未补跑、未修改代码、未提交推送，P3-B3 尚未启动。 [同口径复算](../.eduwork/p3b27-acceptance-f3733c1/comparison-verified.json)

每个轮次目录保存 `manifest.json`、`model-outputs.jsonl`、各阶段报告、`target-events.jsonl`、`lab/workspace/tasks.sqlite3` 和 `lab/workspace/tasks.checkpoints.sqlite3`。 [v1](../.eduwork/p3b27-acceptance-f3733c1/release-v1/manifest.json) · [v2](../.eduwork/p3b27-acceptance-f3733c1/release-v2/manifest.json) · [v3](../.eduwork/p3b27-acceptance-f3733c1/release-v3/manifest.json) · [v4](../.eduwork/p3b27-acceptance-f3733c1/release-v4/manifest.json) · [v5](../.eduwork/p3b27-acceptance-f3733c1/release-v5/manifest.json) · [批次启动日志](../.eduwork/p3b27-acceptance-f3733c1/launcher.log)

## 2026-11-23 · 第二批真实稳定性复验

**第二批独立评分为 1/5、NO-GO，第一批仍为 1/5、NO-GO。** 第二批 v1 达标，v2、v3、v4 在 Focus 验收失败后进入 `NEEDS_REVIEW`，v5 因 Design 超时停止。超时从第一批的 3 次降至 1 次，UNKNOWN 从 4 次降至 1 次。本批的三个 Focus 失败都有 HTTP 200 和明确用量。 [第二批原评分](../.eduwork/p3b27-retest-f3733c1/audit.json)

### 冻结与探针

开始前，本地 HEAD、`origin/master` 与 GitHub `master` 均为 `f3733c14c5da6b33cfc7a9a70812566f72dd7c85`，准确提交的 CI 7/7 仍为 success。与第一批相比只有两份报告追加，已提交候选重算 SHA-256 仍为 `09f116e204a46476ef0293ef6a400e054c405c383df5bba7d0ab393ca2c98a96`。含首次报告追加的实际工作区摘要在第二批运行前后均为 `595888e770b13f71038b88c554d32e36d9775fc8de60877e5c02d2dc57772ee8`。源码与启动脚本摘要仍为 `41a4a08f88ee9dbb42f5b9894ad31820cc708a8125f657eec950f48756949854`，公开模型配置摘要仍为 `cecf6dff2dccf853e1da5327457631243d9d9271f0575d1c6a823261aca52172`。 [第二批冻结记录](../.eduwork/p3b27-retest-f3733c1/batch.jsonl)

未发现并行验收。复用第一批探针、五轮执行脚本、安全门禁及冻结评分器，原目标、60 秒超时配置、预算、批准目录、故障注入、HITL 与恢复规则一致。十个任务和靶场实例跨两批各自独立，第一批 324 个证据文件摘要一致。第二批北京时间 22:32:15 至 22:54:22，共 1,327.531 秒。 [两批独立性与证据核对](../.eduwork/p3b27-retest-f3733c1/audit-integrity.json)

| 探针 | 状态 / 用量 | 耗时 | tokens | 服务端响应 ID |
| --- | --- | ---: | ---: | --- |
| 1 | READY / KNOWN | 1.704 秒 | 147 | `037b0d55-f3dd-44da-b91f-9d66a732181c` |
| 2 | READY / KNOWN | 1.532 秒 | 147 | `560822e0-ca24-45f6-9187-120351932f83` |
| 3 | READY / KNOWN | 1.343 秒 | 65 | `e1b10261421e48848bad48bf77af6900` |

三个响应均为可解析的 HTTP 200，网关未提供独立请求 ID。探针 359 tokens 与正式验收分开计量。 [探针记录](../.eduwork/p3b27-retest-f3733c1/health.jsonl)

### 五轮原评分

| 轮次 | 批准义务 | 负向策略 | 请求级 | 终态 | 原评分 | 模型调用 | 已知 tokens | UNKNOWN | HTTP |
| --- | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: |
| v1 | 31/31 | 24/24 | 24/24 | COMPLETE / OBLIGATIONS_COMPLETE | 达标 | 32 | 268,285 | 0 | 94 |
| v2 | 0/31 | 0/24 | 0/0 | NEEDS_REVIEW / Focus 缺 1 项映射 | 未达标 | 2 | 30,204 | 0 | 0 |
| v3 | 0/31 | 0/24 | 0/0 | NEEDS_REVIEW / Focus 缺 24 项映射 | 未达标 | 2 | 28,338 | 0 | 0 |
| v4 | 0/31 | 0/24 | 0/0 | NEEDS_REVIEW / Focus 缺 23 项映射 | 未达标 | 2 | 28,783 | 0 | 0 |
| v5 | 19/31 | 12/24 | 12/24 | BLOCKED / MODEL_SERVICE_UNAVAILABLE | 未达标 | 17 | 131,238 | 1 | 48 |

v2–v4 的 31 项分母来自固定批准目录，实际派发为 0，产品未生成终止义务账本和 `strict_complete`。请求级按原始 0/0 保留。v1 `complete=true`，`strict_complete=false`；v5 两项均为 false。v1、v5 各实际收到 2 次 503，其中各有 1 次主用例 `ASSERTION_FAILED`，`checks_passed=false`。v2–v4 无业务请求或断言结果。 [五轮位置与终态](../.eduwork/p3b27-retest-f3733c1/positions.jsonl)

v1 有 9 条批准，25 条 POST 均匹配；15 次创建均有同身份删除后的 404。v5 有 5 条批准，13 条 POST 均匹配；5 次创建均有同身份删除后的 404。其余轮次没有写入或资源需要清理。142 条业务 HTTP 的顺序和身份与回执一致，零越权、重复负向派发、未知写入或重放，零缺失回读。 [审批与证据核对](../.eduwork/p3b27-retest-f3733c1/audit.json)

### 模型调用可靠性

| 轮次 | Focus / Design / Agent 调用 | 确认超时阶段 |
| --- | ---: | --- |
| v1 | 1 / 12 / 19 | 无 |
| v2 | 2 / 0 / 0 | 无，两个 Focus 请求均 HTTP 200 |
| v3 | 2 / 0 / 0 | 无，两个 Focus 请求均 HTTP 200 |
| v4 | 2 / 0 / 0 | 无，两个 Focus 请求均 HTTP 200 |
| v5 | 2 / 6 / 9 | Design `negative_batch_5` 第 1 次 |

第二批 55 次模型调用中，54 次 HTTP 200 且用量 KNOWN，计 486,848 tokens。Focus、Design、Agent 的请求字节数中位数分别为 47,360、34,079、30,235，耗时中位数分别为 13.406、13.961、1.531 秒。每次调用的请求大小、耗时、HTTP 状态、响应 ID、阶段和用量均单独保留。 [两批逐调用账本审计](../.eduwork/p3b27-retest-f3733c1/model-reliability-audit.json)

| 批次与轮次 | 调用位置 | 请求大小（字节） | 账本耗时 | 状态 |
| --- | --- | ---: | ---: | --- |
| 第一批 v2 | 第 27 次调用，Design `negative_batch_5` 第 1 次 | 39,301 | 60.062 秒 | DEADLINE / UNKNOWN |
| 第一批 v3 | 第 16 次调用，Agent 第 10 次 | 46,219 | 122.938 秒 | READ / UNKNOWN |
| 第一批 v4 | 第 28 次调用，Design `negative_batch_6` 第 1 次 | 38,500 | 60.062 秒 | DEADLINE / UNKNOWN |
| 第一批 v5 | 第 18 次调用，Design `negative_batch_4` 第 1 次 | 35,375 | 318.656 秒 | 原因未定 / UNKNOWN |
| 第二批 v5 | 第 17 次调用，Design `negative_batch_5` 第 1 次 | 38,456 | 60.078 秒 | DEADLINE / UNKNOWN |

上述五条均无 HTTP 状态或服务端请求、响应 ID。`negative_batch_5` 在两批均是该组首个 Design 超时，请求大小相差 845 字节，任务内调用序号分别为 27 和 17。第二批 v5 的上一条 Design（`negative_batch_4`）为 35,375 字节，42.500 秒后收到 HTTP 200 且用量 KNOWN；字节数与第一批未归因调用相同，请求摘要不同。第二批 28 次 Agent 调用未复现 READ 超时，第一批有 65 次 Agent 调用。仅凭这两批无法定位服务端、网络或 Harness 计时环节。

### Focus、引用与剩余义务修复

- P0 缺项诊断出现 6 次，涉及 4 轮。v2、v4、v5 的诊断都经请求摘要核实传入下一次实际 Focus。v2 从缺 24 项变为缺 1 项；v4 从缺 1 项变为缺 23 项；v5 从缺 10 项补到完整 24 项。v3 首次提交了 24 条原始映射，但因来源子句缺失被拒；第二次缺 24 项，按原修复流程停止。v1 首次即接受 24 项。只有 v1、v5 有持久化映射，均在批准范围内。
- **P1 在 v1 真实触发并完成修复。** `negative_batch_7` 第一次 Design 的 `SOURCE_UNKNOWN` 指向未在用例内声明的创建步骤。第二次实际编码请求保留了可信诊断、批准生产者 `createParcel / web_rule_3` 及 `parcelId ← $.id`，请求摘要匹配账本。模型在两个用例各自的 `before` 声明了合法生产者，并完成该批义务。该轮仅记一次 `runtime_repair`。
- **P3-B2.7 新增第三次可信修复未触发。** v5 的两个 `OPENAPI_UNKNOWN_BODY_FIELD` 拒绝为 `_negative_5`、`_negative_9` 生成了基线见证。两组各只有一次 Design，任务随后在另一组超时停止。未发送 `negative_repair` 请求。`web_rule_3_negative_9` 仅在 v1 实际执行，省略 `body.meta` 后收到已批准的 422；v5 仍未覆盖。五轮无 `GROUP_RUN_LIMIT` 或第四次组运行，所见候选与准入记录未出现冲突。
- 27 条 Focus/Design 编码请求均与调用账本对齐，七条带诊断的工具消息通过脱敏核查，未发现凭据或禁止字段。 [P0/P1 专项](../.eduwork/p3b27-retest-f3733c1/audit-p0-p1.json) · [剩余义务、身份及清理专项](../.eduwork/p3b27-retest-f3733c1/audit-p3b27.json)

### 两批比较与后续判断

| 同口径指标 | 第一批 | 第二批 |
| --- | ---: | ---: |
| 原评分 / 31/31 / 请求级 24/24 | 1/5 · 1/5 · 1/5 | 1/5 · 1/5 · 1/5 |
| 确认超时 / UNKNOWN | 3 / 4 | 1 / 1 |
| Focus / Design / 案例预检拒绝 | 3 / 12 / 10 | 7 / 4 / 4 |
| `GROUP_RUN_LIMIT` / `NO_PROGRESS` / `OBLIGATION_STALLED` | 0 / 0 / 0 | 0 / 0 / 0 |
| P0 缺项轮次 / 补齐轮次 | 2 / 2 | 4 / 1 |
| P1 执行修复 / P3-B2.7 新路径触发 | 0 / 0 | 1 / 0 |
| HITL 批准 / 匹配 POST / 同身份清理 404 | 30 / 83 / 37 | 14 / 38 / 20 |
| 模型调用 / 已知 tokens / 业务 HTTP | 116 / 942,732 / 306 | 55 / 486,848 / 142 |

第二批再次出现相似输入规模的 `negative_batch_5` Design 超时，说明第一批的超时现象可复现。另三轮有完整的服务响应，直接阻塞是 Focus 提案未通过来源或映射校验。两批候选相同，较少调用与较早停止有关。当前证据不足以把超时归因到网关波动或 Harness 缺陷。建议 P3-B2.8 先做只读诊断，按调用 ID 和时间窗核对 `negative_batch_5` 超时及第一批无分类 UNKNOWN，再独立检查 Focus 修复后遗失已声明映射的过程。现有超时、重试和评分门槛继续保留。 [第二批同口径汇总](../.eduwork/p3b27-retest-f3733c1/comparison.json)

两批各自保留 1/5、NO-GO，历史 P3-B2.3 至 P3-B2.6 原记录完整。第二批的 manifest、模型输出、Trace、HTTP 回执、SQLite 和 checkpoint 保存在同名轮次目录。本轮仅追加报告，未补跑、提交推送或进入 P3-B3。 [v1](../.eduwork/p3b27-retest-f3733c1/release-v1/manifest.json) · [v2](../.eduwork/p3b27-retest-f3733c1/release-v2/manifest.json) · [v3](../.eduwork/p3b27-retest-f3733c1/release-v3/manifest.json) · [v4](../.eduwork/p3b27-retest-f3733c1/release-v4/manifest.json) · [v5](../.eduwork/p3b27-retest-f3733c1/release-v5/manifest.json)
