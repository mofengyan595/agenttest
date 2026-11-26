# C1 确定性 RequestedCheck 影子编译

2026-11-24 · 基线 `bec965fba0d7f5c039812cf94d7567b765747af3`

**离线原型通过。** 对现有可信规则已证明的封闭整项目范围，编译器可以从批准目录生成完整的 RequestedCheck，保留来源和身份见证。对已确认的纯结构化选择，可以生成可审阅的映射，旧协议需要原文证据的字段保持空缺。正式 Focus、Design 及执行仍走原路径。P3-B2.8 保持 **0/5、NO-GO**，原复杂自主发布门槛保持 4/5。[产品演示基线](product-demo-and-delivery-baseline.md) · [P3-B2.8 历史结果](deepagents-model-reliability-p3b28.md)

## 职责与接点

| 事实或决策 | 当前所有者 | C1 的处理 |
| --- | --- | --- |
| 操作、批准策略及操作内 `check_ref` | `check_index` 从项目登记的 PolicyRegistry 投影目录，`operation_checks` 按操作编号，现有 Focus wire 校验引用。 | 复用同一目录和引用。每行记录操作、策略、`check_ref`、身份、实例（如适用）和项目快照。目录重排按原 Registry 的规范顺序处理。 |
| 已证明的整项目范围、当前 `source_ref` 和 `source_quote` | `request_atoms` 给原文分界，`project_variant_scope_sources` 识别范围限定子句，`missing_requested_checks` 补充严格闭集规则，`input_sources` 给来源编号。 | 将缺项诊断中原有的范围和来源推导提为 `release_check_source`，原诊断及影子共用。C1 只接受现有闭集支持的两条当前子句，无前轮未决范围。无独立规则证明时不生成映射。 |
| 已确认的结构化局部范围、目标授权、身份可用性 | 项目及选择的身份来自可信输入。权限来自冻结项目与操作员确认，在线身份可用性来自服务端库存事实。 | 核对同一项目摘要、允许操作、选中角色、批准策略和库存。缺确认、已撤回、同一操作同角色多实例、不可用身份或 pending 规则会待审或冲突。结构化范围和自由文本混合时退出该路径。 |
| 精确值和授权变体 | `validate_requested_checks` 验证真实原文、字段和长度形状；`partition_requested_checks` 在现有 `select_negative_approval` 下核实额外授权。 | 有原文的投影通过原校验和分区。纯结构化输入验证同一批准变体规则并保留有无字段的区别；不构造需要 `source_quote` 的对象。具体变体超出权限时保留待审原因。 |
| 计划与执行 | Focus 解析开放语言，Design 结合策略及 IR 构造候选用例。`request_planning_ir` 接受原 Focus 需求；`negative_batches` 决定负向批归属；原执行器、审批和账本处理真实请求。 | 仅在回放进程中比较等价性。编译器没有任务服务、模型工具、Executor 或 SQLite 的调用入口。正式义务、覆盖和实际执行权限继续从原链得到。 |

