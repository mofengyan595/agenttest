# 第二次读写验收后的恢复与离线修复（2026-11-09）

范围：只做恢复与离线修复，不启动真实写入，不重跑 T1/T2 已消费位置，不运行真实模型验收，
未访问生产或 CourseInsight。起点 `710dd85`。

## 1. 设计被拒却以 `FINISHED` 收尾（已修）

原先 `product_tasks.run` 在试用完全部设计分组后**无条件**把任务从 `RUNNING` 转为
`FINISHED`；当所有分组都在设计校验阶段被拒（T1 的 `OPENAPI_VALUE_ENUM`）时，任务既没有
用例也没有报告，前端只能看到"已结束"，失败被静默。

修复（`src/agenttest/product_tasks.py`）：

- 结束前检查"设计记录全部为 `REJECTED` 且没有任何用例、没有任何目标请求"；
- 满足时改为 `RUNNING → NEEDS_REVIEW`，`summary` 保留
  `reason`（方案未通过校验，本轮没有发送任何业务请求）、`diagnostic{code,layer,fields}`、
  `complete=False`、`checks_passed=False`、`report_id`；
- 通过新增 `failure_report()` 发布一份零步骤 `RunReport`（`outcome=NEEDS_REVIEW`、
  `reason`/`diagnostic` 为拒绝码、`plan_valid=False`），并写入 `REPORT` 事件，供证据与前端读取；
- 正常空轮（`no_current_action`、`relevant=false`）与既有 `FINISHED` 路径不变。

确定性回归 `tests/test_product_rejection.py`（无网络、无模型）：

1. `test_design_rejection_is_reported_and_never_finished`：设计抛出
   `ValidationFailure(Layer.OPENAPI, "OPENAPI_VALUE_ENUM")` → 断言状态 `NEEDS_REVIEW`、
   `diagnostic.code/layer`、`design_records[0].reason`、`complete/checks_passed=False`、
   失败报告 `outcome/reason/diagnostic` 与空步骤、用量未被标记 UNKNOWN；
2. `test_unrejected_round_still_finishes`：无拒绝的轮次仍为 `FINISHED`，新分支没有把正常
   轮次降级。

## 2. `c61c704` 的输入枚举变体修复复核

修复面：设计阶段的枚举收窄只在**承认该取值的变体**上生效；执行阶段仍按每个策略自己的
闭合参数校验。三类边界都有确定性回归：

| 场景 | 期望 | 测试 |
| --- | --- | --- |
| 基础策略批准值 `first` 在基础策略下被接受 | 通过 | `test_variant_values_are_admitted_by_their_own_approved_set_only` |
| 冲突策略只批准 `second`，用 `second` 执行 | 通过 | `test_execution_still_rejects_a_value_used_with_the_wrong_variant` |
| 用冲突策略执行 `first`（跨变体滥用） | 拒绝 `OPENAPI_VALUE_ENUM` | 同上 |
| 任何变体都不批准的 `third` | 拒绝 `OPENAPI_VALUE_ENUM` | 同上 + 设计阶段反向用例 |
| 无 `enum` 的开放规则 | 保留请求值 | `test_variant_values_are_admitted...` |
| 并集来自同一槽位各变体 | `["first","second"]` | `test_approved_values_union_covers_every_declared_variant` |

结论：修复只放宽"设计阶段对已批准取值的重复校验"，未放宽执行阶段；跨变体滥用与未批准
取值仍被拒。

## 3. T2 的 `UNKNOWN` 用量：无可核验回执，保持阻断

归档运行库 `runs/rw-acceptance-v2-20261109/workspace/tasks.sqlite3` 现状（只读核对）：

```
51ca0125cd2b4e469f0de0db49943eed  8e3fa3b4282748d6a8f2c0d0aff2adcd  UNKNOWN
  calls 1  http_status null  request_sha256 346ced4f4f9b9b41ab5d5b543a782075d103bddf9ecf1e4fb39fae25343d39ec
  elapsed_ms 30031.0  total_tokens null
reconciliations 0   exceptions 0   model_usage_blocked True
```

对账要求（`ProviderUsageReceipt` 全字段）：`call_id`、`task_id`、`request_sha256`、
`source=OPERATOR_VERIFIED_PROVIDER_RECORD`、`provider_reference`、`evidence_sha256`、
`input_tokens`、`output_tokens`、`total_tokens`（可选 `provider_confirmed_not_received`）。

