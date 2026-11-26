# AgentTest 契约工具复用与领域动作离线 PoC

2026-11-26 · 基线 `bec965fba0d7f5c039812cf94d7567b765747af3`，包含此前未提交的展示、C1 系列及审查文档。适用范围为开发评估。六项长期建设方向、P3-B2.8 原评分 **0/5、NO-GO** 和 **4/5** 门槛保留。C1.2 未完成，C1.1 默认关闭。[长期路线图](ROADMAP.md) · [选型审查](build-vs-buy-and-tooling-review.md)

## 冻结样本与环境

- A 使用 C1.2 包裹 OpenAPI 3.0.3 与原批准快照。四项为创建时数量缺失、数量低于边界、`meta` 类型不符，以及同资源替换时数量缺失。前三项沿用原关联键观察策略，第四项要求创建回执、后续同资源绑定、删除与缺失回读。
- B 使用独立合成 OpenAPI 3.1.0 资产登记契约，本地 `$ref`、查看者和编辑者各有批准规则。四项为 `limit` 越界、`title` 缺失、`notes` 显式 null 及绑定创建响应 ID 后更新 `rating` 越界。合成业务错误码、同角色依赖、清理与所有拒绝/待审控制均冻结。
- 输入与预期分开。先写出项目、位置及人工预期，再记录项目原件、对照 lock 和三项夹具 SHA-256。固定 8 个批准位置、12 个控制，A/B 各十项。A0 的合法基线由独立的冻结模型替身提供。C0 对 A02 与 B01 分别保留一个未赋值的数量与查询开放槽，其余使用已提供的可信基线。
- 首次运行前静态核对发现，包裹 A04 响应 `id` 和消费端 path 在原契约为字符串。保留原 oracle 的数字字面量，另封存响应类型勘误，以字符串 `"17"` 提供该模拟回执。渲染路径、同资源选择器、身份、策略、断言及业务预期相同。B02/B03 的关联头同时是契约和策略上的必填项，原夹具继续按冻结设计执行。
- 使用现有 `.venv-comparison`，版本直接核对为 Python 3.11.15、Schemathesis 4.32.4、Hypothesis 6.170.8、pytest 9.1.1、jsonschema 4.26.0、openapi-spec-validator 0.9.0、httpx 0.28.1。每个 B1 位置最多 16 份数据，正向、负向各 8。seed 为 31826，只开 generate，关闭 examples database 和失败收缩。没有安装或升级依赖。

包裹快照（本地归档 `.eduwork/c1.2-focus-pairs/frozen/parcel.project.json`） · [合成契约](../tests/fixtures/contract-tool-reuse-poc/assets.json) · [输入](../tests/fixtures/contract-tool-reuse-poc/positions.json) · [独立预期](../tests/fixtures/contract-tool-reuse-poc/oracle.json) · [冻结摘要](../tests/fixtures/contract-tool-reuse-poc/freeze.json) · [运行前勘误](../tests/fixtures/contract-tool-reuse-poc/pre-run-erratum.json)

## 三臂如何比较

| 对照 | 实际经过的路径 | 模型度量口径 |
| --- | --- | --- |
| A0 · 当前 Design | 冻结合法提案经 `propose_design`、实际 `TestDesignModel.prepare_request` 和出站响应 schema 检查、生产解码及 `construct_design`。原批次用例预检追加获批的观察或清理步骤。 | 记录替身调用的位置、UTF-8 请求字节、模型需填写的线协议叶子值和步骤参数条目。真正模型调用为零。 |
| C0 · 原确定性构造 | 从已批准的单一策略、可信基线和唯一前置生成草案，经 `construct_plan`、统一预检、批准绑定及 Executor。缺值时报告开放槽。 | 这条试验路径没有 Design 编码。开放槽是尚未完成的输入，不能计作省去了一次等价执行。 |
| B1 · 公开生成 API + 原派生 | 沿用已有对照的内存 `from_dict`、操作级 `as_strategy` 和 Hypothesis 生成接缝。只读取可批准的 body/query 数据。全部路径 ID、认证和资源前置仍由可信计划绑定。候选必须通过原型别、合法基线及冻结输入检查，负向固定变更仍由原策略派生。 | 以 C0 为基线，仅在解决相同开放槽并取得同等原断言证据时计为新增可省 Design 的位置。B1 的原生负例与批准精确请求只做离线等价比较。 |

三个臂共用冻结的十二项拒绝/待审控制，门禁只执行一次，不扩大分母。故障证据、响应替身和明确待审期望留在评估层。未替换生产工具协议、允许策略、断言、原完成评分、Executor、HITL、Checkpoint 或 HTTP 账本。[离线驱动](../scripts/contract_tool_reuse_poc.py) · [已有工具对照](plan-reuse-stage1.md)



