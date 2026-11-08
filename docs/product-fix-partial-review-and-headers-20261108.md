# 产品能力修复：局部待审与必填 Header（2026-11-08）

本文件记录正式隔离读写生命周期验收**之前**的最后一轮产品能力修复。范围只有两个问题：
局部待审与独立操作的职责边界、必填 Header 参数支持。不新增 A/B，不重跑已冻结的真实模型实验，
不启动有状态写入验收，所有写授权保持 `NOT_GRANTED`。

## 1. 结论摘要

| 项目 | 结论 |
| --- | --- |
| 问题一 | 已修复。未批准的**断言**只扣住自己；被独立批准的只读操作继续执行 |
| 问题一保守边界 | 变更操作（POST/PATCH/PUT/DELETE）、带顺序/资源依赖的断言、未被独立批准的读取，仍然整体待审 |
| 问题二 | 已实现必填 Header：OpenAPI 解析 → 审批作用域 → 输入校验 → 计划绑定 → 执行器发送，全链路 |
| 安全检查等级 | 未降低任何等级；待审项不减、不忽略、不改写断言 |
| 写入授权 | `WRITE_AUTHORITY["operator_approval"]` 仍为 `NOT_GRANTED`，`startable()` 仍拒绝启动 |
| 真实模型调用 | 0 次（本轮全部为确定性离线测试） |

## 2. 问题一：局部待审与独立操作

### 2.1 原始证据链（G2 第二轮 baseline）

`docs/evidence/f4-recheck-20261108/positions/G2_r2_baseline/terminal.json` 显示：

1. 模型对 `核对响应的 $.seal 字段` 主动提出了待审问题，并在同一响应里把 `readEntry` 标记为
   `required` 动作；
2. 产品把该断言记为 `response_requirements[0] = {"select": "$.seal", "status": "NEEDS_REVIEW",
   "reason": "RESPONSE_CHECK_NOT_APPROVED", "blocked_operations": ["readEntry"]}`；
3. 该字段被 `product_tasks.py` 读成 `input_blocked_operations`，再从
   `design_groups(... operation_ids=[op for op in selected_ids if op not in blocked_inputs])`
   中被整体剔除；
4. 结果：已批准的 `readEntry` 从未进入计划，整组只发出 1 个请求（`readVoucher`），而不是 2 个。

即：**模型问对了问题，产品把已批准的只读操作一起扣住**。这是产品执行策略错误，不是模型错误。

### 2.2 确定性失败反例

`tests/test_partial_review_scope.py::test_unapproved_assertion_holds_only_itself` 复现该形状
（真实零件：intake、`product_tasks`、`FocusSelection`、`unresolved_response_operations`、
执行器与报告），断言：

- 实际发出的请求是 `/pages` 与 `/health`（`sorted` 后比较），即已批准的 `readPage` 必须执行；
- `input_blocked_operations` 不再包含 `readPage`；
- 断言条目仍是 `status="NEEDS_REVIEW"`、`selector="$.items"`、`blocked_operations=[]`、
  `held_operations=["readPage"]`；
- 汇总 `complete` 仍为 `False`，且待审问题对用户可见。

另外 3 条参数化单测覆盖决策函数的三个分支：读取被独立批准 → 释放；变更操作 → 不释放；
断言挂在不相关的操作上 → 不释放。

### 2.3 修复后的规则

`src/agenttest/requirement_ledger.py::releases_held_operation` 只有同时满足四条才释放：

1. 该断言涉及的每个操作，都被**另一条 `required` 子句独立要求**（`independently_required_actions`）；
2. 断言子句本身不带 `before` 顺序边、`same_resource` 资源链接或 `existing_resources` 固定资源；
3. 引用这些操作的 `required` 子句同样不带上述结构（避免跨越依赖链释放）；
4. 操作方法只能是 `GET`/`HEAD`/`OPTIONS`。

不释放时保持原行为：`blocked_operations` 记录被扣住的操作，`input_blocked_operations` 继续从计划中
剔除它们。释放时写 `held_operations`，`blocked_operations` 置空，断言本身**仍在待审**，
`complete` 仍为 `False`，并额外产生一条可见的 `held_*` 澄清问题（`product_tasks.py`）。

