# P3-B2.3 诊断与离线验收方案

2026-11-17。P3-B2.2 冻结复验有 3/5 轮测到全部 31 项批准义务，发布门限为 4/5，结论仍为 **NO-GO**。本阶段只执行离线诊断和针对性检查。原始归档留在 `.eduwork/p3b22-retest-20261117T052048Z-d4a1f9a3/`，较早的 Focus 证据在 `.eduwork/p3b22-live/`。

## 超时与用量

| 轮次 | Trace 与结果（北京时间） | 本地模型调用 ID | 请求 SHA-256 |
| --- | --- | --- | --- |
| v2 | `phase-04.json`，`model-outputs.jsonl:13`。`negative_batch_4` 设计调用在 13:33:28.055 记下 `MODEL_TIMEOUT`，耗时 60.016 s，HTTP 状态缺失，响应 0 字节，账本 `UNKNOWN` | `a7123b6d68db4ab6b2e629f6c716271e` | `9f73474eadbcb6f2a41305bcd106a7ce83766d1a3ad40ee93633f0f9bd76a9b5` |
| v3 | `phase-00.json`，`model-outputs.jsonl:1`。首次 Focus 在 13:34:42.383 记下 `MODEL_TIMEOUT`，耗时 60.031 s，HTTP 状态缺失，响应 0 字节，账本 `UNKNOWN` | `4ab6e9170548424d8b6c401659bc8cae` | `897e1ed241ce6a8fc04efcc816f0e11f3965bc60ffa1595458febe5c66870f99` |

