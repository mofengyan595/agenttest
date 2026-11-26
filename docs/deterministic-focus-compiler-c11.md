# C1.1 确定性 Focus 编译接管候选

2026-11-24 · 工作区基线 `bec965fba0d7f5c039812cf94d7567b765747af3`

本轮把 C1 编译器接到实际产品任务的 Focus 入口。候选按项目显式启用，适用范围限于已有可信规则唯一证明的封闭整项目文本。P3-B2.8 原评分保持 **0/5、NO-GO**，复杂自主发布仍采用原 **4/5** 门槛。[长期路线图](ROADMAP.md) · [C1 影子证据](deterministic-focus-compiler-c1.md) · [历史真实验收](deepagents-model-reliability-p3b28.md)

## 范围与职责

| 决策 | 接管候选的处理 |
| --- | --- |
| 是否适用 | 服务端在模型请求编码前，从当前原文、冻结项目范围和库存建立证据。复用 `project_variant_scope_sources` 和 `release_check_source`，仅接受原有严格的闭集形态。无前轮范围，且批准操作及其正常动作身份具备就绪证据。 |
| 已批准的负向映射 | `compile_requested_checks` 使用同一 PolicyRegistry、操作白名单、可信身份和目录。每条映射见证包含操作、策略、角色、`check_ref`、当前原文、`source_ref`、子句 ID、项目摘要和变体类型。被挂起的检查阻止接管，原因保留。 |
| Focus 开放语义 | 模型继续解释需求、分配正常动作的获准身份、处理资源关系及顺序，按来源填写 `requirement_map`。候选 wire 声明 `compiled_release_v1`，操作表仍保留输入、问题、正常行为及适用的资源来源字段。 |
| 可信合入 | 候选 wire 没有 `requested_checks` 字段。模型提交同名字段、改变已证明的范围、选用越权身份或改选资源来源时拒绝。语义提案匹配后才合入服务端映射，仍执行原 Focus 来源、授权、精确值、需求链接与分区校验，再进入同一 Planning IR、Design 和执行门禁。 |

候选只负责严格闭集原文中的**批准默认负向变体**。明确的值、长度、JSON null、局部范围、当前与未来任务并列、撤回、跨轮条件、开放语言及结构化 UI 来源继续使用原路径或按原规则待审。保存真实文本来源，纯 UI 选择继续使用 C1 影子结果中的确认记录。本轮沿用原审批、执行、预算、清理、持久化和未知写入规则。[Focus 入口](../src/agenttest/product_focus.py) · [Focus wire](../src/agenttest/product_focus_wire.py) · [原检查校验](../src/agenttest/product_checks.py) · [任务及 IR 入口](../src/agenttest/product_tasks.py)

## 开关、证据及回退

- 通用项目接入默认采用原协议。受控部署可以将服务端 `AGENTTEST_FOCUS_COMPILED_RELEASE_PROJECT` 指定为单个已接入项目的 ID。任务提交时将协议版本写入可信 SQLite 任务上下文。项目授权摘要沿用原定义，文本及模型没有配置权限。
- 已启用项目在持有原文范围和就绪证据时采用候选协议。推导不适用或待审时，在派发 Focus 请求前进入原协议并记录 `FALLBACK`。可信目录与权限冲突时在模型调用前拒绝。候选提案发生语义或职责冲突时保留拒绝诊断，省略映射的能力不能覆盖冲突。
- 任务摘要 `design_records[].check_compilation` 记录协议、适用性、项目摘要、状态及映射见证。 `APPLIED` 表示 Focus 合入后通过校验，覆盖和完成状态仍由原执行证据决定。 `REJECTED` 下的映射只作为诊断见证。原 `summary.requested_checks` 继续是经过 Focus 校验的投影。
- 关闭开关后新任务回到原协议。在开关改变前已提交的任务继续使用其记录的协议。项目快照、固定回归资产、原审批绑定、HTTP 回执、Checkpoint 与预算账本继续使用原有校验和持久化。[任务协议和快照绑定](../src/agenttest/product_tasks.py) · [任务上下文](../src/agenttest/task_store.py) · [固定回归](../src/agenttest/saved_tests.py)

## 端到端离线证据

