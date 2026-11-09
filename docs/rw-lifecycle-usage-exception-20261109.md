# 一次性用量例外登记（2026-11-09，T2 单次调用）

## 操作员决定

> 对第二次读写验收中 T2 的那一条 UNKNOWN 模型调用，授权登记一次性用量例外。
> 必须依据原始 `call_id`、`task_id`、`request_sha256` 精确匹配，保留原始 UNKNOWN、
> 超时证据和审计记录。不得填写估计 Token、伪造供应商回执、修改历史裁决或放宽其他调用的
> 用量门禁。

审批文本哈希：见审计文件 `authorization_sha256`（SHA-256，冷复算可核对原文）。

## 精确匹配的原始记录

| 字段 | 值 |
| --- | --- |
| `call_id` | `51ca0125cd2b4e469f0de0db49943eed` |
| `task_id` | `8e3fa3b4282748d6a8f2c0d0aff2adcd`（T2，`NEEDS_REVIEW`） |
| `request_sha256` | `346ced4f4f9b9b41ab5d5b543a782075d103bddf9ecf1e4fb39fae25343d39ec` |
| 记录状态 | 登记前 `UNKNOWN`，登记后仍 `UNKNOWN` |
| 超时证据 | `reason=MODEL_TIMEOUT`、`http_status=null`、`elapsed_ms=30031.0`、`response_bytes=0` |
| 用量字段 | `input_tokens/output_tokens/total_tokens` 全部保持 `null` |

## 登记方式

`python scripts/rw_lifecycle/usage_exception.py --workspace runs/rw-acceptance-v2-20261109/workspace --call-id … --task-id … --request-sha256 … --decision "<审批原文>" --audit docs/evidence/rw-lifecycle-20261109-v2/usage-exception.json`

- 脚本先只读核对归档行；`task_id` 或 `request_sha256` 与审批不一致即拒绝
  （`DECISION_DOES_NOT_MATCH_ARCHIVED_CALL`），不会写入任何记录；
- 真正写入走既有 `TaskStore.record_usage_exception`，其校验为：行存在、状态 `UNKNOWN`、
  `task_id` 与 `request_sha256` 匹配、任务已终止、同一 `call_id` 只登记一次；
- 授权对象模型 `UnknownUsageException` **没有** token 字段，因此结构上不可能填入估计用量；
  `scope` 固定为 `THIS_CALL_ONLY_PRESERVE_UNKNOWN`。

## 未做的事（与授权边界一致）

- 未填写任何 token 数、未生成供应商回执、未调用 `reconcile_model_usage`；
- 未修改 T1/T2/T3 的任何裁决、证据文件或历史冻结；
- 未放宽其他调用的门禁：闸门仍是"任何 `PENDING`/`UNKNOWN` 且无对应记录即阻断"。

## 验证结果

在归档库的**副本**上验证（不修改证据库）：

| 检查 | 结果 |
| --- | --- |
| 登记后归档库 `model_usage_blocked()` | `False`（仅此一次调用被放行） |
| 于副本插入第二条 `UNKNOWN` 后 | `True`（新调用立即重新阻断） |
| T1 的 `KNOWN`、`NOT_SENT` 行 | 状态与统计数据未变 |
| 归档 `UNKNOWN` 行 token 字段 | 仍为 `null` |

确定性回归 `tests/test_usage_exception_scope.py`：精确匹配登记后原行仍 `UNKNOWN`、无 token、
`total_tokens` 为空；第二条未授权 `UNKNOWN` 仍阻断；`request_sha256` 不符时脚本与存储层
分别拒绝；审批哈希与审计字段结构固定。

## 审计文件

`docs/evidence/rw-lifecycle-20261109-v2/usage-exception.json`（含审批原文、审批哈希、
`call_id/task_id/request_sha256`、登记前后状态、超时证据、闸门验证结果、未做事项声明）。
