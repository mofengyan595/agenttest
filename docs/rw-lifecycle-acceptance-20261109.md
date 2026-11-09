# 首次隔离 API 读写生命周期验收（2026-11-09）

## 1. 结论摘要

| 用例 | 判定 | 归因 | 诊断码 | 目标 HTTP | 模型调用 |
| --- | --- | --- | --- | --- | --- |
| T1 完整生命周期 | **FAIL** | 模型规划失败 | `FOCUS_REQUIREMENT_GAP_WITHOUT_EVIDENCE` | 0 | 1（ecnu-max） |
| T2 幂等性 | **NEEDS_REVIEW** | 夹具表达力不足 | `FOCUS_INPUT_TEXT_CHANGED` | 0 | 1（ecnu-max） |
| T3 权限隔离 | **FAIL** | 模型规划失败 | `FOCUS_REQUIREMENT_WITNESS_MISSING` | 0 | 1（ecnu-max） |

三条首次位置全部真实执行：真实 `ecnu-max`、产品正常自然语言入口（`web_contract_v2`
模型设计路径）、批准策略、动态绑定、Executor HTTP 与审计链。三条都在 **focus（需求规划）
阶段**被产品自身校验拦下，**没有向 127.0.0.1:19097 发送任何业务请求**（写 0、读 0），
因此没有资源残留、没有 UNKNOWN、没有可重放写入。

产品侧行为全部符合安全预期：拒绝执行、说明原因、`usage` 全部 KNOWN、`WRITE_AUTHORITY`
运行结束仍为 `NOT_GRANTED`、测试服务已停止。

## 2. 运行身份与冻结

- 冻结点：HEAD `1af2a1f`，工作区干净（`freeze.json` 记录 `git status` 与 `src/agenttest/**` 的
  SHA-256 聚合），模型 `ecnu-max`，目标 `http://127.0.0.1:19097`，环境 `isolated`，
  `allow_mutations=true`、`isolated_confirmed=true`，预算 12 请求 / 120 秒 / 2 次模型调用。
- 批准声明：六个批准操作（`createItem`、`getItem`、`updateItem`、`replaceItem`、`deleteItem`、
  `listItems`）、`Idempotency-Key` 必填头、批准生成输入 `name`、只读身份 `auditor`（403）和
  匿名（401）访问检查；`startable()` 六项授权门直接通过，未绕过。
- 夹具初始状态 `10718ffae8dc…`，`items=0`；契约 `document`/`setup` SHA-256 与评分器
  `lifecycle.py` SHA-256 一并冻结。
- 证据：`docs/evidence/rw-lifecycle-20261109/`（`evidence.json`、`freeze.json`、`dry.json`、
  `summary.json`、`attempts/`）。

## 3. 三条用例的真实结果

三条任务均为 `NEEDS_REVIEW`，`reason` 是产品面向用户的原因句，模型层诊断码与字段如下
（来自 `model_usage_journal`，`usage_state=KNOWN`）：

| 用例 | 产品诊断 | 字段 | 请求/响应 SHA-256 前 12 位 | tokens | 耗时 |
| --- | --- | --- | --- | --- | --- |
| T1 | `FOCUS_REQUIREMENT_GAP_WITHOUT_EVIDENCE` | — | `3af75592e4db` | 10292 | 3.9 s |
| T2 | `FOCUS_INPUT_TEXT_CHANGED` | `input_requirements.2.value`、`approved_input_fields.0` | `29d76e05543f` | 9249 | 14.4 s |
| T3 | `FOCUS_REQUIREMENT_WITNESS_MISSING` | `requirement_map.request_3.before` | `716fba56a99e` | 9242 | 3.1 s |

三条的夹具审计为空（`audit_rows=[]`），`/__state` 前后均为 `items=0`，
`state_sha256` 与冻结初始值一致，未发生越权写入或名称/身份异常。

### 3.1 T1（FAIL，模型规划失败）