本次调用在 30.03 s 被本地超时终止、无响应体、无供应商回执，仓库与运行档案中也没有任何
供应商侧用量凭据，因此**不存在可核验回执**：保持 `UNKNOWN`、保持用量阻断，未登记用量
例外、未估算 token、未重放请求。

**需要操作员决定**（二选一，均需明确书面授权后由后续阶段执行）：

1. 提供供应商侧可核验记录（含上述字段与证据哈希）→ 走既有 `reconcile_model_usage` 流程；
2. 明确授权对该 `call_id` 登记一次用量例外，并接受该次调用用量永久不可知。

在收到决定前，任何新的真实模型调用都会被 `MODEL_USAGE_REQUIRES_RECONCILIATION` 拒绝。

## 4. 30 秒单次模型超时与 120 秒用例预算

现状（`src/agenttest/llm_planner.py`）：`ModelSettings.timeout` 默认 **30 s**，字段上限
**120 s**，此前只能由构造参数设置，`.env` 只允许三个键。观测：T1 Focus 28.66 s 成功，
T2 Focus 30.03 s 超时——供应商延迟正好压在默认上限上，30 s 是这次失败的直接约束。

关系：用例预算 120 s（`authority.WRITE_AUTHORITY["budgets"]`）要容纳"Focus + Design 两次
模型调用 + 最多 12 次目标 HTTP + 校验开销"。两次 30 s 调用理论上还剩 60 s 余量，但只要任一次
接近上限，超时就成为常态；而超时按产品规则**不可重试**且会把用量记为 `UNKNOWN`，一次抖动
就会阻塞后续所有调用。

本次改动（有上限、可审计，未改变默认行为）：

- 新增 `AGENTTEST_MODEL_TIMEOUT`（进程环境或模型 env 文件），经
  `model_timeout()` 解析并受字段 `(0, 120]` 约束；非法值直接报错，不静默回退；
- 冻结记录 `model.timeout` 与 `max_input_tokens`（`acceptance.model_identity`）；
- 驱动新增 `driver_case_budget_exceeded`：单用例实际耗时超过
  `budgets.max_seconds` 即记入发现项；
- 默认仍是 30 s，未擅自放宽任何预算；`.env` 未修改。

**建议配置（待操作员批准后写入授权副本）**：单次调用 `AGENTTEST_MODEL_TIMEOUT=45`
（在 120 s 字段上限内），单用例墙钟预算 150 s（2×45 s + 60 s 校验/HTTP 开销），模型调用仍
≤2 次、目标 HTTP 仍 ≤12 次；超时依旧不可重试、记 `UNKNOWN` 并停止。若操作员更倾向保守，
可保持 30 s 并把用例预算维持 120 s，代价是慢响应仍会频繁超时。

## 5. 测试

- 定向：`tests/test_product_rejection.py`、`test_model_timeout_budget.py`、
  `test_focus_approved_expectations.py`、`test_rw_lifecycle_preparation.py`、
  `test_product_tasks.py`、`test_product_design.py` 等 8 个文件 **127 passed**；
- 全量离线（`scripts/check_full.py --output runs/full-checks-recovery`，四个批次）：
  **2507 项，2492 passed，0 failed，0 errors，15 skipped**，
  `successful=true`、`exact_test_id_coverage=true`，墙钟 1213.6 s；
  本轮新增 17 项确定性测试（拒绝收尾 2、超时/预算 9、变体准入与滥用 2、原有批次内其余）；
- `ruff check .` 通过。

## 6. 安全状态

- `WRITE_AUTHORITY["operator_approval"]` 仍为 `NOT_GRANTED`；未调用 `startable()`；
- 19097 端口无监听，夹具未启动；未运行 T1/T2/T3；
- 本次改动不产生任何目标 HTTP、不产生模型调用；T2 的 `UNKNOWN` 保持原样。

## 7. 下一步

1. 操作员就第 3 节做出决定（回执或例外）；
2. 决定后就第 4 节批准超时与预算数值，写入授权副本并在冻结记录中体现；
3. 上述完成后再安排新的冻结运行执行 T3（T1/T2 位置不复用）。