## 逐位置结果

**采用结论：暂不引入 Schemathesis 生产依赖，继续 C0 与高层领域动作设计。** B1 在两份契约各补齐一个开放槽，两个位置均通过原断言。共 **18/20** 个位置符合冻结预期。三臂共同遇到的两处必填 header 失败使采用门槛未满足。此 PoC 证明了纯数据生成接缝和两个增量，不证明完整负向构造或复杂发布可用。

表中数字是实际进入记录型 Transport 的次数。十二个共用控制各执行一次。通过情况以冷复核为准。原汇总曾错误地用仅在其他运行模式填充的 `RunReport.plan_structure` 作为策略列表，使实际 PASS 的行在原 `oracle_pass` 栏显示 false。保留该原件，冷复核用已执行步骤 ID 关联冻结计划，并逐条对账持久化回执、计划摘要、策略摘要与原断言。该核对没有再生成样本或执行 HTTP。原汇总（本地归档 `.eduwork/contract-tool-reuse-poc/completed-run/summary.json`） · 冷复核（本地归档 `.eduwork/contract-tool-reuse-poc/completed-run/cold-audit.json`）

| 位置 | 冻结义务或边界 | A0 | A0 请求 B / 输出叶子值 | C0 | B1 |
| --- | --- | --- | ---: | --- | --- |
| A01 | createParcel 数量字段省略 | PASS / 3 | 43,143 / 31 | PASS / 3 | PASS / 3，固定派生 |
| A02 | createParcel 数量低于批准下界，实发整数 0 | PASS / 3 | 43,162 / 31 | 开放槽 / 0 | PASS / 3，补齐合法基线数量 |
| A03 | createParcel `meta` 类型不符，冻结字面量 | PASS / 3 | 43,162 / 31 | PASS / 3 | PASS / 3，固定派生 |
| A04 | replaceParcel 省略数量，绑定当前创建资源，以同身份删除和回读 | PASS / 4 | 43,358 / 59 | PASS / 4 | PASS / 4，固定派生 |
| B01 | listAssets 查询 `limit` 越界，实发参数值 6 | PASS / 1 | 35,164 / 12 | 开放槽 / 0 | PASS / 1，补齐合法基线 limit |
| B02 | registerAsset 省略 `title`，期待批准业务码 | TOOL_ERROR / 1 | 35,192 / 22 | TOOL_ERROR / 1 | TOOL_ERROR / 1，写入前必填 header 失败 |
| B03 | registerAsset `notes` 显式 null，期待批准业务码 | TOOL_ERROR / 1 | 35,152 / 22 | TOOL_ERROR / 1 | TOOL_ERROR / 1，写入前必填 header 失败 |
| B04 | 创建并按回执 ID 更新 `rating` 至批准的越界值，随后同角色清理 | PASS / 4 | 35,393 / 36 | PASS / 4 | PASS / 4，固定派生 |

| 位置 | 冻结拒绝或待审控制 | 实际诊断 | 派发 |
| --- | --- | --- | ---: |
| A05 | 把批准的下界越界换成上界 | `NEGATIVE_VALUE_BOUNDARY_DIRECTION_CHANGED` | 0 |
| A06 | 在获批变更之外新增 body 字段 | `OPENAPI_UNKNOWN_BODY_FIELD` | 0 |
| A07 | 绑定缺失的生产者 | `CONSTRUCTION_REFERENCE_INVALID`，合法候选空 | 0 |
| A08 | 凭空指定角色 | `CONSTRUCTION_NODE_NOT_APPROVED` | 0 |
| A09 | 写计划未获批准 | `HUMAN_APPROVAL_REQUIRED`，SQLite 记录 `WAITING_APPROVAL` | 0 |
| A10 | 缺已批准的同资源清理步骤 | `DESIGN_SELECTED_CLEANUP_MISSING` | 0 |
| B05 | 未登记的负向策略 | `CONSTRUCTION_NODE_NOT_APPROVED` | 0 |
| B06 | 两个生产者均可用但没有批准选择 | `CONSTRUCTION_REFERENCE_INVALID`，同时列出两个合法生产者 | 0 |
| B07 | 用查看者替换获批编辑者 | `CONSTRUCTION_NODE_NOT_APPROVED` | 0 |
| B08 | 用另一未经批准的字面量替换固定值 | `NEGATIVE_VALUE_VARIANT_NOT_AUTHORIZED` | 0 |
| B09 | 远程契约引用 | `OPENAPI_LOCAL_REFERENCE_REQUIRED`，在第三方加载前拦截 | 0 |
| B10 | 把已固定的实例引用改向同角色另一实例 | `PLANNING_REFERENCE_MISMATCH`，批准与提议的生产者不同 | 0 |

