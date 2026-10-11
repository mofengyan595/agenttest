# 阶段 2B 最小设计：受控的只读失败调查

状态：最小实现已接入，验收结果见本文末尾及 docs/progress.md。继续复用 Executor、Verifier、Vault 和已审核断言。不接 MCP、RAG、多 Agent、前端或新数据库。

## 从阶段 2A 证据划定范围

首轮 13/18 中三个非法计划只有粗诊断，不能通过调查补造原因；T12 意图不符须在执行前阻断；T18 工具失败不能靠重放创建来恢复。第二轮结果另记于独立评测文档，不预先假定其全部通过。

调查只针对已执行且有可审计响应证据的失败。JSON/DSL/策略/意图预检失败直接报告，不发额外业务请求。只读调查用于收集补充证据，不替代规划修复，不自动改判原结果。

第二轮实际为 16/18，T04 名称不符、T05 显式弃权，二者业务请求均为 0，调查入口必须跳过；不能为追求恢复率补发创建或读取。ownership 9/9 通过不表示未来调查已验证，最小验收仍须注入真实业务故障。详见 [第二轮证据](stage2a-round2-evaluation.md)。

## 状态流与信任边界

`execute -> classify_failure -> choose_readonly -> validate_investigation -> investigate -> report`

- 默认关闭。操作员显式配置独立 InvestigationBudget；首个实现上限固定为 1 轮、1 个 HTTP 请求、1 次 FakeAnalyzer 调用、30 秒，四项默认均为零。请求前预扣次数，失败/超时同样消耗预算。没有真实模型调用或 Token 消耗，不引入尚无用途的 Token 计费机制。
- 首个实现用 FakeAnalyzer，结果仍不可信。输入只有固定诊断、脱敏状态和断言结果、公共契约与只读能力目录；没有 JWT、凭据、原始响应、目标地址或预算修改权限。
- 模型只能选 capability_id、角色和运行时生成的不透明 resource_ref。可信 policy_id 和 selector 仅在操作员目录中，由执行器绑定。不接受 URL、headers、断言、预期状态码、原始资源 ID、写操作或任意代码；未知字段和能力直接拒绝。
- 操作员独立维护 ReadOnlyCapability：operation_id、角色/场景、可信策略 ID、允许的来源策略/selector 及人工确认的无副作用语义。整体最多一次调查，比逐能力限次更严格；仍通过 OpenAPI、TargetConfig、Policy Registry、Vault 和预算检查。
- GET 不足以证明无副作用。mini target 的异步状态查询会推进状态，不自动纳入目录。首批仅审阅 getCourse 的本人读取和跨用户拒绝查询。
- 引用仅来自同一 run 内已完成且校验通过的创建响应，由 Vault 过滤后执行器绑定。不能换 ID、访问其他 run/资源或由分析器补数据。
- Vault 只使用当前角色已经建立的认证上下文，不重发登录 POST，不向图状态或报告持久化 JWT。认证不可用时停止并交操作员处理。
- Executor 内部增加受控只读批次入口，复用现有绑定、校验、发送和 Verifier；不写第二套 HTTP 逻辑。该入口无法执行原 Scenario 的创建步骤。

## 不可变证据、恢复与独立预算

调查前固定原计划、契约、策略、目标、角色、安全预算和结果摘要，前后检查一致。能力目录是操作员额外授权的受限范围，模型不能扩权。

原 outcome、断言和尝试记录不可覆盖。报告追加 investigations：关联父 run/步骤、能力、引用摘要、请求前 journal、状态/延迟/长度/响应摘要、校验结果、预算和固定错误码。后一次读取成功不能回写原失败为 PASS。

每次调查发送前分配唯一 attempt ID 并扣预算。恢复只读取历史，不能恢复执行旧请求；发送结果未知时记 ATTEMPT_OUTCOME_UNKNOWN 并停止。最小实现不自动重发未知结果请求。

重新规划、重试、调查各用独立预算，默认 0；不得借用创建预算或因图恢复重新进入 execute。最小 2B 不实现修复计划或自动重跑。未来重规划只生成新的受控只读计划并重新校验；新建资源只能在操作员新授权的独立 run 中执行。

## 最小验收方案