效果：不存在"部分写入"路径——变更操作永远不会被释放，因此不会把系统推入不可恢复状态。

### 2.4 有意改变的既有测试

| 测试 | 旧期望 | 新期望 | 理由 |
| --- | --- | --- | --- |
| `tests/test_requirement_ledger.py::test_explicit_response_cannot_disappear_from_focus`（5 个参数） | 待审断言使整组 0 请求 | 已独立批准的读取照常执行 | 旧期望固化了 2.1 的缺陷 |
| `tests/test_partial_review_scope.py`（新增） | — | 第 2.2 节 | 反例先于修复存在并失败 |

未改变：`tests/test_mixed_planning_round3.py::test_unapproved_content_assertion_still_holds_its_read`
（该断言带 `before` 顺序边与 `same_resource` 链接，按第 2.3 节第 2 条仍整体扣住）、
`tests/test_mixed_planning_gaps.py::test_pending_fixed_resource_only_holds_its_own_read`
与 `tests/test_mixed_planning_p0.py::test_unapproved_data_or_assertions_are_never_replaced`——
它们描述的断言带固定资源/资源链接，行为不变，继续整体扣住。

## 3. 问题二：必填 Header 参数

### 3.1 分层实现

| 层 | 位置 | 行为 |
| --- | --- | --- |
| OpenAPI 解析 | `request_validation.request_schemas` | `in: header` 进入 `parameters["header"]`；头名非法报 `OPENAPI_INVALID_HEADER_NAME`；`Authorization`/`Proxy-Authorization`/`Cookie`/`Host`/`Content-Length` 报 `OPENAPI_RESERVED_HEADER_PARAMETER`；同名大小写冲突报 `OPENAPI_DUPLICATE_HEADER_PARAMETER` |
| 输入校验 | `request_validation.validate_request(..., headers)` | 头名匹配大小写不敏感并回填契约拼写；缺失必填头报 `OPENAPI_PARAMETER_REQUIRED`；未声明头报 `OPENAPI_UNKNOWN_PARAMETER`；值按声明的 schema 校验 |
| 审批作用域 | `policies.PolicyRegistry.resolve` | 每个头必须有它自己的 `BindingRule(location="headers")`，否则 `POLICY_ARGUMENT_MISMATCH`（**绝不隐式注入**）；`closed_arguments` 时头集合必须与规则集合完全一致 |
| 计划绑定 | `dsl.Step.headers` | 新字段；必填头缺失在 DSL 层即报错；登录步骤不得携带头；绑定参与依赖、密钥与引用检查 |
| 实际发送 | `executor._step` | 合并到 httpx `headers`；大小写不敏感地对齐契约拼写；保留头在这里被二次拒绝（`DSL_RESERVED_REQUEST_HEADER`）；身份头始终来自执行器托管的凭据 |
| 证据与脱敏 | `request_validation.public_headers` + `executor` | 敏感头（`x-api-key`、`format: password/token/apiKey`、`x-sensitive: true`）值以 `[REDACTED]` 落库；普通头如实保留；凭据头不进入证据 |
| intake/规划 | `generic_intake.IntakeArgument`、`generic_arguments.argument_slots`、`project_inventory.input_fields`、`product_focus`、`llm_planner` | `header → "headers"` 进入候选槽位与类型约束，模型可为头参数生成字面量或依赖绑定 |

### 3.2 与写夹具的关系

`scripts/rw_lifecycle/write_fixture.py` 的 `Idempotency-Key` 头从此可以直接声明为必填参数；
夹具仍兼容 body `idempotencyKey` 与 query `idempotency_key`，但推荐并优先使用真实服务常用的头传输。

幂等语义由夹具与测试双方固定：同键同体 → 200 且返回同一 id；同键不同体 → 409；不同键 → 新资源。
结果未知的写请求不重放：`tests/test_required_header.py::test_unknown_write_outcome_is_not_replayed`
证明连接中断时只发生 1 次 POST，且报告记录该次尝试的错误码。

### 3.3 验证清单对照

