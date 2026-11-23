# P3-B2.6 请求级覆盖与 NO_PROGRESS 诊断

2026-11-23 · 冻结实现 `ae61d8ad12bb9aa32f6a96c9456031eb46fe44f8` · 只读开发诊断

**P3-B2.5 原评分仍为 2/5，NO-GO。** v2、v5 确实执行了全部 24 条批准负向策略，但 Focus 原始响应就没有请求级负向映射。v1 的三个提案都引用了当前用例未声明的资源生产者。A3 的空候选对提交的方案是正确的，现有授权允许补上生产者。两次重新提案没有把上一轮的结构化诊断带入 Design 请求，三次动作无进展后触发了现有停止规则。

## 1. P0 · v2/v5 的 0/0 从哪里来

五轮 manifest 都记录了同一个原始任务「发版前请完整验收整个项目，正常路径和非法入参都按已批准的规则执行。」解析将整项目发版主句记作 `request_1`，将常规和负向范围限定记作 `request_2`。后者属于服务端识别的 `trusted_scope_clauses`，所以模型的 `requirement_map` 应只填 `request_1`。本批五轮都遵守了这个键约束，服务端均将 `request_2` 留在归一化需求中。原请求和已批准的 24 条负向策略均存在。参见 [范围限定识别](../src/agenttest/task_completion.py#L192) 和 [子句解码](../src/agenttest/request_requirements.py#L630)。

| 轮 | 原始 `scope.operations[].requested_checks`（创建 / 修改 / 替换） | `request_1.response_checks` | 归一化的负向来源 | 已批准负向实测 | 旧评分请求级 |
| --- | --- | ---: | --- | ---: | ---: |
| v1 | 10 / 4 / 10 | 0 | 24 条引用 `source_2`，整项目发版主句 | 14/24 | 14/24 |
| v2 | 0 / 0 / 0 | 18 | 无 `RequestedCheck` | 24/24 | 0/0 |
| v3 | 10 / 4 / 10 | 0 | 24 条引用 `source_3`，含「正常路径和非法入参」的限定句 | 24/24 | 24/24 |
| v4 | 10 / 4 / 10 | 18 | 同 v3 | 24/24 | 24/24 |
| v5 | 0 / 0 / 0 | 18 | 无 `RequestedCheck` | 24/24 | 0/0 |

v2、v4、v5 的 18 个 `response_checks` 都能在 Focus 输入的已批准响应断言目录找到。按接口是 availability 2 项，create/read/change/replace 各 4 项，属于带选择器和期望值的响应体断言。v4 同时给出了这 18 个引用和全部 24 条负向映射。响应断言与负向输入映射是两条并行证据，不能互相抵数。

1. **分母首次为零的地方是模型原始输出。** v2、v5 选择模式均为 `selected`，六个操作的 `requested_checks` 都是空数组。以每轮归档的原始 JSON 和原始 context 调用当前冻结的 `TestFocusModel._decode_output`，复现五轮归一化数量 **24 / 0 / 24 / 24 / 0**，逐项等于归档的 Focus 输出。解码将操作内的 `check_ref` 解析为同操作目录里的 `policy_id`，把 `source_ref` 解析为原句。两轮没有可供解码的负向条目。见 [操作包解码](../src/agenttest/product_focus_wire.py#L132)、[来源解析](../src/agenttest/product_sources.py#L306)、[Focus 解码与校验](../src/agenttest/product_focus.py#L517)。
2. **确定性缺口在来源完整性校验。** Prompt 要求列出明确请求的批准负向检查，`validate_requested_checks` 校验提交条目的策略、范围、来源、重复与字面值，空列表不会产生违规。输入虽已识别这是整项目发版加正负范围限定，却没有为该组合单独核对应该声明的负向策略集合。显式边界的 `bind_boundary_actions` 不适用于这条总括请求。v1 用发版主句作为负向来源仍能计入旧指标，也说明当前校验只要求来源存在，对于不同子句的语义强度没有单独计分。见 [Focus 说明](../src/agenttest/product_focus.py#L375)、[负向映射校验](../src/agenttest/product_checks.py#L150)、[显式边界绑定](../src/agenttest/boundary_obligations.py#L94)。
3. **规划和实际执行没有丢失这 24 条批准义务。** 五轮主 Planning IR 均包含七个已批准正常/回读策略实例，全部关联 `request_1`。真实 Agent 的发版入口另按批准目录生成负向批组。负向批组使用自己所有的 `policy_id`，有 Focus 映射时沿用该条目，缺失时以原任务文本作为该批设计上下文；`ProductTasks.run_group` 将这些条目送入该组的 `required_cases`，并按当前实现使用带显式依赖的 legacy 构造路径。v2/v5 的批组上下文能看到整句 `source_quote`，这是一条已批准批次的调度来源，未写回原 Focus 映射。最终 Scenario 仍由真实的策略、身份绑定、预检和 HITL 控制。见 [负向分组](../src/agenttest/product_obligations.py#L116)、[批组上下文](../src/agenttest/product_agent.py#L752)、[设计路径](../src/agenttest/product_tasks.py#L1302)。
4. **统计和持久化一致。** `ProductTasks.coverage` 取 `summary.requested_checks` 的不同策略 ID 作分母，取该集合与已执行策略 ID 的交集作分子。批准负向覆盖从 `negative_check_catalog` 取集合。v2/v5 在首个阶段快照就为 0/0，并贯穿所有九个快照；v3/v4 的请求级分母从首个快照起一直为 24。五轮最后的 phase JSON 与 SQLite `tasks.summary` 中的 `focus`、`requested_checks`、`coverage`、`termination` 一致。v2/v5 各有 94 条实际 HTTP 回执，24 个负向策略各有一次执行证据。未发现序列化、过滤、合并或持久化丢失。manifest 封存任务、靶场与版本，评分器读最终阶段的 `summary.coverage`，两个来源没有路径不一致。见 [Focus 持久化前赋值](../src/agenttest/product_tasks.py#L829)、[覆盖公式](../src/agenttest/product_tasks.py#L2331)。

### COMPLETE 的契约

- **产品任务完成。** 整项目发版用确定性范围账本检查整项目批准范围和实际策略证据。v2/v5 的该账本中 `requested`、`selected`、`approved` 都是 31/31，批准事务已测、清理已证明。按原 `evaluate` 与义务账本复算，四轮 `complete=true`，v1 为 false。这符合产品当前的整项目执行完成契约。见 [产品完成规则](../src/agenttest/task_completion.py#L760)、[请求范围证据](../src/agenttest/scope_ledger.py#L248)、[终止汇总](../src/agenttest/obligation_gate.py#L369)。
- **发布请求级证明。** 历史评分另要求 `coverage.executed_requested_checks == 24`。该指标要求经 Focus 显式链接且已有实际执行回执。产品的 `COMPLETE` 不承诺这项映射完整，也不能用来证明该项达标。v2/v5 的空映射是缺少这一类证明，0/0 保留。没有根据已批准策略回执、响应断言、范围总账或批组回退来源推定 24/24。

## 2. P1 · v1 三次引用失败与终止

本组使用 `required_cases`，普通 `cases` 为空。三份原始 Design 都按键提交了该组四项义务，但四个用例的 `before=[]`、`after=[]`，每个检查都引用各自未声明的创建步骤。解码把 `before + check + after` 展开，因而首个检查落在 `cases.0.plan.steps.0.arguments.0.from_step`。三次均在构造时拒绝，后面的用例没有各自独立的 Runtime 拒绝记录。见 [批组线格式展开](../src/agenttest/product_checks.py#L550) 和 [构造器引用规则](../src/agenttest/plan_construction.py#L244)。

| 尝试 / 监督轮 | Design 调用 ID | `step_id` → `from_step` | 发给 Design 的备注要点 |
| --- | --- | --- | --- |
| 1 / 11 | `ed214eacaa7e4590aef46f85f1880d63` | `neg1_replace_missing_name` → `setupCreateParcelNeg1` | 各负向检查独立，校验批准状态；需资源时删除并回读 404。 |
| 2 / 13 | `63d6bb7a00cf46d8b1b74a0957f3e6f8` | `check_replace_missing_name` → `seed_parcel_missing_name` | 要求不用前置步骤、逐项只发单步，禁止 `from_step`。 |
| 3 / 15 | `c83580a846214848a2d0ee66bb198cf0` | `replace_missing_name` → `createParcelForNegative1` | 再次要求单步且无 `from_step`，仅提到缺少名称并检查 422。由候选 `design:web_rule_7_negative_1` 委派回原负向组。 |

共同错误为 `CONSTRUCTION_REFERENCE_INVALID / SOURCE_UNKNOWN`，`obligation_id=web_rule_7_negative_1`，
引用槽为 `path_parameters.parcelId`，`select=$.id`。三项候选列表
`allowed_from_steps`、`direct_from_steps`、`reordered_from_steps` 都为空，无其他被屏蔽候选。
用每次原始响应与原始 context 离线解码，三次诊断连同路径、作用域和候选逐项复现。

### 合法生产者与反馈传递

- **引用非法，候选枚举正确。** `from_step` 要求同一用例内已有节点，不能引用策略名、别的用例或者已清理的资源。三个名字只是变化，同一种未声明来源错误重复了三次。A3 正确列出提交方案内无候选。当前批准策略 `web_rule_7_negative_1` 继承 `web_rule_7`，其 `parcelId` 授权来源为匿名身份下的 `createParcel / web_rule_3` 对应 `$.id`。三次真实设计 Schema 都允许 `before` 放该策略，但未强制非空，接受了这种形状直到构造校验。见 [批准前置策略](../src/agenttest/product_checks.py#L397) 与 [局部候选计算](../src/agenttest/plan_construction.py#L180)。
- **反馈进入 Agent 消息，缺口发生在 Agent 到 Design。** 第一次和第二次拒绝均以 ToolMessage 保存在 checkpoint，包含错误路径、空候选、所属义务与同用例已声明 ID 的提示，后续都有 Agent 模型回合。第二次拒绝后，Agent 一度请求已测的 `design:web_rule_3_negative_1`，收到 `UNKNOWN_CANDIDATE_ID`，同时读了覆盖情况，之后才提交第三次设计。第三条拒绝有 checkpoint，没有后续已发送模型回合。Agent 完整 POST 正文未归档，checkpoint 与回合顺序是这一路径的证据边界。
- **Design 请求可精确复验。** 三次 Design context 都重新编码并与真实请求摘要一致。第二、三次正文含表中模型自写备注，没有上一次的 `CONSTRUCTION_REFERENCE_INVALID`、`SOURCE_UNKNOWN` 或候选列表。三次工具事件均为 `runtime_repair=false`。`run_group` 在备注为空或重复时才调用 `formal_repair_note`，两次新备注都走了直接传递。虽然 `ProductTasks` 将上次拒绝传给 `instance_feedback` 参数，这个字段只有实例输入路径进入请求，本组三次走 legacy 负向批组。这是可复验的 Harness 反馈传递缺口。见 [修复备注选择](../src/agenttest/product_agent.py#L710)、[上次诊断传递](../src/agenttest/product_tasks.py#L1418)、[实例反馈使用条件](../src/agenttest/product_design.py#L726)。
- **机会已经消耗，但可信自动修复未执行。** 三次均发出了真正的 Design 调用，消耗该组 3/3 次尝试。第一轮是初次提案，后两轮都是改提案机会。第三次满足上一条为形式拒绝的准入条件，却仍由不同的模型备注占用，没有消耗每组一次的确定性自动修复。另两轮无效备注把资源前置条件写成单步禁引用，无法代替缺失生产者的合法方案。

### NO_PROGRESS 和剩余义务

三次失败均在预检和业务派发前结束，各发 0 次业务 HTTP，业务累计停在 54。拒绝没有增加已测义务、可信清理回执或解除阻塞。用 v1 已保存的持久化执行状态与同一实测证据签名做内存复算，三个无进展观察依次得到 **1、2、3**，触发 `NO_PROGRESS`。最后一个 Agent 记录为 `calls=0 / usage_state=NOT_SENT / error=NO_PROGRESS`；没有第四次已发送尝试。已结论为 `PARTIAL / OBLIGATION_NOT_DISPATCHED`，与现有规则一致。终止时还有 66 次业务请求、23 次模型调用和 235,626 tokens 的当时可用额度，停止原因并非额度耗尽。见 [进展证据签名](../src/agenttest/execution_budget.py#L401) 和 [ADAPTIVE 观察计数](../src/agenttest/execution_budget.py#L523)。

未派发的 10 项全部属于 `replaceParcel`。批组 5 有 4 项，剩余尝试 0；批组 6 有 4 项、批组 7 有 2 项，尝试均为 0，终止前仍具备分组授权和依赖条件。用 v3 已执行的三个正例批组原始 Design 在 v1 的批准项目上离线解码并预检，**10/10** 均有合法 Scenario。每个计划为已批准创建 → 同身份负向替换 → 批次清理 DELETE → 同身份批准的 absence GET，共 40 个计划请求，低于 v1 当时剩余的 66。预检生成了 `createParcel` HITL 审批操作清单，并确认回读要求；没有执行审批或发送请求，Mock 传输调用为 0。此结果证明存在可执行方案，不证明 v1 可以在同一次实时运行中产生这些方案，也不补记历史证据。

按题设 A–E，**A** 为真，Agent 自写备注误解了引用提示，Design 改名后仍引用未声明节点；**B** 在本样本未发现，A3 的候选范围是当份方案；**C** 为真，合法生产者在批准目录内，空列表提示没有明确说明应补哪类前置节点，且可信反馈未进入实际重提的 Design 请求；**D** 为假，所缺的十项均已有合法计划证据；**E** 为真，终止规则按原设计触发，风险集中在派发前的计划与反馈路径。

## 3. 评分与安全对账

用原 P3-B2.4 的 `grade.py` 对两批归档做只读复算，P3-B2.4 为 **0/5**，P3-B2.5 为 **2/5（v3、v4）**，与归档一致。两批使用同一评分代码对请求级已执行数、31 项批准义务、24 项无重复负向策略、完成、预期需检出的 503 和清理证据判分。P3-B2.5 审计另核对了 HTTP 事件顺序、审批与逐资源回读，这些检查对本批各轮没有改变原评分布尔值。`strict_complete` 和 `checks_passed` 独立公开，历史评分没有把它们设为该注入故障场景的通过条件。 [原评分器](../.eduwork/p3b24-audit/grade.py)

- 正式验收 430 条 HTTP 回执全部持久化，115 条 POST 均对上已批准的 HITL，未发现越权、未知写入或重放。65 次创建均有同身份 DELETE 后的实际 GET 404 及已批准回读断言。库存与 204 没有用于替代回读。
- 每轮查到 2 条真实 503 回执，初测及复核各一条，主用例留下 1 项 `ASSERTION_FAILED`，预期仍是 200。v2–v5 的 `complete=true` 表示覆盖和清理齐全。`strict_complete` 和 `checks_passed` 五轮都为 false。没有把检测到故障换算为断言通过。
- P3-B2.3、P3-B2.4、P3-B2.5 的历史 NO-GO 均保留。请求级 0/0、各轮评分及 4/5 门槛保持原样。

## 4. 下一轮最小改动候选

| 优先级与边界 | 最小修复候选 | 对应定向测试 | 风险与限制 |
| --- | --- | --- | --- |
| P0 · Focus 对已确定范围的校验 | 仅当现有的确定性分类已识别整项目发布和明确的正负范围限定时，从该次已批准负向目录得到应证明的集合。用现有 Focus 拒绝与修复通道报告缺少的操作内 `check_ref` 和原句来源，要求下一份提案显式提交。保留 `request_2` 的服务端范围限定身份。 | 离线回放 v2/v5 的空列表应返回缺失映射诊断；v3/v4 原样通过。部分缺失、零批准负向、普通功能验证、受限发版及显式字面值各测一条。覆盖统计仍只读已验证的 Focus 映射和实际回执。 | 避免按全目录扩大未批准或缩小的范围；先确认目录准备状态与阻塞记录。可能增加一次现有 Focus 修复机会的使用。避免对历史任务补记映射或更改评分。 |
| P1 · 已有形式修复的反馈传递 | 当服务端已有同组形式拒绝时，在现有一次确定性修复机会内把受验证的诊断作为实际 Design 约束。对引用缺失附上批准生产者策略、绑定槽和选择器；同组新模型备注不能排除获准且必需的前置。A3 已声明候选列表仍按原局部语义保留。 | 模拟 v1 的新备注与委派调用，断言下一次实际编码包含结构化诊断与合法生产者，确实计入现有自动修复；不改分组尝试上限。另测候选空、合法已声明后置来源和无授权生产者，确保仍拒绝缺失引用。 | 不自动改写被拒 DSL，不插入业务步骤，不将未批准 ID 提升为候选。长提示占用上下文；使用现有脱敏、长度限制和封闭策略 ID。 |
| P1 · 可选的后续收紧 | 如果能证明该槽始终要求同用例前置且无固定资源路径，考虑让其专属 `before` Schema 要求非空。暂保留构造器和预检作最终守卫。 | 覆盖必需前置、固定资源、允许非创建来源、登录依赖与多个合法候选。原 v3 十项方案应继续通过。 | 全局加 `minItems` 会误伤可单步执行的检查，非空数组也不能证明所写 ID 对应批准来源。无需与 P0 或第一项 P1 捆绑。 |

建议先实施 P0 的范围完整性校验及其回放用例，再单独验证 P1 将可信反馈送入下一次 Design 的改动。无需调整 `NO_PROGRESS`、预算、重试次数、断言、清理或 HITL，也无需增加完成门禁。

## 5. 验证与证据索引

- 原始证据根目录为 `.eduwork/p3b25-acceptance-ae61d8a/`。五轮 `manifest.json`、`model-outputs.jsonl`、`phase-*.json`、`lab/workspace/tasks.sqlite3`、`lab/workspace/tasks.checkpoints.sqlite3` 均为只读依据。[逐轮审计](../.eduwork/p3b25-acceptance-ae61d8a/audit.json) · [P3-B2.5 验收记录](deepagents-hitl-reliability-p3b25.md#15-真实稳定性验收2026-11-23)
- 已执行五份 Focus 原文复解码、三份 v1 Design 原文复解码与请求摘要校验、五轮产品完成复算、两批历史评分复算、十个待派发 Scenario 的本地预检、进展计数复算。定向 pytest 结果为 **115 passed、1 个既有 xfailed**，涉及 Focus、requested checks、引用诊断、义务门禁、执行预算与任务完成六个测试文件。
- P3-B2.5 报告和 `progress.md` 仍为启动时的两份未提交修改，内容未改。只新增本诊断报告。没有实施修复、重新冻结、调用真实模型、发业务 HTTP 或启动验收。

## 6. 2026-11-23 · P0 Focus 请求级负向映射完整性

本地开发基线为 `ae61d8ad12bb9aa32f6a96c9456031eb46fe44f8`。新增规则在接受 `selected` 或 `all_ready` Focus 前运行。服务端须已识别整项目 `PRE_RELEASE` 与明确的正负范围限定。本次输入须能解析为一个无额外限制的当前整项目发版主句和一个可引用的当前范围子句。附带范围、额外子句或未来动作导致范围不能唯一确定时沿用原校验。

必填集合取本次批准负向目录中的策略 ID，限于已授权并经 inventory 确认 `READY` 的业务操作。有身份要求的策略须使用就绪身份，且没有待补身份记录。回放旧 Context 时采用归档保存的完整未就绪清单和身份状态。目录为空、确认信息不足、策略 ID 重复、操作标识无法安全引用或必填集合超过现有 40 项上限时沿用原校验。集合不随模型选择缩小。接口和身份仍受原有授权规则约束。 [Focus 接受校验](../src/agenttest/product_focus.py) · [批准目录与缺项比较](../src/agenttest/product_checks.py)

新增 `FOCUS_REQUESTED_CHECKS_MISSING`，只列出尚未声明的批准策略。诊断以固定顺序列出 `count` 和每项 `operation_id`、操作内的 `check_ref`、当前范围的 `source_ref`。下一份提案需在对应操作的 `requested_checks` 补写，`exact_value=null`，适用时 `exact_length=null`。已有合法映射保留。`request_2` 仍由服务端记为范围限定，模型只在 `requirement_map` 表达 `request_1`。响应体断言与负向输入继续独立验证。A1 聚合保留可独立判定的原有错误及新增缺项，沿用现有一次修复通道。 [修复载荷](../src/agenttest/product_tasks.py)

### 原始 Focus 回放

| 轮 | 原始负向映射 | 原始响应体断言 | P0 对同一原始响应与 Context 的判定 |
| --- | ---: | ---: | --- |
| v1 | 24 | 0 | 通过，归一化结果与归档相同，保留 24 项原有 `source_2` |
| v2 | 0 | 18 | 拒绝，缺 24 项。创建 10、修改 4、替换 10，建议来源均为 `source_3` |
| v3 | 24 | 0 | 通过，归一化结果与归档相同 |
| v4 | 24 | 18 | 通过，两类映射均与归档相同 |
| v5 | 0 | 18 | 拒绝，缺 24 项。创建 10、修改 4、替换 10，建议来源均为 `source_3` |

v2/v5 的 24 个引用都保留在离线编码的下一次 Focus 请求中。正常任务入口的 Mock 测试截取了实际第二次请求，确认同时包含已有字面值修复信息和缺项清单，且不含未经批准的字面值或凭据。Mock 的第二份提案显式补齐后被接受。另已覆盖部分映射、零批准策略、普通功能测试、受限发布、未来动作、响应体断言、操作及身份未就绪、目录不明确与 A1 聚合。 [定向回归](../tests/test_p3b26_focus_completeness.py)

8 个文件的离线定向回归为 **173 passed、1 个既有 xfailed**，其中新增 P0 用例 14 项。Ruff 全仓检查和 `git diff --check` 通过。未运行全量冻结回归、真实模型或业务 HTTP。

### 评分与后续边界

旧评分器只读复算仍为 P3-B2.4 **0/5、NO-GO** 和 P3-B2.5 **2/5、NO-GO**。P3-B2.3 的历史 NO-GO 同样保留。P0 只改变新 Focus 提案的接受条件，覆盖公式、真实 503 的断言失败、产品 `COMPLETE` 及义务门禁沿用原规则。v2/v5 历史请求级覆盖仍为 0/0，归档与 P3-B2.5 报告原文保留。

P1 仍需在独立改动中验证，legacy 负向批组的下一次实际 Design 请求能收到同组上一回 `SOURCE_UNKNOWN` 的脱敏诊断、批准生产者及绑定选择器，即使模型提交了新的备注。保持现有修复上限及 `NO_PROGRESS`。P0 的离线结果不提供真实验收的新样本。

## 7. 2026-11-23 · P1 Design 确定性引用反馈

legacy 负向批组上一份 Design 在构造阶段报 `CONSTRUCTION_REFERENCE_INVALID / SOURCE_UNKNOWN` 时，`AgentSession.run_group` 从同组、最近一次拒绝记录选取可信诊断。此修复请求省略 Agent 自写备注。`ProductTasks.run_group` 将诊断独立传给 `propose_design`，后者按当前批组批准目录和身份投影 `reference_repair`，由原有 Design 编码器写入实际请求。实例输入路径仍采用原有 `instance_feedback`。[Agent 分组入口](../src/agenttest/product_agent.py) · [任务传递](../src/agenttest/product_tasks.py) · [Design 构建](../src/agenttest/product_design.py)

`reference_repair` 含错误码、用例索引、步骤、字段路径、失败的 `from_step` 与 `select`。`allowed_from_steps` 及其两个子集仍取自上一份方案中的同用例节点。`approved_producers` 根据当前策略、`before` 可用目录、绑定槽和就绪身份计算，只给出操作、策略、角色和选择器。诊断提示先在同一用例的 `before` 中声明该生产者，再引用其步骤 ID。未确认合法生产者时返回空集合。载荷使用白名单投影和有界提示，拒绝直接转发原始请求值、凭据及自由文本。[拒绝位置](../src/agenttest/plan_construction.py) · [生产者投影](../src/agenttest/product_checks.py) · [选择器脱敏](../src/agenttest/model_input.py)

| 核对项 | 归档与离线结果 |
| --- | --- |
| v1 三份原始 Design | 三份均复现 `SOURCE_UNKNOWN`；失败 `from_step` 分别是 `setupCreateParcelNeg1`、`seed_parcel_missing_name`、`createParcelForNegative1`。当前用例的候选始终为空。 |
| 旧版与新请求 | 旧版第二、三次请求含单步和禁止引用的模型备注。新版本用原始第一份拒绝驱动 Mock，实际第二次请求同时携带空的已声明候选与批准生产者 `createParcel / web_rule_3 / role=null / $.id`，绑定槽为 `path_parameters.parcelId`。相反的模型备注及测试凭据均未进入请求体。 |
| 修复次数 | 重试工具记录 `runtime_repair=true` 一次，消耗现有一次机会。同一引用拒绝再次出现时，换备注也无法复用该机会。若先经历另一种形式错误，仍可在第三次采用此修复。组上限仍为 3，定向测试确认没有第四次请求。 |
| 计划与资源边界 | v3 已归档的四个合法 `before` 方案在 v1 的批准项目上通过原有预检，保留创建、负向替换、删除和同身份回读。定向测试覆盖较晚声明的合法候选、没有批准候选，以及另一个用例中已创建或已清理的资源均无法为本用例提供引用。 |

12 个定向文件 **196 passed**，含 5 项新增 P1 测试。Ruff 和 `git diff --check` 通过。回放与测试只使用本地归档或 Mock，真实模型和业务 HTTP 均为 0 次。尚未验证真实模型收到反馈后能否自行修好一组计划。

本改动沿用原有 Planning IR、Schema、DSL、Policy、HITL、义务门禁、`NO_PROGRESS`、预算、清理和未知写入处理。P3-B2.3、P3-B2.4、P3-B2.5 历史 NO-GO 保留，P3-B2.5 的 2/5 和 v1 的 21/31 仍是历史成绩。原发布门槛继续为 4/5。

## 8. 2026-11-23 · 候选冻结与全量离线回归

**本次全量离线回归结论为 FAIL。** 唯一一次 `scripts/run_frozen_regression.py` 执行完成，pytest 为 **2,985 passed、46 failed、15 skipped、1 xfailed**，退出码 1，耗时 **51 分 17.04 秒**。从冻结脚本启动到身份复查结束共 **51 分 30.59 秒**。没有启动真实模型、业务接口或稳定性验收。

### 冻结身份与收集审计

- 基线为 `master` 的 `ae61d8ad12bb9aa32f6a96c9456031eb46fe44f8`，启动时与本地 `origin/master` 引用一致。候选有 10 个已修改文件和 3 个未跟踪文件，暂存区为空。启动前未发现并行回归或验收进程，变更路径和内容扫描只有测试用合成凭据命中。README 保留历史 NO-GO 和待真实复验状态。
- 冻结脚本的 checkout SHA-256 为 `42ecd6d417bd2c4151f660035a52ed50ed9410072808b22c6a359169ce15802c`。另存的 1,715 个 Git 可见文件逐文件原始 SHA-256 清单总摘要为 `f10380051ac13d150e44ab0e0a14f180c7eed484948bf37cdd9f284104f58913`。测试结束时两种快照的 HEAD、状态和摘要均一致。
- **3,047 collected、3,047 reported**，`missing`、`extra`、`repeated`、`nonunique` 均为 0。与 P3-B2.5 的原始测试 ID 清单逐项比对，新增恰为 P0 的 14 项和 P1 的 5 项，无移除。15 项跳过和 1 项既有 xfail 的测试 ID 与历史清单一致。
- README 和原有文档包含在完整冻结清单中，运行期间零漂移。全量结束后仅向本报告和 `progress.md` 追加结果，源码、测试、配置和 README 保持冻结字节。

| 清单分组 | 文件数 | 前后相同的 SHA-256 |
| --- | ---: | --- |
| 源码 | 222 | `8ad626bf5586ccdc32871c29c7a38c446a712a01c9d1b23405288f46f7563c15` |
| 测试 | 249 | `9df18dcc3b89c6960c7b2c2f381fce2401a0f3933f5a3dabcde49a597985b204` |
| 配置与脚本 | 155 | `0344df3efe430a6905bea2fdd44f4e36d8e7d8b9484dd44a971a29b7a4d27ff1` |
| README 与文档 | 805 | `1770405475449d94374e4169db9e9df555fbcbcbe981042a969663c39a00eaff` |
| 未跟踪条目 | 3 | `800a7435dfe500bc006a9fdf682e51dec395a15e08246308783d4e09ecb49c63` |

### 回归分组

| 范围 | 本次终态 |
| --- | --- |
| P0 新增 / P1 新增 | 14 / 5 passed |
| Focus 与请求级映射 | 127 passed、1 个既有 xfailed |
| Design 与 Agent | 114 passed |
| 构造与引用诊断 | 82 passed |
| 引用归档冷评分 | 20 passed、1 failed |
| HITL / 恢复 | 3 / 26 passed |
| 预算 / 账本与模型用量 | 31 / 35 passed |
| 生命周期与清理 | 67 passed |
| 义务门禁与完成判定 | 53 passed |
| 模型输入脱敏 | 14 passed |
| RW 启动授权、消耗与冻结预检 | 34 passed、34 failed |

### 失败归因与边界

46 项失败均发生在 Git 检查。34 项直接收到 `git rev-parse HEAD` 退出 128，另 12 项在 Git 清单或忽略路径检查时收到封装后的 `Evaluation checkout unavailable`。首个失败是 `test_nl_usage.py::test_cli_complete_export_then_no_model_replay`，调用路径在 `nl_usage.execute` 退出 `patch.dict(os.environ, env)` 后再次读取冻结身份时中断。

在相同启动环境中只读复现了触发条件。继承的 `GIT_CONFIG_VALUE_0` 和 `GIT_CONFIG_VALUE_5` 是空值。`git rev-parse HEAD` 在 `patch.dict(os.environ, ...)` 之前和之内均退出 0，退出上下文后 Git 子进程丢失空值条目并退出 128，报 `missing config value GIT_CONFIG_VALUE_0`。冻结脚本的父进程仍能完成 Git 快照比对。现有证据将这批失败定位到 Windows 子进程环境恢复，46 项仍按真实失败保留。

未修改候选、Git 配置、断言、跳过规则或预算来消除失败。未重跑全量，P3-B2.3、P3-B2.4、P3-B2.5 历史 NO-GO 均保留。本次候选未满足全量离线放行条件。

[冻结前完整清单](../.eduwork/p3b26-freeze/run-20261123T062040Z/identity-before.json) · [冻结后完整清单](../.eduwork/p3b26-freeze/run-20261123T062040Z/identity-after.json) · [pytest 日志](../.eduwork/p3b26-freeze/run-20261123T062040Z/regression/pytest.log) · [测试 ID 终态](../.eduwork/p3b26-freeze/run-20261123T062040Z/regression/tests.json) · [结果审计](../.eduwork/p3b26-freeze/run-20261123T062040Z/audit-summary.json) · [实际计时](../.eduwork/p3b26-freeze/run-20261123T062040Z/timing.json)

## 9. 2026-11-23 · Windows Git 环境隔离修复

冻结进程继承了 `GIT_CONFIG_COUNT=10`。其中 `GIT_CONFIG_VALUE_0` 和 `GIT_CONFIG_VALUE_5` 存在且为空。首个失败发生在 `test_cli_complete_export_then_no_model_replay`，其调用的 `nl_usage.execute` 在退出 `patch.dict(os.environ, env)` 后重新读取冻结身份。Windows 的整表恢复会重写继承的空值。Python 的环境映射仍显示这些键，Git 子进程的实际环境中已经缺失。两次只读探针均记录到 `git rev-parse HEAD` 在补丁前、补丁内和退出后的退出码分别为 **0、0、128**。stderr 为 `error: missing config value GIT_CONFIG_VALUE_0` 和 `fatal: unable to parse command-line config`。同一目录、同一 Git 命令显式传入 Python 环境映射后退出 0，复现无需改变仓库、路径、锁或 Git 配置文件。[只读探针](../.eduwork/p3b26-git-environment/before-probe.jsonl)

顺序复现中，`test_existing_regression_temporary_directory_is_preserved` 单独运行通过。把它放在原首个失败用例之后，两项均失败。修复前按原冻结记录的顺序单独执行全部 46 个失败 ID，**46/46 复现**。[单独运行](../.eduwork/p3b26-git-environment/before-adjacent-alone.log) · [最小顺序组合](../.eduwork/p3b26-git-environment/before-pair.log) · [原 46 项复现](../.eduwork/p3b26-git-environment/before-46.log)

修复仅在 `tests/test_nl_usage.py` 的 Windows 测试内替换 CLI 使用的环境覆盖上下文。覆盖结束时只撤销该次传入的键，继承的 Git 配置无需重写。原有真实 Git 调用、检查和断言照常运行。新增测试为子进程继承一个合法空配置及一个正常非空配置，验证两次环境覆盖内外的 `HEAD`、配置和环境恢复，再移除一个必需配置值，确认真实 Git 仍输出对应 stderr 并返回 128。生产使用的补丁上下文沿用原实现。[测试隔离与新增回归](../tests/test_nl_usage.py)

| 定向验证 | 结果 |
| --- | --- |
| 新增环境测试与原两项顺序组合 | 3 passed |
| 原 46 项按原顺序复验 | 46/46 passed |
| P0 与 P1 | 14/14、5/5 passed |
| 新增环境测试及相关 Git、冻结、启动检查 | 18/18 passed |
| 合并定向运行 | **83 passed**，耗时 598.86 秒，83 项均有唯一终态 |

Windows 通过真实 Python/Git 验证。现有 Linux WSL shell 验证了导出的空值可被子进程继承，但该发行版缺少 Git 和 Python，未运行 Linux pytest。全仓 Ruff 和 `git diff --check` 通过。P0、P1、产品源码和配置的摘要保持不变，原冻结目录 10 个文件逐项 SHA-256 一致。[修复后顺序组合](../.eduwork/p3b26-git-environment/after-new-pair.log) · [定向日志](../.eduwork/p3b26-git-environment/after-targeted.log) · [逐项终态](../.eduwork/p3b26-git-environment/after-targeted-tests.json)

已满足申请再次冻结的定向条件。唯一一次全量回归仍为 **FAIL**，P3-B2.3、P3-B2.4、P3-B2.5 的历史 NO-GO 保留。第二次全量冻结和真实验收尚未启动。

## 10. 2026-11-23 · 第二次全量离线冻结回归

**P3-B2.6 第二次全量离线回归 PASS。** 使用当前项目 `.venv` 与现有 `scripts/run_frozen_regression.py`，在独立目录执行一次默认完整测试集。pytest 退出码 0，**3,032 passed、0 failed、0 errors、15 skipped、1 xfailed**。pytest 耗时 **48 分 19.14 秒**，冻结脚本总耗时 **48 分 32.562 秒**。本次保留 Windows 启动时继承的空值 Git 配置，使用真实 Git 检查与原有断言。离线网络限制、超时及 skip / xfail 规则沿用原配置。

### 收集和逐项审计

- **3,048 collected、3,048 reported**。`missing`、`extra`、`repeated`、`nonunique` 均为 0，按原始 pytest ID 对账，保留参数文本中的斜杠差异。
- 相比第一次新增恰好 1 个测试，即 Windows Git 环境恢复。原测试无移除，唯一终态变化是原 46 项由 failed 变成 passed。Windows 测试、P0 14 项和 P1 5 项均实际执行并通过。
- 第一次冻结目录的 10 个文件逐项 SHA-256 一致，**第一次 FAIL 保留**。15 项跳过和 1 项既有 xfail 与第一次的 ID 及终态一致。

| 范围 | 第二次终态 |
| --- | --- |
| Focus 与请求级映射 | 127 passed、1 个既有 xfailed |
| Design 与 Agent | 114 passed |
| 构造、引用诊断及引用归档冷评分 | 103 passed |
| HITL / 恢复 | 3 / 26 passed |
| 预算 / 账本与模型用量 | 31 / 35 passed |
| 生命周期与清理 | 67 passed |
| 义务门禁与完成判定 | 53 passed |
| 模型输入脱敏 | 14 passed |
| RW 启动授权、消耗与冻结预检 | 68 passed |

### 冻结身份

基线为 `master` 的 `ae61d8ad12bb9aa32f6a96c9456031eb46fe44f8`，与本地 `origin/master` 引用一致。候选共 11 个已修改文件和 3 个未跟踪文件，暂存区为空。1,715 个 Git 可见文件的逐文件原始 SHA-256 清单总摘要为 `66b43a75f8825085a2144175866f6ef779526bcda22bd7f0a15a5d5ee0624b7a`。冻结脚本的 checkout SHA-256 为 `47497f1df20cde9e21f6183dccd2b1c4cde095ff311bbecae55f370baec5eb3e`。两套快照前后完全一致，工作区状态和文件均零漂移。

| 清单分组 | 文件数 | 本次前后一致的 SHA-256 |
| --- | ---: | --- |
| 源码 | 222 | `8ad626bf5586ccdc32871c29c7a38c446a712a01c9d1b23405288f46f7563c15` |
| 测试 | 249 | `cbf7b196f3693b9e04c7a390445dfa395f4776590313258f62bfe629872dc48c` |
| 配置与脚本 | 155 | `0344df3efe430a6905bea2fdd44f4e36d8e7d8b9484dd44a971a29b7a4d27ff1` |
| README 与文档 | 805 | `a07c3c214d08a64e50ab210ae64c64a60e1e7aff144e084db7c962dcfe321533` |
| 未跟踪条目 | 3 | `177d9ba96af584f38ae95f33b12a574ee9735fb101588612095a55ba6b5de3b2` |

运行结束后只向本报告和 `progress.md` 追加结论。README、源码、测试和配置仍与本次冻结快照逐字节一致。

### 耗时与放行边界

最慢三项为 PocketBase 全分母冷评分及篡改检查 **123.32 秒**、通用参数归档复算 **62.56 秒**、引用诊断归档冷评分 **60.84 秒**。前 20 项及同样出现于第一次运行的 `TestDesign` 收集提示均保留在 pytest 日志。完整默认收集、原 46 项、新增 20 项及冻结身份均通过，已满足下一步提交和 CI 核查的本地条件。尚未提交、推送或触发本候选的远端 CI。真实模型和真实业务 HTTP 均为 0 次，历史 NO-GO 和 P3-B3 准入保持现状。

[第二次冻结前清单](../.eduwork/p3b26-freeze/run-20261123T075739Z/identity-before.json) · [冻结后清单](../.eduwork/p3b26-freeze/run-20261123T075739Z/identity-after.json) · [pytest 日志](../.eduwork/p3b26-freeze/run-20261123T075739Z/regression/pytest.log) · [逐项终态](../.eduwork/p3b26-freeze/run-20261123T075739Z/regression/tests.json) · [完整审计](../.eduwork/p3b26-freeze/run-20261123T075739Z/audit-summary.json) · [计时](../.eduwork/p3b26-freeze/run-20261123T075739Z/timing.json)