| 场景 | 验收结果 |
| --- | --- |
| PASS / 预检失败 | 零调查 HTTP，原结果保持 |
| ownership 业务失败 | 可选一次批准查证；创建总数始终 1；原 ASSERTION_FAILED 保留 |
| 名称/ID 不一致 | 只能读取原创建 ID，不能修正名称/断言或换资源 |
| 伪策略/写操作/状态推进 GET | 请求前拒绝，零工具调用 |
| 越权角色/跨 run 引用/原始 ID | 拒绝，不登录其他账号或访问其他资源 |
| 注入/凭据泄露 | Vault 阻断；输入及报告无秘密；权限/目标/断言/预算不变 |
| 调查预算为零/耗尽 | 请求前停止，不挪用原测试预算 |
| 超时/工具/报告失败 | 独立分类保留证据，不重放 create 或未知结果请求 |
| 恢复/重复 checkpoint | 只读历史，不自动重发写操作或调查请求 |
| 可信配置变动 | 摘要检查失败即停止，无模型授权路径 |

## 最小实现与生命周期

- `investigation.py`：操作员能力目录/配置/零默认预算，StrictModel 分析选择与 FakeAnalyzer。
- `readonly_runtime.py`：从失败步骤已验证的来源引用生成单次目录；全套 DSL/OpenAPI/Policy 预检后只执行所选 GET，不重新执行用于校验的原计划前缀。
- `Executor.run`：在同一 AsyncClient/Vault 中完成原业务并先保存结果，再独立调查；原业务超时与调查时间分开。原流程结束后 `finally` 清除响应绑定、已建立角色集合和 Vault 中的 JWT/环境副本。Python 字符串无法保证物理内存覆写；清除引用不是可承诺的内存取证防护。
- `Workflow.execute` 仍仅调用一次 Executor；调查在该调用内部进行，`analyze_or_finish` 读取既有证据。没有增加可恢复 checkpoint 或额外写入执行入口。
- 会话只存在于 run 栈内，不向分析器暴露；资源句柄随机生成、不可跨 run 使用。分析器没有 Vault、原始响应、目标地址、账号配置或 Golden Labels。
- 目录只包含已建立认证的角色。认证过期返回原有策略判定失败，不尝试登录。第一次配置的 bearer 也必须在原业务成功使用后才能供调查复用。
- 能力的可信无副作用依据来自操作员审阅，不能仅依赖 GET。当前批准文件仅证明独立 mini target，不自动批准同名真实 API。

原业务结果、步骤、断言和尝试在分析前固定并检测漂移；调查只追加独立记录。请求记录唯一 attempt_id，发送前 error_code 初始为 ATTEMPT_OUTCOME_UNKNOWN 并持久化预算；收到完整响应后才清除此码。异步取消会保留未知结果；进程硬中止只能保留最后成功落盘的 journal，无法保证执行取消清理或最后一次状态写入。

调查失败码固定为 `INVESTIGATION_BUDGET_EXHAUSTED`、`INVESTIGATION_SELECTION_REJECTED`、`INVESTIGATION_AUTHORITY_CHANGED`、`INVESTIGATION_TIMEOUT`、`INVESTIGATION_TRANSPORT_ERROR`、`INVESTIGATION_SAFETY_LIMIT`、`INVESTIGATION_TOOL_ERROR`、`INVESTIGATION_REPORT_ERROR` 等。业务失败独立记 `INVESTIGATION_ASSERTION_FAILED`；原业务 outcome 不被任何调查结果覆盖。异常文本、Pydantic 输入及原始模型输出不保存。

持久报告或已有 run ID 一律不能交回执行器恢复；未完成尝试也不能恢复重发。永久报告故障时尽量保留原业务记录并返回固定错误，但不宣称新证据已持久化。同步文件系统阻塞不具备硬实时中断保证。

## 实际验收

运行 `scripts/smoke_investigation_http.py` 使用真实回环 HTTP：正常场景原 PASS、5 请求、0 调查；ownership 原 ASSERTION_FAILED、6 请求、1 次 course_owner_read（200，调查 PASS）。二者各创建 1 次、登录 2 次，服务均关闭，真实模型调用 0。

离线故障注入覆盖名称/ID 一致性、固定来源、非法选择/策略/写入/状态推进 GET、跨 run、默认及独立预算、超时/运输/工具/取消、发送前 journal、认证过期和报告故障。取消模拟进程中断语义；未进行操作系统强制杀进程或物理内存清除测试。完整测试数量、实际运行 ID/attempt ID 与 CI 状态记录于进度文档。

下一里程碑仍须先审查本次证据，再决定是否扩大已审核只读能力；没有实现自动修复、真实 LLM 调查、重规划或恢复。
