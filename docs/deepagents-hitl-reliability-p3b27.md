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
