# Focus 规划与测试声明修复（2026-11-09，离线阶段）

## 1. 结论

三条首次验收失败各有独立的代码根因，均已在产品侧做最小通用修复，没有放宽任何安全校验：

| 用例 | 原诊断 | 代码根因 | 修复 |
| --- | --- | --- | --- |
| T1 | `FOCUS_REQUIREMENT_GAP_WITHOUT_EVIDENCE` | 声明无法表达“删除后回读 404”：`getItem` 只批准 200，模型只能把该子句标为 `needs_review`，而校验器要求未解决子句必须有已识别的 gap 证据 | 新增“批准的预期结果策略”（`IntakeExpectation`）：同一操作、同一身份可声明非 2xx 预期（404/409 等），必须在 OpenAPI 中声明该状态码，模型据此把子句映射为 `required` |
| T2 | `FOCUS_INPUT_TEXT_CHANGED` | 批准生成输入只允许一个取值（`first`），同键异体需要的第二个取值无法获批；模型改写已批准值被正确拒绝 | `IntakeArgument.values` 提供**受控的多个批准取值**，策略变成闭合枚举；越界取值新增诊断 `FOCUS_INPUT_VALUE_UNAPPROVED`；同键异体用“冲突预期策略”表达 409 |
| T3 | `FOCUS_REQUIREMENT_WITNESS_MISSING`（`requirement_map.request_3.before`） | 子句由“然后/再”衔接时校验器要求模型复述 `before` 边，但顺序证据本就来自原文子句顺序 | 新增 `textual_order_edge`：有序子句由原文顺序确定性补齐相邻 `before` 边（重复读、已有边、无前置动作时不补）；缺失且不可推导的见证仍被拒绝 |

另外新增“受控写操作负向权限声明”：`access_checks` 现在可用于写操作，但**仅当** `approve_negative_tests` 开启、状态码 ⊆ {401, 403}、且角色与基础策略身份不同；普通写入权限没有扩大。

## 2. 修复前的离线失败反例

原始真实模型响应体未持久化（`model_usage_journal` 只保存请求/响应 SHA-256 与 token 统计），因此无法逐字重放三条响应。修复前的状态由两类材料固定：

1. **声明层**：旧 `authority.setup()` 只有 8 条策略，没有 404/409 预期，`name` 只有单值 `first`；`tests/test_focus_approved_expectations.py::test_declaration_matches_the_next_round_plan` 需要 11 条策略，旧声明必然缺少其中 3 条（`createItem` 409、`getItem` 404、`updateItem` 403/401）。
2. **响应形状**：三条真实诊断码与字段（`FOCUS_REQUIREMENT_GAP_WITHOUT_EVIDENCE`；`FOCUS_INPUT_TEXT_CHANGED` 的 `input_requirements.2.value`、`approved_input_fields.0`；`FOCUS_REQUIREMENT_WITNESS_MISSING` 的 `requirement_map.request_3.before`）保存在 `docs/evidence/rw-lifecycle-20261109/evidence.json`，离线反例以此为准。

## 3. 修复后的确定性行为

- 声明构建：12 条策略——`createItem` 201 + 409、`getItem` 200/403/401/404、`updateItem` 200/403/401、`replaceItem` 200、`deleteItem` 204、`listItems` 200；`name` 枚举 `["first","second"]`，`Idempotency-Key` 仍为必填头且只有单一批准键。
- 确定性预检（无模型、无真实授权）：`scripts/rw_lifecycle/preflight.py` 原有 10 条控制保持 7 PASS / 3 设计性 FAIL；新增 3 条下一轮 rubric 控制全部 PASS：
  - `v2_approved_alternatives_and_conflict`：`201, 200, 409, 200, 204, 404`，重放复用同一 ID，冲突后资源不变；
  - `v2_identity_denials_without_state_change`：`201, 200, 403, 401, 403, 401, 204, 404`，四次拒绝均未改变状态；
  - `v2_owner_replacement_still_allowed`：`201, 200`（所有者替换仍正常）。
- 下一轮声明就绪检查：`scripts/rw_lifecycle/plan_v2.py::readiness()` 要求 11 条策略，当前全部满足（`acceptance.py dry` 的 11 项检查同为全绿）。

## 4. 安全回归

新增 `tests/test_focus_approved_expectations.py`（10 项）：

- 未批准取值 → `FOCUS_INPUT_VALUE_UNAPPROVED`；自由文本不保留原文 → 仍 `FOCUS_INPUT_TEXT_CHANGED`；
- 冲突预期改成 201 → `EXPECTATION_STATUS_NOT_APPROVABLE`；未在契约声明的 409 → `EXPECTATION_STATUS_NOT_DOCUMENTED`；预期值不在批准集合 → `EXPECTATION_VALUE_NOT_APPROVED`；无 `approve_negative_tests` → `EXPECTATION_REQUIRES_APPROVED_NEGATIVE_TESTS`；
- 写操作负向权限：关闭批准标记、角色等于基础身份、状态非 401/403 → 均 `ACCESS_CHECK_REQUIRES_APPROVED_READONLY_ROLE`；
- 顺序见证：有序+无前置动作 / 已有边 / 重复读 / 无动作 → 都不补边（不发明证据）。

既有相关回归：声明、输入、Focus、需求台账、回归指纹、Petclinic 链路、夹具准备共 **215 项通过**。
完整离线回归（4 进程）：**2490 收集 / 2475 通过 / 0 失败 / 0 错误 / 15 跳过**，墙钟 920.06 秒，
测试 ID 覆盖完全一致（`runs/full-checks-focus-fixes`）；`ruff check .` 全绿。

## 5. 下一轮首次验收方案（待重新批准）

- 需求集：`scripts/rw_lifecycle/plan_v2.py::FINAL_CASES`（T1/T2/T3 v2），正文与 `RUBRIC` 逐条对应，每个动作只出现一次，不引入 rubric 之外的多余动作；
- 开发样本：`DEV_CASES` 只用于离线预检与测试，不参与首次验收计数；最终用例未经真实模型筛选；
- 批准配置：`scripts/rw_lifecycle/authority.py`（12 条策略、`assisted=True`、`approve_negative_tests=True`、必填头，404/409/401/403 全部在契约中声明）；
- 评分器：`scripts/rw_lifecycle/lifecycle.py` 新增 `conflicts` 指标与 `conflict_count_mismatch` 发现、`approved_denials` 白名单（批准的 401/403 不再误报身份违规）；
- 预检：`preflight.py` 的 13 条控制 + `acceptance.py dry` 的 11 项声明检查；
- 需要重新批准的六项授权与操作范围：仅 `127.0.0.1:19097`、六个批准操作、三条验收路径、每用例 12 次目标 HTTP / 120 秒 / 2 次模型调用。本阶段未授予写入，`WRITE_AUTHORITY` 保持 `NOT_GRANTED`。

## 6. 本阶段边界

未修改 CourseInsight、未访问生产环境、未发送任何真实业务写入、未重跑已消费的三条首次位置；真实模型调用 0 次。