模型把“读取刚创建的条目”等复合中文子句当成需要证据的需求缺口，给出
`FOCUS_REQUIREMENT_GAP_WITHOUT_EVIDENCE`。产品按规则拒绝：未批准/无证据的需求不得
自动补全，也不得发送请求。归因为模型规划失败，与历史 F4 的
`FOCUS_REQUIREMENT_GAP_WITHOUT_EVIDENCE` 同类，不是产品缺陷。

### 3.2 T2（NEEDS_REVIEW，夹具表达力不足）

批准生成输入 `name` 只能有 **一个** 批准取值（声明为 `first`）。模型为了构造“同键异体”
把该输入改成 `second`，触发 `FOCUS_INPUT_TEXT_CHANGED` 并被拒绝。这是夹具声明无法表达
“同一幂等键 + 不同请求体 → 409”导致的，属于夹具表达力不足而非产品缺陷；产品拒绝改动
已批准输入的行为正确。下一步应给夹具补一个“第二批准取值/冲突变体”的表达方式（见 §5）。

### 3.3 T3（FAIL，模型规划失败）

模型在 `requirement_map.request_3` 上未给出 `before` 见证，触发
`FOCUS_REQUIREMENT_WITNESS_MISSING`。被拒后没有发出任何身份请求，因此 403/401 这一次
**未被实际验证**（不是未通过，而是未执行）。归因为模型规划失败。

## 4. 安全与资源审计

- 目标 HTTP 总数 0：三条用例均未进入 Executor HTTP 阶段，没有写请求，也没有读请求。
- 资源残留 0：夹具 `items=0`，命名空间为空；服务停止后 19097 端口无监听。
- UNKNOWN 0：三条 usage 均 KNOWN（tokens 见上表），无未知用量门禁触发。
- 授权恢复：运行结束 `WRITE_AUTHORITY["operator_approval"] == "NOT_GRANTED"`，
  `authority.startable()` 重新拒绝（脚本内断言通过）。
- 无越权/未批准请求、无身份违规、无命名空间违规：因为没有请求到达目标。

## 5. 根因与下一步优先级

1. **最高优先：focus 阶段的模型可靠性**。三条都被 focus 校验拦下，说明当前中文复合需求
   在“证据/见证/原文”三处约束下模型通过率低。建议：在 focus 提示中显式给出
   见证与证据的必填位置示例，并把长复合句拆分为可逐条核验的子句；改进后用**新的**
   首次位置验证（不复用本次三条位置）。
2. **夹具表达力**：为“同一幂等键 + 不同请求体”增加获批的冲突变体（例如第二个获批
   `name` 取值或 `conflict_variant` 声明），使 T2 能在不篡改批准输入的前提下执行。
3. **产品可选改进**：`GenericSetup` 目前无法声明 409 冲突预期（仅 400/422 负向能力），
   `access_checks` 也只支持 GET/HEAD/OPTIONS；若要做完整读写权限矩阵，需要产品侧扩展
   声明能力。
4. 复核清单保持：动态 ID 绑定、必填头、幂等重放、身份隔离、清理回读——本次因未进入
   HTTP 阶段而全部未得到验证，不得记作通过。

## 6. 夹具脚本变更（仅夹具，不改产品）

- `scripts/rw_lifecycle/authority.py`：`Idempotency-Key` 改为必填头并声明头参数；
  `createItem.name` 改为批准生成输入；`getItem` 增加 `auditor`（403）与匿名（401）
  访问检查并在契约中声明 401；`setup()` 启用 `assisted=True`，使项目走
  `web_contract_v2` 的模型设计路径。
- `scripts/rw_lifecycle/acceptance.py`（新增）：冻结 → 确定性预检 → 三条首次位置
  执行 → 评分与证据归档；带“位置已消费不得重放”的保护。
- `runs/` 下两次纯夹具层失败（v1 意图门 `INTENT_UNVERIFIED_BUSINESS_REQUIREMENT`、
  `SUBMISSION_CONFLICT` 提交标签冲突）均 0 模型调用、0 目标请求，已归档在
  `docs/evidence/rw-lifecycle-20261109/attempts/`，不计入首次位置。

产品源码本次未修改；九项 `scripts/rw_lifecycle` 预检与既有测试不受影响（夹具脚本改动
已通过 `ruff check`）。