A0 与 B1 各通过 6/8 个批准位置。C0 通过 4/8，另两个保留开放槽、两个在执行层拒绝。12/12 共用控制符合预期，A0/B1 各对应 18/20，C0 对应 16/20。字段欠缺、未派发、只完成观察或断言未判断均未当成义务完成。逐位置收据（本地归档 `.eduwork/contract-tool-reuse-poc/completed-run/summary.json`）

## 类型、断言及安全

- **整数表示。** 在包裹实际数量约束中，标准 JSON Schema 校验接受等值的 `2` 与 `2.0`。JSON 重新解析后分别为 `int` 与 `float`。同一 AgentTest Validator 只接受前者，`2.0` 在编译后原预检仍为 `OPENAPI_VALUE_TYPE`，零派发。历史对照的 `5.0` 属同类表示差异。严格类型是当前产品输入与批准变更的边界，本轮未做标准化转换，也未放宽 Validator。两个开放槽的实际采样均产生可用严格整数。
- **必填 header 的共用阻塞。** B02/B03 已按原计划构造 `X-Audit-Key`，先执行成功的计数 GET。Executor 验证合法基线时传入已解析 header。随后 `mutate_request` 再次验证原请求，只传路径、query、body 和媒体类型，使必填 header 在第二次验证里缺席，导致 `OPENAPI_PARAMETER_REQUIRED`。三臂的拒绝点一致，负向 POST 均未进入 Transport。显式 null 与字段省略已分别锁定，但 B03 的真实出站投影及 B02/B03 的业务错误码断言没有测到。[Executor 的两次验证](../src/agenttest/executor.py#L524) · [变体验证](../src/agenttest/negative.py#L92)
- **同状态、不同业务码。** 冻结的 B03 补充对照同样在 POST 前被阻断，保留未验证。补充的可达 B04 对照中，正常创建获 201 回执，更新仍回 422，但冻结的业务码断言失败，报告为 `ASSERTION_FAILED`。失败后原清理路径派发 DELETE，未采集缺失回读，不能把这次故障对照算作闭合清理。B04 正常配对才有同身份删除和 404 回读。
- **审批、预算和恢复。** 复用原 `group_operations`、SQLite 评审记录及 `HumanReviews`，PoC 用脚手架承接 Graph 中断点，审批绑定预检计划摘要。A09 pending 状态零派发。预算零的 B04 被原 BudgetTransport 阻断，记录型 Transport 零请求，外层保守记录创建结果未确认。未知写控制在原步骤账本记录一个 `DISPATCHED`、无可信回执。同一运行恢复返回 `DURABLE_WRITE_OUTCOME_REQUIRES_REVIEW`，新增派发 **0**。本轮未重新验收整套生产 HITL/Checkpoint 流程。
- **请求计数。** 归档完整运行的主位置 A0/C0/B1 分别 **20/16/20**，共 **56** 次记录型派发，全部十二个共用控制为 **0**。补充对照 **5** 次（B03 错码尝试 1 次观察、B04 错码 3 次、未知写 1 次，类型及预算对照 0 次），合计 **61**。生成器自行派发、实验中实际网络 HTTP、DNS/连接拦截事件及真实模型调用均为 **0**。Windows asyncio 本机唤醒 IPC 在实验循环进入前建立。未调用 Case 执行方法、CLI runner、状态 runner、外部引用加载或独立重试。

## 生成复用与维护成本

| 指标 | 实测及判读 |
| --- | --- |
| B1 采样 | 8 个位置各 8 个正向和 8 个负向，共 128，短缺 0。正向有 16 个能填入冻结开放槽，另 48 个未保持固定 fixture 值，不用来覆盖原基线。两个开放槽分别有 3、5 个不同的严格整数。 |
| A02 / B01 | A02 的合法数量候选覆盖 1–3，B01 的合法 limit 覆盖 1–5。两处都选首个合格值 1，固定派生随后分别发出原批准的 0、6。两处打开的 schema 边界有真实候选增量。 |
| 原生负例 | 64 个中等价精确请求 0。48 个完整候选与批准变更或固定基线不等价，另 16 个所在位置无法通过必填 header 下的原变体构造。原生负例派发 0。计划里的已批准负向仍由 `mutate_request` 负责。 |
| A0 模型负担 | 8 个离线 Design 位置，合计 **313,726 B** 编码请求、**244** 个替身输出叶子值和 **32** 个步骤参数条目。C0/B1 没有实际 Design 编码或请求。就已通过等价门禁的位置，C0 可省 4 个 A0 位置，B1 可省 6 个，新增 **2** 个。B02/B03 无成功的可替代执行，留在失败分母。指标只衡量离线编码和调用点。 |
| 生成耗时与代码 | 8 个位置的纯生成累计 **4.129 秒**，逐位置约 0.117–2.787 秒，首个位置含首次生成初始化。生成、投影及审计的代码成本详见驱动。当前生成/匹配函数组约 106 行，另有通用严格 wire 比较、冻结、报告与测试脚手架。没有按项目名改变生成规则。资产、策略、基线的人工准备和外部依赖维护仍在，未做人工工时对照。 |

A02/B01 的基线候选确有价值，固定派生已经完成其他通过点。当前增量不足以跨过共同业务语义门槛。继续保留 Schemathesis 作为比较环境的纯数据来源。同步依赖、通用转换及额外生产调度均留待单独决定。接口层的简化可脱离这个依赖独立评审。

## 下一步最小领域工具方案

复用 `get_test_scope` 和现有 `run_test_group(group_id)`、`run_open_obligation(candidate_id)` 的动作身份。可信服务先计算获批候选及必要的前置、身份、绑定、预算和审批。Agent 只选择目标、测试类型及合法候选，必要时在给出的槽里提交输入值。候选不可执行时给出原诊断及可调查的证据引用。结果显式区分候选已选择、预检通过、等待审批、派发、断言与清理。

| 情形 | 建议处理 |
| --- | --- |
| 唯一合法结构、基线与批准变更均已齐备 | 先验证一个 C0 单义务切片。沿原组、预检和审批接口编译稳定计划，省去该位置的内层 Design。A01/A03/A04/B04 有对应的离线证据。 |
| 结构唯一、业务数据槽未填 | 复用现有 `instance_inputs` 的槽位表达，为固定两处示例保留经复核的业务输入或明确待审。B1 可作为离线候选对照。 |
| 合法方案多，或开放语义需要组合多个业务动作 | Agent 从已有候选中选择、调查或澄清。候选目录不足时保留完整 Design 及原验证器。 |
| 来源、角色、值授权或清理无法确定 | 返回待审。审批、预算或未知写门禁由原可信服务控制，模型不能选择绕过。 |

后续改造前先判断 B02/B03 的必填 header 验证缺口应独立排期还是保持暂缓。本轮未改校验、角色、执行、恢复、评分或生产依赖。保留 C1.1 默认关闭，C1.2 未完成，P3-B2.8 仍为 0/5、NO-GO。等待技术选择。

## 验证与证据保留

完整三臂驱动需要本地包裹冻结快照及 `requirements-comparison.lock` 对应的可选环境。标准 CI 仅复用合成资产夹具和辅助构造函数，Schemathesis 与 Hypothesis 的导入局限于可选生成函数。Header 缺陷的后续独立复验见修复报告，冻结 PoC 结果仍为 18/20。[Header 修复](negative-request-context-fix.md)

Git 属性固定夹具的 LF，并保留对照锁文件已冻结的 CRLF 字节。这样 Windows 检出仍符合原摘要。

- 原版脚手架在未知写对照续读报告时使用旧 revision。主位置与前几个补充对照已有归档，汇总未完成。保留第一次业务替身回执和 SQLite 账本，不改样本。修正脚手架读取原报告 revision、使用现有 `ProgressStore` 并增加逐位置落盘后，在另一目录完成同一冻结比较。其后仅做冷复核和静态检查，未启动第二次完整 PoC 或任何真实验收。第一次脚手架归档（本地归档 `.eduwork/contract-tool-reuse-poc/frozen-run`） · 完整运行归档（本地归档 `.eduwork/contract-tool-reuse-poc/completed-run`）
- 现有 `test_approved_negative.py`、`test_negative_values.py`、`test_negative_post_observation.py`、`test_product_preconditions.py` 定向 **71 passed**。本轮无全量回归或五轮验收。
- 新增脚本通过 Ruff。`git diff --check`、新文件空白检查、报告相对链接和冻结文件摘要均核对通过。
- 冷复核复用 `scripts/contract_tool_reuse_poc.py --output .eduwork/contract-tool-reuse-poc/completed-run --audit`，只读原位置收据、ReportStore 与 SQLite 账本并写出单独核对结果。[脚本](../scripts/contract_tool_reuse_poc.py) · 核对结果（本地归档 `.eduwork/contract-tool-reuse-poc/completed-run/cold-audit.json`）