| 要求 | 覆盖 |
| --- | --- |
| 头名大小写不敏感 | `test_required_header_is_validated_case_insensitively`、`test_approved_header_reaches_the_wire_with_declared_spelling`、`test_required_header_survives_openapi_import` |
| 缺失必填头被拒绝 | 同上（`OPENAPI_PARAMETER_REQUIRED`） |
| 未批准的头不得注入 | `test_unapproved_header_is_never_injected`（`POLICY_ARGUMENT_MISMATCH`，0 次请求） |
| 头不得替代认证身份 | `test_header_cannot_replace_the_authorization_identity`（注入 `Authorization` 计划被拒）、`test_approved_header_reaches_the_wire_with_declared_spelling`（身份仍来自执行器凭据） |
| 敏感值在报告中脱敏 | `test_sensitive_header_values_are_redacted`、`test_sensitive_header_never_persists_in_evidence`、`test_ordinary_header_value_is_visible_in_evidence` |
| 幂等键复用返回同一资源、不同键按契约处理 | `test_idempotency_key_replay_returns_the_same_resource`（真实 ASGI 夹具：201 → 200 同 id → 409 → 新 id 201） |
| 结果未知的写请求不自动重放 | `test_unknown_write_outcome_is_not_replayed` |
| 既有 query/body/path 行为不变 | `test_query_body_and_path_behaviour_is_unchanged` + 既有 240 项相关回归全绿 |

## 4. 验证结果

| 命令 | 结果 |
| --- | --- |
| `pytest tests/test_partial_review_scope.py` | 4 passed |
| `pytest tests/test_required_header.py` | 13 passed |
| 相关回归批（executor/intake/planning/ledger/dsl/读写准备） | 240 passed |
| `ruff check .` | 全绿 |
| 完整离线回归（4 进程） | 2480 收集 / 2465 通过 / 0 失败 / 0 错误 / 15 跳过，墙钟 1002.19 秒，测试 ID 覆盖一致（`runs/full-checks/d8892a1a50c94b8e90fec7e3ec062c5d`） |

## 5. 未做与遗留

1. 不启动有状态写入验收；`WRITE_AUTHORITY` 保持 `NOT_GRANTED`。
2. 不重跑任何已冻结的真实模型位置，不新增 A/B。
3. Header 支持不含 `cookie` 参数（仍显式拒绝），也不放松任何 URL/重定向/凭据闸门。
4. 头参数的"值类型"按声明 schema 校验，执行器统一字符串化；数组型头（`style: simple, explode` 的多值）
   仍不支持，会以 `UNSUPPORTED_PARAMETER_ENCODING`/类型检查拒绝，而不是猜测编码。
5. 真实业务接入仍未验证，需要独立环境与单独授权。
6. 兼容性约束：`Step.headers` 为空时不出现在 `model_dump()` 中，因此历史计划指纹与已导出的
   回归资产保持不变；带头的计划是新数据，指纹随之变化，这是预期行为。

## 6. 启动隔离写验收的授权清单

仍需操作员逐条确认（与 `docs/rw-lifecycle-plan-20261108.md` 第 3 节一致）：

1. 批准 6 个操作作为唯一可写范围：`createItem`、`getItem`、`updateItem`、`replaceItem`、
   `deleteItem`、`listItems`；
2. 确认目标仅为 `127.0.0.1:19097` 隔离夹具，环境标记 `isolated`、`allow_mutations=true`、
   `isolated_confirmed=true`；
3. 确认每次运行的唯一命名空间与重置方式（`POST /__namespace`、`POST /__reset`，均需控制令牌）；
4. 确认清理策略：每次创建配对一次 DELETE，并以回读 404 作为清理证据；
5. 确认故障注入只用于补偿测试，结果未知的写请求一律不重放；
6. 确认预算上限与停止条件（建议 `max_requests=12`、`max_seconds=120`、`max_model_calls=2`）。

推荐参数：幂等键走 `Idempotency-Key` 必填头；身份 `author`/`editor`/`auditor`/`outsider`；
只跑 1 条创建→读→改→替换→删除、1 条幂等重放、1 条越权对照；任何 UNKNOWN 立即停止并原样保留。
