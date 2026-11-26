# 负向请求二次校验保留请求头

## 缺陷及影响

Executor 已解析批准的普通请求头，并在第一次 OpenAPI 校验中使用它们。负向策略随后调用 `mutate_request` 对同一合法基线复验，却只传入 path、query、body 和媒体类型。冻结资产登记契约把 `X-Audit-Key` 标为必填，因此 B02/B03 在第二次校验触发 `OPENAPI_PARAMETER_REQUIRED`，派发数为零。此前三臂的共同失败来自同一执行缺陷。

新增独立回归使用另一个已有测试项目和批准的 Header 绑定。修复前，缺必填字段、显式 null、错误业务码三个本应到达 Transport 的位置都被第二次校验阻断，三项拒绝控制照常通过。

## 修改

Executor 向 `mutate_request` 传递已解析、规范化并通过第一次验证的 `request_headers`。二次校验使用同一请求上下文，随后仍只修改批准的 body 或 query 字段。认证注入保留在原有位置，保留 Header 保留字、闭合绑定、凭据检测和精确值保护。

逐项核对两次校验的参数。两处已共用解析后的 path、query、body 和媒体类型，资源引用在第一次校验前解析。本轮未发现需要一并修改的第二处上下文缺失。

## 定向复验

使用原冻结资产契约与请求预期，沿原 A0 提案解码、编译、预检与 Executor 执行。每次仅选择对应位置，使用内存 Transport 和原审批逻辑。

| 位置 | 发出时的字段语义 | 业务回执 | 结果 | 内存派发 |
| --- | --- | --- | --- | --- |
| B02 | `title` 缺失 | HTTP 422 和批准业务码 | PASS，前后观察确认无副作用 | 3 |
| B02 对照 | 同上 | HTTP 422 和错误业务码 | ASSERTION_FAILED | 2 |
| B03 | `notes` 存在且值为 JSON null | HTTP 422 和批准业务码 | PASS，前后观察确认无副作用 | 3 |
| B03 对照 | 同上 | HTTP 422 和错误业务码 | ASSERTION_FAILED | 2 |

两条错误回执的 HTTP 状态断言均成立，业务码断言失败。独立回归同时覆盖缺失 Header、额外未批准 Header 和未经批准的 Header 值，三项均为零派发。原精确负向值保护继续阻止偷换违规值的测试替身。原负向用例、精确值、前置条件与 POST 观察定向测试 **77 passed**。同项工作中的 C2 入口测试另外验证审批前零派发、预算不足、同关联键清理证据及未知写防重放。

本次复验共 10 次内存派发，真实模型和真实网络 HTTP 均为零。复验收据位于本地 `.eduwork/negative-context-recheck/summary.json`。原 PoC 的 **18/20**、三臂分数和 61 次内存派发继续按原冻结记录解释，未重跑三臂。[原 PoC](contract-tool-reuse-poc.md) · [C2 入口证据](deterministic-design-compiler-c2.md)

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q tests/test_approved_negative.py tests/test_negative_values.py tests/test_negative_post_observation.py tests/test_product_preconditions.py
```
