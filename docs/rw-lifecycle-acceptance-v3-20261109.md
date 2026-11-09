# 第三次隔离 API 读写生命周期验收（2026-11-09）

冻结提交 `96f574b`（干净树）、模型 `ecnu-max`（45 s 单次超时、150 s 用例预算、≤2 次调用、
≤12 次目标 HTTP）、目标 `http://127.0.0.1:19097`（本地 `isolated` 夹具）。三个位置各执行
一次，未重跑、未换题、未调分。

## 1. 裁决

| 位置 | 裁决 | 目标 HTTP | 真实模型调用 | 通过的阶段 |
| --- | --- | --- | --- | --- |
| T1-v3 完整读写 | **NEEDS_REVIEW** | 0 | 2（12 889 + 11 356 tokens） | Focus 通过 → **Design 拒绝** |
| T2-v3 幂等 | **NEEDS_REVIEW** | 0 | 1（13 570 tokens） | **Focus 拒绝** |
| T3 身份隔离（上轮未消费） | **NEEDS_REVIEW** | 0 | 1（12 463 tokens） | **Focus 拒绝** |

合计 4 次真实 `ecnu-max` 调用、50 278 tokens、0 次目标请求、0 资源残留、0 UNKNOWN。
三条位置都**没有进入执行阶段**，因此本轮仍未验证真实读写闭环。

## 2. 逐条证据与根因

### T1-v3（完整 CRUD 原文）

- Focus：1 次调用成功（HTTP 200、12 889 tokens、12.5 s、响应 5 361 字节），需求子句与绑定齐全；
- Design：1 次调用返回的步骤图存在依赖环，产品以
  `CONSTRUCTION_DEPENDENCY_CYCLE`（layer `model`）拒绝，**请求发出前**终止；
- 产品行为正确：任务转 `NEEDS_REVIEW`，`summary.reason="方案未通过校验，本轮没有发送任何业务请求"`，
  保留 `diagnostic`，未发布 PASS/FAIL 结论（这正是上一轮修复的收尾路径，本轮首次真实生效）；
- 目标 HTTP 0 次，无资源创建。

根因：**模型**在设计阶段给出自相依赖的步骤顺序（例如 create 依赖自身读取）。

### T2-v3（幂等原文）

- Focus：1 次调用（HTTP 200、13 570 tokens、5.5 s、响应 5 016 字节）返回的
  `requirement_map.request_2.actions` 缺少动作见证，产品以
  `FOCUS_REQUIREMENT_WITNESS_MISSING` 拒绝，未进入 Design、未发送请求；
- 目标 HTTP 0 次。

根因：**模型**遗漏了第二条需求的动作绑定（见证缺失），产品校验按设计拦截。

### T3（身份隔离，上轮未消费）

- Focus：1 次调用（HTTP 200、12 463 tokens、4.7 s、响应 4 736 字节）在
  `requirement_map.request_4.before` 缺少顺序见证，产品以
  `FOCUS_REQUIREMENT_WITNESS_MISSING` 拒绝；
- 目标 HTTP 0 次；该位置至此**已消费**，不再重跑。

根因：**模型**遗漏第四条需求的前置顺序见证。

## 3. 已确认的安全与完整性事实

- 授权：`startable()` 用本轮授权副本；运行后 `WRITE_AUTHORITY` 仍为 `NOT_GRANTED`；
  19097 端口已无监听（夹具随 `served()` 关闭）；
- `/__namespace`、`/__reset` 仅编排器使用；命名空间按位置隔离；
- 目标审计为空、`items` 初末均为 0，无资源残留、无需补偿；
- 用量：4 条 `KNOWN` 槽位，无 `PENDING`/`UNKNOWN`，闸门未触发；
- 证据脱敏扫描干净（无 API Key、Bearer、测试身份令牌、Authorization 头）。

## 4. 本轮暴露的两个编排缺陷（已修复，不影响裁决）

1. 驱动在评分 T3 时假定 `approved_denials` 是字典，而 `plan_v2` 存的是元组 → 首个进程在
   T3 任务结束后崩溃；T3 的产品证据（任务、用量、诊断）保存在运行库中，未丢失；
2. `--plan` 切换后未重建 `SELECTED`，导致第二次尝试只重选 T3；产品以
   `SUBMISSION_CONFLICT` 拒绝重复提交，未产生任何模型调用或请求。

两处均为**驱动**问题，产品源码、评分器、rubric、契约与计划哈希在冻结时已固定，未被改动。
T3 的原始 Focus 响应体因首个进程崩溃未落盘，仅保留诊断、响应字节数与哈希；已在证据中标注。

## 5. 证据

`docs/evidence/rw-lifecycle-20261109-v3/`：

- `freeze.json`：冻结源码 SHA、`plan_v3` 哈希、rubric、批准配置、45 s/150 s 预算、目标初态；
- `dry.json`：12 条批准策略、枚举与必填头绑定、`consumed_positions`、位置清单；
- `result.json`：三个位置的合并结果（T3 取首个真实执行，附第二次重复提交被拒的说明）；
- `evidence.json`：裁决、用量槽位、根因、编排缺陷说明、脱敏扫描结果。

冷复算：`python scripts/rw_lifecycle/acceptance.py dry runs/<new> --plan plan_v3`；
`python scripts/rw_lifecycle/preflight.py` 复核 10 项生命周期 + 3 项变体控制。

## 6. 结论与下一步

- **产品**：本轮所有拒绝都由既定校验完成，任务状态、诊断与报告均正确；
  `OPENAPI_VALUE_ENUM`、静默 `FINISHED`、UNKNOWN 闸门等既有修复未回退；
- **模型**：三处失败均为 `ecnu-max` 规划输出缺陷——两处 Focus 见证缺失、一处 Design 依赖环；
- **测试环境**：无异常（夹具、命名空间、审计、清理均符合预期）。

因此当前瓶颈是**模型规划的稳定性**，而不是产品链路。建议的下一步（需新的授权与新的位置）：

1. 针对"见证缺失"和"依赖环"做一次只读的提示词/校验反馈改进（例如把校验失败的
   `code + fields` 作为**同一次调用内**的结构化重试提示，仍受每用例 2 次调用预算约束），
   并用**新的**自然语言位置验证——不复用本轮三个已消费位置；
2. 或先做离线研究：用已归档的三份 Focus 原文与两份 Design 响应离线重放，确认改进能
   在零模型调用下把见证/依赖环修正，再申请新的真实位置。

在模型能稳定通过规划校验之前，尚不宜宣称"完整 Agent API 测试验收"已达成。
