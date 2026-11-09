# 第二次隔离 API 读写生命周期验收（2026-11-09）

起点提交 `520495a`，冻结提交 `3f9cdae`（干净工作树），目标 `http://127.0.0.1:19097`
（本地 `isolated` 夹具），模型 `ecnu-max`。三条首次位置各调用一次真实模型规划，未重跑、
未换题、未改评分。

## 1. 裁决

| 用例 | 裁决 | 目标 HTTP | 真实模型调用 | 根因类别 |
| --- | --- | --- | --- | --- |
| T1 完整生命周期 | **FAIL** | 0 | 1（KNOWN，11592 tokens） | 产品缺陷（设计阶段输入约束） |
| T2 幂等性 | **NEEDS_REVIEW** | 0 | 1（**UNKNOWN**，30.03 s 超时） | 模型服务超时，用量未知 |
| T3 身份隔离 | **NEEDS_REVIEW（未执行）** | 0 | 0 | 被 T2 的 UNKNOWN 用量闸门拦下 |

三条都没有产生任何真实业务写入：目标审计为空，`requests=0`，资源残留 0，状态哈希保持初值。

## 2. T1：产品在设计阶段拒绝了已批准取值

Focus 阶段完全成功：一次 `ecnu-max` 调用（10390 / 1202 / 11592 tokens，HTTP 200，
响应 5565 字节），结构化输出把 7 条原始子句全部映射为 `required`，`before` 顺序边、
`same_resource` 边、批准输入值（`first`、`renamed`、`replaced`、`Idempotency-Key`）
齐全，无 `needs_review`、无缺口证据请求。

随后设计调用的用量槽记录 `NOT_SENT`、`request_bytes=0`、
`diagnostic={layer:openapi, code:OPENAPI_VALUE_ENUM}`：请求发出前就失败。用归档的
Focus 原文离线重放（`runs/_probe_t1_preflight.py`，屏蔽网络）复现了完整堆栈：

```
product_design.prepare_request → product_schema.constrain_product_steps
  → product_inputs.constrain_input_schema → validate_value → OPENAPI_VALUE_ENUM
```

根因：同一个 `createItem.name` 现在有两个已批准变体——基础策略枚举
`["first","second"]` 与冲突预期策略枚举 `["second"]`。设计阶段把**基础策略**的
已批准值 `first` 拿去对该**冲突策略**更窄的闭合枚举做校验，于是被拒。

离线修复（产品侧、通用、不放宽校验）：

- `product_inputs.approved_values()`：按 `(operation, location, name, path)` 汇总各变体
  的已批准取值并集；
- `constrain_input_schema()`：一个取值只绑定到**承认它**的变体；若它被同一槽位的另一
  变体批准，则跳过该变体的枚举收窄，而**任何变体都不批准的取值仍然报错**
  （`OPENAPI_VALUE_ENUM` 反向测试保留）；
- `product_schema.constrain_product_steps()` 传入该并集。

修复前后确定性对比（同一归档 Focus 原文、零模型调用、零目标 HTTP）：修复前设计请求
字节 0 并抛 `OPENAPI_VALUE_ENUM`；修复后设计请求准备完成（44471 字节）并到达发送点。
新增 2 项回归测试（`tests/test_focus_approved_expectations.py`）。

另发现产品缺陷（未在本轮修改，建议下一步）：设计被拒后任务仍以 `FINISHED` 收尾且
无 `report.json`，没有把设计诊断升级为 `NEEDS_REVIEW`；驱动侧因此只能凭空审计判 FAIL。

## 3. T2：模型服务超时，用量未知

提交后 Focus 调用在 30.03 s 超时（`http_status=null`、`response_bytes=0`），
用量日志记为 `UNKNOWN`，任务 `NEEDS_REVIEW`，目标 HTTP 0 次。没有响应内容可评分，
因此不能判定为产品缺陷或模型规划失败；驱动打印的 `FAIL` 只是空审计的派生结果，
真实裁决按基础设施计为 **NEEDS_REVIEW**。

遵守"任意 UNKNOWN 立即停止"：未重放该写入、未重试该模型调用、未估算 token。

## 4. T3：未执行（用量闸门）

T2 的 `UNKNOWN` 用量行未对账，`TaskStore.begin_model_call` 以
`MODEL_USAGE_REQUIRES_RECONCILIATION` 拒绝新的模型调用，T3 在提交阶段即被拦下：
无模型调用、无目标 HTTP、无资源。该位置因此**未被消费**，但也不能在未对账前执行。

恢复方式（需操作员决定其一，本阶段均未执行）：

1. 用供应商用量回执调用 `reconcile_model_usage`（校验 `request_sha256`
   `346ced4f…` 与任务 `8e3fa3b4…`）；
2. 或登记一次明确的用量例外（`model_usage_exceptions`），保留原始 UNKNOWN 证据不变。

对账后 T3 可在**新的**冻结运行中执行一次。

## 5. 安全与完整性

- 授权：`startable()` 使用本次运行的显式授权副本 `operator_approval="GRANTED"`；
  运行结束仓库默认 `WRITE_AUTHORITY=NOT_GRANTED` 已恢复，测试服务已停止，19097 端口无监听。
- 命名空间：`e2e-t1-…`/`e2e-t2-…` 独立；`/__namespace`、`/__reset` 仅测试编排器持有控制令牌。
- 未出现越权、未出现未批准状态码、未出现 UNKNOWN以外的用量异常、无资源残留。
- 证据脱敏：`docs/evidence/rw-lifecycle-20261109-v2/` 中扫描不到 API Key、Bearer、
  测试身份令牌或 Authorization 头（`evidence.json.redaction.scan == []`）。
- 预算：T1 目标 HTTP 0/12、模型 1/2；T2 目标 HTTP 0/12、模型 1/2（未知）；
  T3 目标 HTTP 0、模型 0；总 KNOWN 用量 11592 tokens（10390 输入 / 1202 输出）。

## 6. 证据与冷复算

`docs/evidence/rw-lifecycle-20261109-v2/`：

- `freeze.json`：冻结提交、源码 SHA、需求原文与 rubric、批准配置、模型参数、目标初态、预算；
- `dry.json`：12 条批准策略、枚举与必填头绑定；
- `result.json`：三条位置的完整审计、Focus 原文（SHA-256 + 结构化解析）、评分发现；
- `evidence.json`：裁决汇总、用量日志、离线复现与修复记录、脱敏扫描结果。

冷复算：`python scripts/rw_lifecycle/acceptance.py dry runs/<new>` 复现声明；
`python runs/_probe_t1_preflight.py`（归档路径存在时）复现 T1 的设计拒绝与修复后行为。

## 7. 下一步

1. 先修"设计被拒仍以 FINISHED 收尾"的产品缺陷（应有 `NEEDS_REVIEW` + 诊断）；
2. 由操作员决定 T2 未知用量的对账或例外登记；
3. 对账后用新的冻结运行执行 T3 一次（不重跑 T1/T2 的已消费位置）；
4. 若需重测 T1/T2 的判定，必须使用**新的**位置与新的冻结，不复用本轮结果。