- **原始项目路径。** 工单项目经正常接入和任务提交走到 Focus 与 Design。候选合入的映射数量和批准目录一致。Design 收到可核对的实际请求后，由 Mock 返回不合格提案，最终没有业务派发。
- **第二契约。** 按 C1 冻结的观测站契约、身份和批准目录，通过 `TaskService.submit`、实际 Focus/Design 编码和脚本化 Deep Agents 比较旧协议及候选。测试适配从冻结 OpenAPI 补齐 Focus 编码需要的绑定字段类型，并使用通用任务路由的内部场景标识。冻结负向策略、角色与预期集合保留。原契约及改名、重排、增删与零负向策略共六组，逐组核对映射、Planning IR、分组、归属、覆盖分母及 Design 请求。映射按策略 ID 比较，行顺序来自各自投影。
- **边界。** 当前与未来并列、撤回、否定、局部、身份含糊、来源冲突文本、精确值及长度，以及跨轮撤回，在候选调用前进入原协议。缺少身份凭据的项目由既有入口拦截。批准目录和写入许可冲突时调用模型次数为 0。额外映射、改报模式、关闭正常动作、选择越权身份、改选资源来源均拒绝。启用与停用两个方向均验证了已待审批任务保持其原协议，运行记录继续关联同一项目摘要。
- **确定性运行分支的限制。** 观测站的开关切换用例在待审批后继续运行。两臂均通过 Focus，随后原确定性 Design 分支以 `DESIGN_REQUIRED_IDENTITY_NOT_APPROVED` 停止。六组进入 Design 编码的对照使用脚本化 Deep Agents，确定性分支的多身份 Design 行为留在原范围。
- **历史证据。** 三批 15 轮、25 份原提案按原路径再校验，原判定一致。11 份原合法提案按候选 wire 离线回放，保留语义字段并由编译器生成映射，Planning IR 11/11 等价。归档中其余 14 份拒绝维持原判定，三批原分仍为 1/5、1/5、0/5，均为 NO-GO。[离线测试](../tests/test_focus_takeover.py) · [冻结留出](../tests/fixtures/focus-compiler-holdout.json) · [历史原路径回放](../scripts/replay_focus_compiler_c1.py)

## 请求与生成负担

以下是实际送入 Focus MockTransport 的 UTF-8 请求体大小，字段数统计每条负向行的 `check_ref`、`source_ref`、`exact_value` 及按策略出现的 `exact_length`。每个被选中操作原有的 `requested_checks` 数组键也从候选 wire 移除。字节差异同时包括 Prompt 简化、已确定范围的 schema 约束及负向目录移除。

| 第二契约样本 | 请求字节 原 → 候选 | 模型负向行 原 → 候选 | 行内字段 原 → 候选 | 编译行 |
| --- | ---: | ---: | ---: | ---: |
| 冻结原例 | 31,001 → 13,237 | 6 → 0 | 19 → 0 | 6 |
| 改名 | 30,929 → 13,185 | 6 → 0 | 19 → 0 | 6 |
| 目录重排 | 31,001 → 13,237 | 6 → 0 | 19 → 0 | 6 |
| 新增批准策略 | 31,791 → 13,237 | 7 → 0 | 22 → 0 | 7 |
| 移除批准策略 | 30,102 → 13,237 | 5 → 0 | 16 → 0 | 5 |
| 零负向策略 | 25,157 → 13,237 | 0 → 0 | 0 → 0 | 0 |

六组每臂各有一次 Focus、一次 Design、四次脚本化 Agent 模型调用。候选减少了生成工作，离线调用次数差为 0，真实 token、延迟、费用和拒绝率仍待实测。六组批准义务覆盖分母及 Design 入参相等，送入校验的 Design 提案被拒绝，实际执行分子为 0。零负向组仍有正常动作和 Focus 语义职责。

11 份历史合法提案用 `chatecnu` 兼容编码配对，使用离线模型名并在内存中去掉旧协议的修复反馈，将两臂统一为首轮请求。请求体每份 **47,362 → 25,433 字节**，模型映射每份 **24 → 0 行**，行内字段 **78–80 → 0**。这是可重复的协议对照，原历史网关请求及评分均保留。

在专门选取的 22 个入口对照位置中，6 个采用候选、9 个调用前回到原协议、6 个在任务中拒绝、1 个在项目入口因缺凭据被拒绝。六个接管正例的检查分区中没有待审行。九个回退位置只验证分流，用原协议的故障替身结束任务。样本刻意加入了负控制，比例不代表日常任务。

## 复现与停点

标准 CI 使用 Git 内的合成夹具验证编译和任务入口。只有 `test_archived_legal_semantics_through_new_wire` 和独立历史回放脚本需要三批本地 `.eduwork/` 归档。标准检出中仅该历史用例因归档未安装而跳过，合成夹具的入口对照在 Deep Agents CI 运行。

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_focus_takeover.py tests/test_focus_compiler.py `
  tests/test_product_focus.py tests/test_p3b26_focus_completeness.py tests/test_planning_ir.py `
  tests/test_product_tasks.py tests/test_product_durability.py
.\.venv\Scripts\python.exe scripts/replay_focus_compiler_c1.py --archives .eduwork
.\.venv\Scripts\python.exe -m ruff check src/agenttest/product_focus.py `
  src/agenttest/product_focus_wire.py src/agenttest/product_tasks.py tests/test_focus_takeover.py
git diff --check
```

最终定向集 **159 passed、1 个既有 xfailed**，其中新增 C1.1 用例 **26/26**。既有预期失败是原 P2.1 英文精确输入诊断用例。Ruff、相对链接及差异检查通过。三批历史归档分别核对 324、193、202 个文件的 SHA-256，前后共 719 个文件一致。实际真实模型和业务 HTTP 都为 0，本轮未运行全量冻结回归、五轮验收或 Git 写操作。

后续如单独获准，可在隔离环境用小样本先对照真实 Focus 的结构化输出、语义、拒绝原因、已知用量和延迟。两臂使用同一冻结任务及验证标准，先停在 Focus 输出或 Design 派发前。若出现授权或来源冲突、IR 或覆盖分母漂移，停止候选。真实模型成功率和成本仍缺证据，默认发布配置继续关闭。本轮到离线候选为止。
