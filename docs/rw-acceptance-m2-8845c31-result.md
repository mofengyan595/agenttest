# 冻结 v4b M2 真实模型验收结果（HEAD `8845c31`）

日期：2026-11-10。执行 HEAD：`8845c31c1d7e08cc877f6fae237b6a78e65e4260`（= `origin/master`，执行前工作树干净）。
本轮是正式测试，未修改源码、Prompt、schema、测试、rubric、冻结记录或评分规则；M2 只执行一次，未重试。

## 1. 结论摘要

| 项 | 结果 |
| --- | --- |
| 最终 verdict | **NEEDS_REVIEW** |
| M2 是否消费 | **是，永久消费**（`positions/M2` 完成，attempt `c41194640044409589e5c800c2a14135`） |
| 业务 HTTP 请求 | **0**（预期 `POST 201 → GET 200 → DELETE 204 → GET 404` 全部未发生） |
| 模型调用 | **2 次**（Focus 1 + Design 1），均 `ecnu-max`、HTTP 200，达到授权上限 |
| token | **22574**（输入 20200 / 输出 2374） |
| 用例耗时 | **36.58 s**（预算 150 s 内） |
| 首个失败阶段 | **Design / 依赖构造校验**：`CONSTRUCTION_DEPENDENCY_CYCLE`（`plan.steps.1`、`plan.steps.2`） |
| F1 | `UNCONSUMED`，未执行 |
| `WRITE_AUTHORITY` | `NOT_GRANTED` |

## 2. 执行前核验

1. HEAD 与 `origin/master` 均为 `8845c31…`，工作树干净（`git status --porcelain` 为空）。
2. 冻结一致性门禁通过：冻结头 `d8eeae1…`、冻结摘要 `31741a2d751b…`；`--accept-code-change 8845c31`
   仅审计记录冻结后的代码变更，未放宽任何 artifact 规则。
3. `preflight-8845c31.json`：M2/F1 各 9/9 `READY`，`blocking=[]`，仅 `SOURCE_CHANGED`。
4. 执行前 `M2=UNCONSUMED`、`F1=UNCONSUMED`、`M1=HISTORICAL_CONSUMED`，无 `positions/M2`。
5. 模型身份 `ecnu-max` / `chatecnu` / `https://chat.ecnu.edu.cn/open/api/v1` / 45 s /
   4096 输出 / 20000 输入，凭据 `PRESENT`。
6. 隔离靶场 `127.0.0.1:19097` 就绪，初始状态 `10718ffa…`。

授权：`authorize-M2-8845c31.json`，id `2a7faf16196d4fae8b6c720bd4cae525`，operator `mofengyan`，
绑定 HEAD `8845c31`、M2、12 请求 / 150 秒 / 2 次调用 / 45 秒超时，完整性校验通过，无凭据。
旧 `de9a869` 授权未复用、原样保留。

## 3. verdict 与消费

- 执行命令返回：`M2 NEEDS_REVIEW statuses=[] requests=0`，findings：
  `create_not_confirmed`、`create_count_mismatch`、`cleanup_unverified`、
  `driver_sequence_mismatch`、`driver_no_target_requests`。
- `completed.json`：`verdict=NEEDS_REVIEW`、`requests=0`、`model_calls=null`、
  `elapsed=36.58`、`evidence_sha256=3ece07629cc218e8…`（与 `evidence.json` 实测 SHA-256 一致，
  20145 字节）、`completed_at=2026-11-10T14:22:39+0800`。
- M2 已消费，**不得重试**；F1 未被触碰。

## 4. 分阶段结果

| 阶段 | 结果 | 证据 |
| --- | --- | --- |
| Focus | **成功**：HTTP 200，1 次调用，27.843 s，输入 11276 / 输出 892 | `model-usage.json` 第 1 条；`evidence.json.focus` |
| Design | **模型调用成功，产物被拒绝**：HTTP 200，1 次调用，8.344 s，输入 8924 / 输出 1482；返回计划未通过构造校验 | `model-usage.json` 第 2 条；`project-report.json.diagnostic` |
| 实例绑定 | **未执行**（计划无效，`dsl_valid=false`、`dynamic_binding_rows=0`） | `evidence.json.checks` |
| 依赖构造 | **失败**：`CONSTRUCTION_DEPENDENCY_CYCLE`，字段 `plan.steps.1`、`plan.steps.2` | `project-report.json` |
| 执行器 | 未启动（0 请求，审核行为空） | `evidence.json.audit` |
| 生命周期判定 | 无资源，无法验证 DELETE/404；因空状态而 `cleanup=true`，但 `cleanup_verified=false` | `evidence.json.metrics/state` |

Focus 产物本身有可疑输入：一段 4 步流程被展开成 8 条 `required` 请求，其中 `request_2` 重复
`createItem`，`request_4/6/8` 重复 `getItem`/`deleteItem`，且未给出实例索引；这是 Design 产生
依赖环的上游诱因，但 Focus 阶段本身返回成功。

## 5. 实际 HTTP 与预期序列