- v2 先在批次 3 收到 HTTP 200 和 7,576 tokens，随后解析失败，调用 ID 为 `7ecff264c7db42a3bfa5678d452c9147`。批次 3 的下一次提案返回 HTTP 200 和 7,732 tokens，调用 ID 为 `9537fd212a034277bbf3c8f518649c69`，预检通过并执行了两项。批次 4 的超时来自另一次调用。v2 账本共 16 次，15 次已知合计 111,241 tokens，含未知项的总量为 `null`。终止为 `BLOCKED / MODEL_SERVICE_UNAVAILABLE`，已测 14/31，29 次业务 HTTP。
- v3 账本只有该未知调用，以 `NEEDS_REVIEW` 停止，0/31、0 次业务 HTTP。v2 成功 Focus 的请求摘要与 v3 超时一致，说明相同请求内容的两次观察不同，摘要不能唯一标识请求。v2、v3 的用量对账和例外表均为空。
- 两次超时的本地记录均无 HTTP 状态、网关错误码、服务端请求 ID 或模型响应。`llm_planner.py` 当前把 `httpx.TimeoutException` 与外层 `TimeoutError` 归并为 `MODEL_TIMEOUT`。归档只能确认客户端在获得响应前超时，无法进一步区分连接、传输、网关排队或推理。批前 3 次小探针为 HTTP 200，不能解释随后的两次超时。
- 已核对本地 Trace、SQLite 账本和 ChatECNU 公开文档。当前权限下没有可访问的网关逐请求日志或对账记录，**两次超时的服务端状态和实际用量不可核实**。公开文档给出对话响应 `id` 示例和账户配额汇总。结算核对需要获授权的逐请求记录，可使用 `ecnu-max`、约 13:32:28–13:33:28 与 13:33:42–13:34:42 两个时间窗及请求摘要协助匹配。仍以 `UNKNOWN` 为准，不用探针、额度变化或已知调用均值补算。[ChatECNU 通用对话](https://developer.ecnu.edu.cn/vitepress/llm/api/completions.html) · [配额限制](https://developer.ecnu.edu.cn/vitepress/llm/limit.html)

## 原始输出回放与最小修复

使用归档中的原始解码输入，经现有 `TestFocusModel._decode_output`、`TestDesignModel._decode_output` 和 `validate_design_case` 回放。模型服务与目标 HTTP 均未调用。预检使用 v2 归档的 OpenAPI 和审批快照，实际执行覆盖从归档回执计算。v3 没有可回放的输出，Focus 提案异常使用历史失败和 v2 成功响应对照。

| 问题与证据 | 归因 | 最小修复及针对性验收 |
| --- | --- | --- |
| v3 首次 Focus、v2 批次 4 都在获得 HTTP 状态前超时。客户端持久化账本只有本地 ID、摘要和聚合的 `MODEL_TIMEOUT`。 | 超时的上游根因需服务端核实。Harness 的缺口是请求关联与超时阶段诊断。 | 在模型客户端和同一账本记录本地调用 ID、发送/结束时间、可观测的超时类别、收到状态后可获得的白名单请求 ID 或响应 `id`。关联请求头须先有网关支持证据。模拟连接超时、无响应头的等待超时、读响应体超时和已有网关拒绝路径，验证标识可空、秘密不落盘、`UNKNOWN` 仍阻止后续调用，已有重试与预算规则保持。 |
| 历史 s1/s2 两次、t2 首次把仅作可信范围说明的 `request_2` 加入 `requirement_map`，回放报 `FOCUS_SOURCE_CLAUSE_MISSING`。t2 第二次报 `FOCUS_SOURCE_REFERENCE_INVALID`。u4 两次报 `FOCUS_INPUT_TEXT_CHANGED`，涉及 `requested_checks.*.exact_value`。v2 首次 Focus 通过。 | 模型提案的来源和字面值问题。`FOCUS_SOURCE_CLAUSE_MISSING` 的名称对这份“多报键”证据不直观。 | Focus 请求和现有修正提示复用同一份可信可执行键集合，明确列出缺少/多出的键，继续使用原诊断码。已有批准输入优先引用 `source_ref`/`check_ref`，明确要求精确值的场景仍校验原文。回放上述 8 份拒绝响应与 v2 成功响应，分别断言诊断和范围。测试现有有界修正次数、错误时 0 业务 HTTP，以及候选不能引入新审批。 |
| v2 批次 3 的原始响应报 `JSON_SYNTAX/MISSING_COLON`，位置 1253、行 1 列 1254。v4 批次 5 同类错误在位置 5273、行 1 列 5274。v2 下一次原始响应通过，先前错误响应本身没有派发。 | 模型响应格式错误。现有严格解码及有界修正链工作正常。 | 保留解析器的拒绝行为。把现有行、列、原因及未测义务在已有修正通道中直接呈现，按需收紧同批次非必要解释输出。验收以两条原文重复命中精确诊断、拒绝时 0 业务 HTTP、v2 保存的第二份输出通过、预算耗尽和未知用量仍按原门禁停止为准。 |
| v1 批次 7 的原始响应引用未提交的 `createParcelForNegative8`。回放报 `CONSTRUCTION_REFERENCE_INVALID / SOURCE_UNKNOWN`，字段为 `cases.0.plan.steps.0.arguments.0.from_step`。诊断的 `allowed_from_steps` 却只列出消费步骤自身 `replaceQuantityOutOfBound`。 | 悬空引用来自模型。把自己作为可用候选是独立的 Harness 诊断缺陷。 | 区分“当前场景已有节点”与“满足批准绑定的合法来源”。无合法来源时返回空集合并指出缺少前置步骤。保留拒绝，不插入业务节点，不替换模型引用。用原始 v1 响应及最小用例验证未知、自引用、跨用例、无合法来源和同场景合法来源，拒绝时 0 业务 HTTP。 |
| v2 批次 1 提交 4 项，预检和实测各只有 1 项。其余三项为 `web_rule_3_negative_10`、`web_rule_3_negative_2`、`web_rule_3_negative_3`。模型把测试值放到 `arguments[].name`，违反出站 schema 的字段枚举，三项均被 `OPENAPI_UNKNOWN_BODY_FIELD` 拒绝。以冻结配置重建出站请求，字节数 32,718 和请求摘要均匹配。批次 2、3 分别为 4/4、2/2。批次 4 无输出，批次 5–7 未派发。 | 错误字段名属于模型规划问题。Harness 保留了欠测账本，但混合结果仍标为 `DESIGNED`，工具返回的单个拒绝项只有笼统原因，缺少对应 policy ID 和安全字段路径。 | 保留逐用例预检与独立有效项执行。为批次结果显式列出实测、预检拒绝及未派发的 policy ID，并附受限诊断码和结构路径。模型输入用现有批准字段选择提供更明确的参数槽提示。已有后续提案只携带该批剩余义务，禁止重放已测项或自动改写拒绝用例。用 v2 原文验证 `4/1/1`、三个拒绝可定位且各自 0 HTTP，再用离线 Mock 验证后续提案只含这 3 项，仍受原预算和门禁限制。 |

## 验收顺序与退出条件

1. 先落地可独立验证的模型请求诊断字段与引用候选修正。对照 v1/v2/v3/v4 归档和 Mock transport 验证请求标识、错误类型、引用路径、凭证清理与状态兼容性。
2. 再收紧 Focus 和负向批次提案提示，并补齐部分预检失败反馈。保持审批清单、请求及模型预算、批准断言、清理义务、终止条件和现有重试上限。离线测试对照 v2 的 31 项分母、17 项未测、真实 503 的 `ASSERTION_FAILED`、拒绝项没有执行回执。验证无越权请求、未知写入不重放、清理证据缺失时无法完成。
3. 离线验收可证明诊断准确、失败拒绝正确、后续输入范围受控。模型生成合法提案的比例、超时率与历史未知用量仍受外部服务限制。离线结论不改写原始证据或 P3-B2.2 的 NO-GO。将来若走发布验收，沿用原 4/5 门限并另行启动。

## Git 与执行范围

`git --no-optional-locks status --short` 可读。`.git/index` 存在，检查时 `.git/index.lock` 不存在，进程快照无 `git`、`git-remote-https`、`git-credential-manager`、`git-lfs`。`.git` ACL 可读，含显式拒绝写入/删除的条目，`index` 继承相关条目。当前会话的文件访问范围标记 `.git` 为只读。历史 `index.lock` 写入拒绝与权限限制一致，现存锁或竞争进程无证据。检查到此停止，未做写入探针、删除锁、改 ACL、提权、暂存或提交。

离线原始输出回放完成。针对性 pytest 筛选得到 `14 passed, 92 deselected in 4.70s`。此前较宽的定向检查在输出 21 个进度点后无进展，已中断，未取得整组结果。相关 9 个 Python 模块的 `ruff check --no-cache` 返回 `All checks passed!`。

本轮只变更诊断与进度文档。未重新执行全量回归或五轮发布，未进入 P3-B3。下一步是按上述第一项实现并验证最小可观测性修正和引用诊断修正。

## 最小修复实施与验收

| 修复 | 修改位置与前后差异 |
| --- | --- |
| A · 模型调用关联 | `report.py` 定义类型化关联字段，`task_store.py` 在既有 SQLite 账本保存字段，沿用原用量状态条件。`llm_planner.py` 和 `deep_harness/chat_model.py` 采集时间、请求摘要、模型标识、响应状态以及可观测的错误类型。`product_tasks.py` 和 `product_agent.py` 把同一本地调用 ID 带入规划记录或 Agent Trace。以前只能用时间与请求摘要交叉匹配，现在可通过 ID 对账。 |
| B · 引用候选 | `plan_construction.py` 根据当前位置、当前场景以及批准绑定的参数、来源策略、选择器和角色列出候选。自身、后续节点、负向结果、认证结果和不存在的节点不列入建议。`obligation_gate.py` 在候选为空时明确提示缺少符合批准绑定的前置步骤。无序提案仍按既有规则排序，排序后的执行器仍拒绝非前序引用。 |
| C · 预检诊断 | `dsl.py` 在拒绝点记录步骤和掩码位置，合法字段仅取自已声明的可写请求 Schema。`product_design.py` 把位置映射回 `arguments[n].name` 并关联经过操作和角色核对的策略 ID。`diagnostics.py`、`product_tasks.py` 与 `product_agent.py` 将构造、Schema、引用、欠覆盖的诊断投影到任务证据和批次工具结果。混合批次仍可执行独立有效用例，拒绝的义务没有执行回执。 |

上表的 Python 路径均相对于 `src/agenttest/`。新增 `tests/test_p3b23_diagnostics.py`，从历史失败抽取最小回归输入，并用 Mock transport 验证持久化、规划、Agent Trace 两条调用路径、无网关 ID、超时和已知用量。更新原有 `tests/test_obligation_gate.py` 的引用提示用例，批准为字面值的槽没有合法步骤来源。

新调用的 `local_call_id` 对应 SQLite `model_usage_journal.id`。按该 ID 查到 `state`、`task_id` 和 `stats` 后，可在 `summary.design_records[].model_call` 或 `summary.agent_runtime.call_trace[]` 找到相同 ID。Focus 网关重试的中间记录位于 `summary.focus_gateway_retries`。任务一级的 `run_id` 沿用产品任务 ID。时间戳为 Unix 秒，耗时由单调时钟计量。网关请求 ID 来自受限响应头，响应体 ID 独立记录。缺失时为 `null`，调用超时且无用量时保持 `UNKNOWN`，旧记录不补写。新增字段不保存凭证、请求头集合或请求正文。

对照原始归档得到以下结果：

| 样本 | 本轮结果 |
| --- | --- |
| v1 批次 7 | `SOURCE_UNKNOWN`，路径 `cases.0.plan.steps.0.arguments.0.from_step`。失败步骤为 `replaceQuantityOutOfBound`，义务为 `web_rule_7_negative_8`，候选为空。 |
| v2 批次 1 | 原始 4 项提案仍是 1 项通过预检，3 项拒绝。拒绝步骤为 `neg_meta_type_mismatch`、`neg_name_type_mismatch`、`neg_name_empty`，对应 `web_rule_3_negative_10`、`web_rule_3_negative_2`、`web_rule_3_negative_3`。字段均为 `plan.steps.0.arguments.1.name`，Schema 候选为 `meta`、`name`、`quantity`。回放只运行预检，归档中的 1 项实测回执原样保留。 |
| v2 批次 3 | 原 `JSON_SYNTAX / MISSING_COLON` 仍在位置 1253，行 1 列 1254。保存的下一份响应仍通过。 |
| 历史 Focus 与 v3 | s1/s2/t2/u4 共 8 个响应维持原来源或字面值拒绝，v2 有效 Focus 仍通过。v3 超时无响应可解码，原 `UNKNOWN` 不变。 |

定向 pytest 的同步用例 177 通过，Mock 异步用例 52 通过，额外 ASGI/HITL 和恢复用例 5 通过。Mock/ASGI 验证覆盖拥有者约束、批准写入、未知写入验证与不重放，以及预算和义务门禁。普通 Windows 测试进程的 asyncio 唤醒 socket 初始化仍停滞，异步定向测试使用只对该测试进程生效的无网络测试事件循环。Ruff 和 `git diff --check` 通过。原始归档回放业务 HTTP 为 0。

改动进入模型调用、构造诊断和预检证据的正常 Runtime 路径。Executor 派发、审批、预算、清理、`NO_PROGRESS`、`strict_complete` 和 `checks_passed` 使用原有判断。历史 31 项义务、24 项负向策略和 4/5 发布门限沿用。使用同一冻结算法计算，工作区 Python 源码 SHA-256 为 `4e088caa9087059cc5aac917f052c2f8bdad7443fdb58b009de48ebb0af1eadd`。旧归档源码摘要 `104211aa39ea9edb0be8b45b4cafc80a5c97d8bb44e5037219c15af884e2b0d3` 和 NO-GO 结论仍在原归档。

没有启动新的全量冻结回归，标准 Windows 异步环境恢复后可用新的输出目录执行优化入口：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/run_frozen_regression.py --out .eduwork/p3b23-frozen-offline
```

此次未启动真实模型验收，未进入 P3-B3。历史超时的服务端根因、真实用量和新诊断下的真实模型提案成功率仍缺证据。HEAD 为 `ee01cf6393cd34b5427382ed2a2243c28d83c679`，变更留在工作区，未暂存或提交。

## 2026-11-17 冻结真实验收结果

**NO-GO：0/5 轮达到 31/31，发布门限为 4/5。P3-B3 未获准。** 五轮全部纳入统计。三次健康探针均为 HTTP 200，用量分别为 147、147、65 tokens。探针用量单列。

- 冻结提交为 `f3117213099e1becd332a2f29e4b97e3a7af16aa`，源码 SHA-256 为 `4e088caa9087059cc5aac917f052c2f8bdad7443fdb58b009de48ebb0af1eadd`。工作区摘要为 `2f04d01b754a5884be991466518be6f2c4910f8d2845a49c635431c16b6090f6`，公开配置摘要为 `cecf6dff2dccf853e1da5327457631243d9d9271f0575d1c6a823261aca52172`。
- 普通 PowerShell 全量离线回归收集并执行 2,929 项。2,913 passed、15 skipped、1 xfailed、0 failed、1 warning，耗时 2,436.92 秒。回归前后摘要一致，重复收集、漏项、额外项和重复执行均为 0。
- 五轮真实验收按序完成，启动器的五个退出码均为 0。批次从预检到收尾用时 13 分 50.324 秒，证据保存于 `.eduwork/p3b23-acceptance-f311721/`。离线证据目录为 `.eduwork/frozen-regression-p3b23/`。

| 轮次 | 实测义务 / 负向策略 | 业务 HTTP | 终态与归因 | 模型调用 / 已知 tokens / UNKNOWN 次数 |
| --- | --- | ---: | --- | --- |
| v1 | 0/31、0/24 | 0 | `NEEDS_REVIEW`。Focus 首次误填可信范围子句，返回 `FOCUS_SOURCE_CLAUSE_MISSING`。修正尝试仍因 `FOCUS_INPUT_TEXT_CHANGED` 被拒。 | 2 / 29,274 / 0 |
| v2 | 19/31、12/24 | 48 | `BLOCKED / MODEL_SERVICE_UNAVAILABLE`。两项负向用例分别因未知的请求体字段被预检拒绝，批次 5 请求随后超时。 | 17 / 118,573 / 1 |
| v3 | 21/31、14/24 | 54 | `PARTIAL / OBLIGATION_NOT_DISPATCHED`，Agent 以 `NO_PROGRESS` 停止。四次 `JSON_SYNTAX/MISSING_COLON` 出现在批次 1、5、5、6，剩余 10 项 `replaceParcel` 负向策略无执行回执。 | 22 / 181,677 / 0 |
| v4 | 21/31、14/24 | 54 | `BLOCKED / MODEL_SERVICE_UNAVAILABLE`。Focus 首次被拒后修正通过，批次 1 曾报 JSON 语法错误，批次 5 随后超时。 | 19 / 152,466 / 1 |
| v5 | 0/31、0/24 | 0 | `NEEDS_REVIEW`。首次 Focus 请求超时，未知用量门禁停止后续模型调用。 | 1 / 无已知用量 / 1 |

v1、v5 停在 Focus 阶段，尚未建立终止义务账本。表内的 0/31 按冻结验收范围和零业务执行证据计。五轮的 `complete` 均为 false。进入运行时的三轮 `strict_complete`、`checks_passed` 均为 false。

### 安全、清理与诊断对账

- 156 条业务 HTTP 与持久化回执逐轮对齐，全部为首次尝试并有 `EVIDENCE_COMMITTED` 回执。43 次 POST 均可关联已批准的 HITL 组和步骤，合计 15 项批准记录。零越权、零未知写入重放、零负向策略重复执行。
- 15 次资源创建均有清理证明，另有 28 次负向 POST 无副作用证明。五个隔离靶场最终库存均为 0。运行时逐轮确认工具边界，未进入运行时的两轮业务请求为 0。
- 持久化账本共 6 次真实 `readAvailability` 503，另有对应的 3 条用例 `ASSERTION_FAILED` 证据，预期 200。没有将 503 误判为通过。
- 61 个本地调用 ID 全部与 Trace 中的相同 ID 对齐，并逐条核对任务、请求摘要、状态、错因和用量。58 次明确用量合计 481,990 tokens。其余三次均为 `MODEL_TIMEOUT / DEADLINE`，分别发生于 v2 批次 5（60.032 秒）、v4 批次 5（60.047 秒）及 v5 的 Focus（60.062 秒）。三条记录的 HTTP 状态、网关请求 ID、用量均为空，继续保留 `UNKNOWN`。这些记录只能证实客户端超时，服务端等待与计量仍需逐请求对账。
- v2 的两项 `OPENAPI_UNKNOWN_BODY_FIELD` 均精确定位到 `plan.steps.0.arguments.1.name`，对应批准义务 `web_rule_3_negative_10`、`web_rule_3_negative_6`，各有具体步骤 ID，且均未派发。当前样本未触发 `CONSTRUCTION_REFERENCE_INVALID`，引用候选修正仍以定向回放和离线回归为证据。

下一步保留这五个位置，并以归档中的 Focus 来源和值错误、批次 5/6 JSON 语法错误开展离线诊断。对于三笔超时，只有获授权的服务端逐请求记录才能补全用量证据。保持审批范围、用量门禁及发布门限。