开放语言中提到“全部”或“发布”不足以证明范围。当前与未来动作并列、否定、撤回、跨轮范围、多角色指代、同一资源或多个合法变体仍需要模型理解或操作员裁决。OpenAPI 描述输入结构，负向业务预期仍引用冻结批准策略。[检查目录、原校验与分区](../src/agenttest/product_checks.py) · [Focus wire](../src/agenttest/product_focus_wire.py) · [来源索引](../src/agenttest/product_sources.py) · [任务范围](../src/agenttest/task_completion.py#L192) · [Planning IR 接点](../src/agenttest/product_tasks.py#L58)

## 输入、输出与边界

- **输入。** `GoalProject` 的只读快照，带项目摘要的 `FocusAvailability`，以及可信当前文本或 `ConfirmedCheckScope`。后者含选择记录 ID、操作员确认记录 ID、项目摘要、操作与角色、可选实例标识、是否包含负向、撤回状态和按策略 ID 绑定的已确认精确约束。调用方负责从可信服务端记录提供这些事实；原型不接收浏览器或模型直接声明的授权，也没有新增确认存储和 API。
- **输出。** `COMPILED`、`NEEDS_MODEL`、`NEEDS_REVIEW` 或 `CONFLICT`，附原因、映射、原规则下的影子可执行分区与待审分区。`executable` 仅指通过映射校验和精确值分区的 `RequestedCheck`，用例构造、请求预检、审批、预算及派发仍有原门禁。一个策略绑定多个角色时，原型保守记录待审；同一操作不同角色且各有独立策略可以映射。
- **原文来源。** 封闭文本的 `source_quote` 来自真实当前子句，记录 `clause_id`、`source_ref` 与项目摘要。纯 UI 选择记录确认和选择 ID，不伪造原文；确定出的行以 `STRUCTURED_SOURCE_REQUIRES_PROTOCOL` 留在待审分区，`executable` 为空。`COMPILED` 表示映射已经确定，UI 路径的正式来源协议仍需另行设计。
- **精确约束。** 默认变体取策略批准值。`exact_value: {"value": null}` 表示明确测试 JSON null，`exact_value: null` 是显式默认标记，省略则为隐式默认；影子序列化保留三者差异。批准的省略必填字段策略与显式 null 策略各有独立 ID，原授权规则拒绝将 null 替换到省略策略。结构化长度走原长度与取值变体校验；带自定义值的开放文本仍留在已有 Focus 路径。C1 不从邻近文字猜测值或长度。

独立实现为 `focus_compiler.py`。`product_checks.py` 仅提取共享的封闭范围来源、就绪条件和精确值变体校验，原缺项诊断和原 partition 从相同位置委托。未修改 Focus wire、Prompt、Planning IR、Design、评分、预算、安全、HITL、清理、SQLite 或 Checkpoint 的执行语义。[影子编译](../src/agenttest/focus_compiler.py) · [共享规则](../src/agenttest/product_checks.py)

## 历史离线回放

从 P3-B2.7 两批、P3-B2.8 一批的原 `model-outputs.jsonl` 读取归档上下文及提案。SQLite 以 `mode=ro&immutable=1` 打开，重建冻结项目并逐轮与原任务项目摘要对齐。独立预期文件登记各操作的批准策略 ID 序列、合法来源及原判定，再用原 Focus 解码器重判模型输出。批准映射预期来自各档案的批准配置，成功提案用于对照原行为。历史失败也全部纳入。复验只执行解码、映射校验和分区；测试适配器拒绝发出任何模型请求。[冻结预期](../tests/fixtures/focus-compiler-history.json) · [回放脚本](../scripts/replay_focus_compiler_c1.py) · [逐调用收据](../runs/focus-compiler-c1/replay-with-provenance.json)

| 历史批次 | 轮数 / Focus 响应 | 原校验接受 | 影子等价 | 历史分母与评分 |
| --- | ---: | ---: | ---: | --- |
| B2.7 首批 | 5 / 8 | 5 | 8/8 | 五轮显式分母 24。原评分 1/5、NO-GO。 |
| B2.7 复验 | 5 / 9 | 2 | 9/9 | 三轮 Focus 停止仍为 0/0，另两轮分母 24。原评分 1/5、NO-GO。 |
| B2.8 | 5 / 8 | 4 | 8/8 | v1 保留 0/0，另四轮分母 24。原评分 0/5、NO-GO。 |

三批均为同一类型的封闭发布请求，25/25 份上下文各得到 24 条映射、24 条影子可执行、0 待审。原解码判定逐份一致，11 份 `SELECTED`、10 份映射缺失、2 份精确值与原文冲突、2 份多余范围子句。11 份接受提案的原分区和影子策略 ID 等价。对这 11 份，保持需求及其余 Focus 字段，仅在内存替换 `requested_checks`，重算的 Planning IR 完全相等；24 项的所属 7 个批次与历史记录匹配。批准目录分母仍为 31。4 份未接受的终止位置未构造原 IR，也未在实际运行中建立请求级分母；旁路计算的 24 与批次归属保存在新证据，不写到这些位置的原报告。[回放与分母](../runs/focus-compiler-c1/replay-with-provenance.json) · 历史原评分（本地归档 `.eduwork/p3b28-acceptance-bec965f/audit.json`）

| 代表位置 | 原模型提案与原判定 | 影子结果 |
| --- | --- | --- |
| B2.7 首批 v1、B2.8 v3 | 24 条、Focus 接受。合法原文来源可用主句或范围说明，v3 使用 `source_2`。 | 策略、操作和身份一致，影子使用可证明的限定子句 `source_3`。 |
| B2.7 首批 v2 初次、B2.8 v5 初次 | 0 条；原校验要求补足 24。 | 24 条均从目录和来源确定。 |
| B2.7 复验 v2 第二次 | 23 条，漏 1；仍拒绝。 | 24 条。 |
| B2.7 复验 v3 | 首次 24 条且单条映射可独立校验，多了 `requirement_map.request_2`；第二次换成 `all_ready` 并漏掉 24 条。 | 映射由可信范围直接生成，保留 24 条。两份原提案的拒绝各自保留。 |
| B2.7 复验 v4 | 23 → 1，按操作/检查键丢失 22 条单独有效映射。 | 24 条，没有依赖重新转写。 |
| B2.8 v1 | 首次 24 条含 10 条不合语义的精确值/长度；第二次保留 14 条合法映射，同时省略应置空字段的 10 条，原校验仍拒绝。 | 输入本身是无精确值的封闭范围，24 条均使用批准默认变体。该结果未覆盖或修正原模型输出。 |
| B2.8 v2 | 首次 24 条合法映射，但多余子句导致拒绝。第二次移除多余子句后通过。 | 只编译映射，未编译或改写 `requirement_map`。 |

## 第二契约与变形验证

实施前冻结观测站契约、六条手工登记的批准负向策略、三种角色、19 个位置与四种变形的正确预期。夹具使用 UTF-8 和 LF 计算的 SHA-256 为 `e2f8a361a9afb1a00b03cbe19711ea2c296a42217ef370c77d9053718fa9c37d`，测试逐次核对冻结摘要。[留出夹具](../tests/fixtures/focus-compiler-holdout.json) · [测试与冻结摘要](../tests/test_focus_compiler.py)

| 组别 | 离线结果 |
| --- | --- |
| 已确定正例 6/6 | 封闭整项目得到六条策略映射；局部 observer 得到四条；同接口不同角色得到五条且保留原角色；公开心跳零批准负向；明确仅正常路径时不生成负向；结构化精确值、长度及 null 类型保真。三种有映射的 UI 选择路径均保留真实确认记录，旧原文分区不执行。 |
| 负控制 13/13 | 4 个开放范围交回模型，涵盖当前与未来并列、撤回、否定与歧义身份；6 个待审覆盖固定策略越值、误用 null、不可用角色、pending 策略、同角色多实例和缺确认；3 个冲突覆盖来源、操作白名单和过期确认。辅助用例覆盖项目摘要漂移、已存在历史范围、借用其他角色或越区策略。 |
| 变形 4/4 | 重排目录时保持规范映射；操作与策略改名后映射跟随新标识；向目录加入一条批准策略变为 7 项；移除一条变为 5 项。名单外及待审检查未进入影子可执行分区。 |

生产影子模块没有包裹、工单、观测站业务名或固定 31/24 数量。历史政策 ID 与轮次仅用于回放预期，事实由当前传入的可信目录计算。

## 生成减负与剩余限制

| 范围 | 模型还需承担的工作 | 已证明可省的生成工作及调用边界 |
| --- | --- | --- |
| 历史封闭整项目 | RequestedCheck 这一步无需再理解范围或选择策略；原 Focus 的需求映射、角色行动、前置依赖和问题仍可能需要模型处理。 | 每份上下文确定 24 条检查，至少对应 48 个必填的策略/来源引用字段，并能由程序提供默认值标记。25 份历史响应实际生成过 435 条条目和 1,419 个条目字段，含失败及修复。条目内容可确定并不等于历史调用可删除。 |
| 留出封闭整项目 | 检查映射本身无需模型选择，其他规划职责按原流程。 | 独立契约可直接生成 6 条映射的 12 个引用字段。已有 Focus 请求里没有独立的「只生成检查映射」调用；本轮实际取消 0 次。 |
| 结构化已确认范围 | 已确认的操作/角色和批准目录确定可映射行。不可用角色、身份冲突或值未批准需给出人工待审。 | 可省相应条目的重复转写。将来需确认真实结构化来源由谁签认、如何持久化、如何与现有任务覆盖兼容，之后才可能省一次只为该结构转写的调用。本轮 `RequestedCheck` 旧协议无可执行输出。 |
| 开放或冲突范围 | 继续需要模型解释，或需要操作员补充、裁决与确认。 | 保持待审路径。不能假设减少调用、token、延迟或成本。 |

缺映射是 10 份历史提案的首个诊断，另外两份把批准默认值当作精确原文的提案也涉及生成负担。还有两个主因是额外范围子句，证明 Focus 的其他部分要独立处理。当前证据支持缩短映射生成和核验范围，实际能避免几次修复、减少多少 token 仍待单独测量。

**建议仅为封闭整项目文本准备正式接管的下一道验证，暂缓打开产品路由。** 接管前需要证明产品输入的可信来源和库存绑定、原 Focus 任一失败下的终态、完整范围及覆盖报告，以及结构化来源的独立协议。先验证单一可证明范围，保持同一原 Validator 与义务来源；后续获准才以独立小样本测真实生成减负。混合文本、未确定身份或任务实例、直接 UI 派发、Design 重构与通用补丁修复继续留在当前试点之外。

## 验证与停点

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/replay_focus_compiler_c1.py
.\.venv\Scripts\python.exe -X utf8 -m pytest -q tests/test_focus_compiler.py `
  tests/test_p3b26_focus_completeness.py tests/test_p3b24_diagnostics.py tests/test_product_sources.py `
  tests/test_planning_ir.py::test_archived_m2_normalizes_eight_clauses_into_four_instances `
  tests/test_planning_ir.py::test_unapproved_operation_is_an_ambiguity -k 'not task_service'
.\.venv\Scripts\python.exe -m ruff check .
git diff --check
```

定向 pytest **80 passed、1 deselected**。首次定向执行发现留出 JSON 的列表需要转换为严格输入元组，修正测试适配后未更改样本和预期。原任务服务集成用例排除在本轮纯离线范围，历史原 Focus 解码通过上述 25 份归档验证。Ruff、差异检查通过；三批归档的 719 个文件在独立回放前后摘要一致，既有产品展示修改保留。[pytest 回执](../runs/focus-compiler-c1/targeted.xml) · [归档完整性](../runs/focus-compiler-c1/archive-integrity.json)

本轮真实模型、业务 HTTP、新五轮验收、全量冻结回归和提交推送均为 0。C1 正式接管、Design 重构和完整 P3-B3 未启动。