| 预期 | 实际 |
| --- | --- |
| `POST /items` → 201 | 未发送 |
| `GET /items/{itemId}` → 200 | 未发送 |
| `DELETE /items/{itemId}` → 204 | 未发送 |
| `GET /items/{itemId}` → 404 | 未发送 |

`evidence.metrics.requests=0`，`checks.methods=[]`、`checks.statuses=[]`，`audit=[]`。
findings 中的 `driver_sequence_mismatch`（detail 为空方法/状态列表）和
`driver_no_target_requests` 正是零请求的直接体现。

## 6. 模型调用、token 与时间

| 调用 | 阶段 | HTTP | 耗时 | 输入 token | 输出 token | 合计 |
| --- | --- | --- | --- | --- | --- | --- |
| `8fe89014…` | Focus | 200 | 27.843 s | 11276 | 892 | 12168 |
| `f1bf8e72…` | Design | 200 | 8.344 s | 8924 | 1482 | 10406 |
| 合计 | — | — | 36.187 s（服务时间） | 20200 | 2374 | **22574** |

模型调用 2/2（上限内），业务请求 0/12，用例墙钟 36.58 s/150 s，单次调用均在 45 s 内。

**记账不一致（如实披露）**：`evidence.product.model_calls=null`、`product.requests=null`，
`completed.json.model_calls=null`，项目报告 `planner_calls=0`、`model_call=null`；但平台
`model_usage_journal` 明确记录 2 次 KNOWN 调用、共 22574 token。也就是说完成标记把已知用量记成
了 `null/UNKNOWN`，真实用量以 journal 为准；本轮不修改该记账代码。

## 7. 资源最终状态与 DELETE/404 证据

- 命名空间 `e2e-m2-1110-142201`；执行前后 `state_sha256` 均为 `f98c3ad52702…`，`items=0`、`audit=0`。
- 从未创建资源，因此不存在 DELETE、也没有 404 回读；`cleanup=true` 只是因为前后都为空，
  `cleanup_verified=false` 如实反映“无法验证清理”。
- 无残留资源、无未知写、无重放；审计行为空。

## 8. 首个失败阶段与根因

- **首个失败阶段**：Design（模型返回计划后的构造校验），发生在任何目标请求之前。
- **错误码**：`CONSTRUCTION_DEPENDENCY_CYCLE`（layer `model`，fields `plan.steps.1`、`plan.steps.2`），
  外层 `MODEL_INVALID_RESPONSE`。
- **根因**：模型生成的计划在第 1、2 步之间形成依赖环，`construct_plan` 的环检测拒绝该计划，
  `plan_valid=false`、`dsl_valid=false`，执行链未启动，所以 0 业务请求。
- 非基础设施故障：两次模型调用均 HTTP 200 且用量已知；不是超时、不是凭据问题、不是靶场不可达。

## 9. 与 v4a M1 的对照

| 项 | v4a M1（历史） | 本次 M2 |
| --- | --- | --- |
| verdict | NEEDS_REVIEW | NEEDS_REVIEW |
| 模型调用 | 1 | 2（达到上限） |
| tokens | 11750 | 22574 |
| 业务请求 | 0 | 0 |
| 首个失败阶段 | Focus 见证缺失（`FOCUS_REQUIREMENT_WITNESS_MISSING`） | Design 依赖环（`CONSTRUCTION_DEPENDENCY_CYCLE`） |
| 结论 | 规划阶段失败 | 规划阶段失败，执行链未启动 |

两次都未进入执行器；M2 不再是“未执行”，而是有真实、可复现、不可重放的 `NEEDS_REVIEW` 结果。

## 10. 边界与状态

- M2 已消费，**未重试**；F1 **未执行**，仍 `UNCONSUMED`。
- `WRITE_AUTHORITY=NOT_GRANTED`（命令结束与 `status` 均确认）。
- 未修改源码、Prompt、schema、测试、rubric、预算、评分器或 `freeze.json`。
- 执行 HEAD 保持 `8845c31`；第 9 轮仅将本报告与脱敏证据单独归档提交，未改动原始执行记录。

## 11. 证据索引

`docs/evidence/rw-acceptance-m2-8845c31/`：

- `claim.json`、`running.json`、`evidence.json`、`completed.json`
- `m2-run-8845c31.log`
- `authorize-M2-8845c31.json`
- `execution-manifest-8845c31-2.json`（执行清单）、`execution-manifest-8845c31.json`、`preflight-8845c31.json`
- `project-report.json`（服务报告，含 `CONSTRUCTION_DEPENDENCY_CYCLE` 诊断）
- `model-usage.json`（两次调用的用量，已去除请求/响应摘要哈希）
- `status-after.json`

## 12. 建议（不属于本轮执行）

M2 已不可重试。若未来另有新的、明确授权的冻结位置（例如 F1 或新的 v4c），需要先解决两个
规划层问题：Focus 对重复实例的建模（应为同一实例或显式索引，而不是 8 条重复请求），以及
Design 依赖图成环。两者都属于产品规划缺陷，应另开开发轮次并重新冻结，不得复用本次 M2 结果。
