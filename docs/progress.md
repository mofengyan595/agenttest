# 开发进度

## 阶段 2A 最终验收（2026-10-11）

- 最终代码提交：`d18107eaf959489d3fe157d15ca02e5252f074df`，已推送原仓库 master，所有旧提交保留。
- 对应 [Actions 38115469263](https://github.com/mofengyan595/agenttest/actions/runs/38115469263) 四组全部 success。Windows/Linux agent 各 264 passed；base 各 208 passed、3 skipped、1 个已有 Starlette 弃用提醒。
- 四组 Ruff、pip check、旧回环 HTTP 通过；两个 Agent 组新增 3 步/5 步自然语言回环通过，真实模型调用 0。默认测试和 CI 完全不依赖学校 Key。
- 本地最终 264 passed，Ruff 和依赖检查通过。首轮真实评测仍为 13/18，21 次生成总计 26797 Token；修复后只做离线回归，没有把历史失败覆盖或编造第二轮模型指标。
- 本次阶段 2A 完成；尚待后续独立轮次改善规划成功率、扩展审核任务和安全诊断。暂不接 MCP、RAG、前端或真实 CourseInsight。

## 最终审查与远端 CI（2026-10-11）

- 文档及首次完整验收提交：`30af4fdab2be0a8535a035cf301f6399f810bc44`，已推送。
- [Actions 38115309963](https://github.com/mofengyan595/agenttest/actions/runs/38115309963) 四组全部 success：Windows/Linux base 各 207 passed、3 skipped（可选 Agent 模块）、1 个既有 Starlette 弃用提醒；agent 各 262 passed。四组 Ruff/pip check/旧回环均通过，两个 Agent 组新增自然语言回环也通过，无真实模型调用。
- 最终审查发现“无批准路径时返回空草案”的提示与 minItems=1 矛盾。仅将模型草案的空步骤定义为显式弃权，直接进入 NEEDS_REVIEW / MODEL_NO_APPROVED_PLAN；实际 Scenario 仍至少一步、空计划绝不执行或 PASS。
- 新增结构化弃权和完整图不执行 HTTP 的两个回归；最终本地 **264 passed**，Ruff、pip check 通过。未增加真实模型调用，历史评测数字不变。
- 公开前实际 Key 检查覆盖 75 个跟踪文件、169 个历史 blob，无匹配；本地 .env、运行产物和虚拟环境仍被忽略，未重写任何既有提交。

## 阶段 2A 交付记录（2026-10-11）

| 模块 | commit SHA | 实际验证 |
| --- | --- | --- |
| user_task 与参数化策略 | `2a18dca9756bea29a42c51333ae1e6a51ead705d` | 54 项相关测试、Ruff、旧回环 HTTP PASS |
| 真实结构化 Planner / ChatECNU | `19c321accd09c690f70d093bb2ebb72195c1da5f` | 248 项全量测试、Ruff、pip check、真实两类任务 PASS |
| 人工任务与三基线、报告可靠性 | `7ff3b39ce9779b0ab8424e0d642ceceda65a4139` | 262 项全量测试、Ruff、pip check、30 次离线回放一致 |
| 四组无 Key CI 与自然语言回环 | `3141af1bc8d9087b20c97bacc07eb32274252b00` | 矩阵结构检查、Ruff、3/3 与 5/5 请求 PASS |

- Python 3.11.15，沿用 .venv，未安装或升级新依赖；保留原提交历史，未改 CourseInsight。
- 新旧真实回环 HTTP 已复查：8/9、3/3、5/5 均 PASS，全部临时服务关闭；真实模型不进入 CI。
- 首次模型评测及失败详见 [评测记录](stage2a-evaluation.md)、[脱敏统计](stage2a-results.json)。配置详见 [模型配置](model-configuration.md)。
- 首次真实 13/18 是实际结果，不因报告修复回放成功而上调；当前尚未验证修复后的第二轮真实模型成功率。
- 下一里程碑：细化不泄密的非法规划诊断，在新的独立评测轮次改善规划可靠性；不扩展 MCP/RAG/真实业务系统。

## 阶段 2A CI（2026-10-11）

- 评测与报告模块：`7ff3b39ce9779b0ab8424e0d642ceceda65a4139`。
- 保留 Windows/Linux × base/agent 四组 Python 3.11；Agent 组额外执行两类自然语言计划的真实回环 HTTP，规划来自 FakeLLM，真实模型调用 0。
- CI YAML 矩阵结构实际检查通过；无 --real、Key 或模型评测步骤。Ruff 通过；新增 smoke 本机实际 3/3、5/5 请求 PASS。
- 下一步：提交模型配置、首次评测统计和 README，推送后核对远端四组结果。

## 阶段 2A 人工任务、评测与证据可靠性（2026-10-11）

- 真实适配器提交：`19c321accd09c690f70d093bb2ebb72195c1da5f`。
- 用户明确人工批准 T01–T18：两个意图各 9 条、alice/bob 及合成名称变体，固定创建 201、本人查询 200 并比较 ID/名称、他人查询 403；只适用于独立 mini target。
- 增加三基线及分组统计，Golden Labels 不进入真实模型输入。计划有效、策略选择、意图满足、端到端成功分别计数；固定 FakeLLM 的 PASS 不能替代需求满足。
- 实际首轮：人工 18/18 成功；Fake 固定计划有效 18/18、策略 5/18、意图/成功 1/18；ecnu-plus 有效 15/18、策略 15/18、意图 14/18、成功 13/18。
- ecnu-plus consistency 成功 6/9，ownership 成功 7/9；2 次 PLAN_REJECTED、1 次 MODEL_INVALID_RESPONSE、1 次 TASK_MISMATCH、1 次 WORKFLOW_ERROR 全部保留，未自动修复计划或重跑覆盖指标。
- 18 次评测共 23332 Token（16761 输入/6571 输出），延迟均值 8716.94 ms、P95 17187 ms。连通性另 1 次零生成请求；含首轮探测/演示共 21 次生成、26797 Token，无自动模型重试。
- 离线复现 Windows 报告 replace 的 PermissionError / WinError 5；仅对报告文件原子替换有限重试，永久错误仍传播。图节点改为同事件循环、返回独立快照，避免线程取消后继续修改报告。
- 修复后 30 次离线延后第二角色登录回放全部 PASS、落盘与返回 30/30 一致，0 次真实模型调用；历史真实失败不改为成功。
- 完整离线 **262 passed**；Ruff 通过；pip check 无损坏依赖。旧真实回环 8 步/9 请求 PASS（三个 API 200）；新增两个路径 3 步/3 请求、5 步/5 请求 PASS，服务关闭。
- 公开前检查 75 个拟公开文件和 143 个历史 blob，无本次真实 Key；.env/.venv/.python311/runs 实际被忽略。
- 限制：本轮只有一次 18 条小样本评测；历史非法输出仅有摘要，无法复原具体文本；不宣称广泛泛化能力。下一步保留失败样本，改善规划可靠性和安全诊断。

## 阶段 2A 真实模型适配器（2026-10-11）

- 上一模块：`2a18dca9756bea29a42c51333ae1e6a51ead705d`（user_task、公共上下文、参数化策略）。
- 核对 OpenAI 结构化输出、LangGraph Graph API，以及用户指定的 ChatECNU 模型/对话/结构化输出/模型列表官方文档。
- 增加独立 HTTPX Planner Adapter，不增加 SDK 依赖。学校协议使用 max_tokens + json_schema + thinking disabled，目标和模型仅来自环境变量或显式本地 .env；无官方 OpenAI 默认地址，无失败降级格式或自动重试。
- 模型结果经有界 JSON、封闭 PlannerDraft、现有 Scenario/OpenAPI/Registry/Vault 校验，仍复用原 Executor/Verifier。默认 CI 不访问模型；新增 agent/agent-demo 真实调用必须显式 --real。
- 报告仅增加调用数量、状态码、字节数、耗时、Token usage 和摘要；不保存 API Key、原始响应、思考内容或提示词。
- 离线全量 **248 passed**，Ruff 通过，pip check 无损坏依赖。
- 真实连通性：GET models 200，配置模型存在，0 次生成；严格结构化探测通过（1 次生成，956 Token）。
- 真实两类需求产生不同计划：一致性 3 步 PASS（run c9e43eb634ca484d894c99dc9f94a955），归属 5 步 PASS（run 835faa6955054440a32d8df9d1270563）。首轮总生成次数严格限制为 3，逐次无自动重试；详细统计在本地 runs，不提交原始产物。
- 下一模块：18 条候选任务人工审核、三基线评测和安全回归；待审核候选不能声称已完成人工批准。

## 阶段 2 自然语言输入与参数化策略（2026-10-11）

- 基于 `d44b6de141c94007a124d4ca67520071bf59e041`，原始基线 189 passed，Ruff 通过。
- 增加 user_task 长度、控制字符、URL/明显秘密文本限制及 Vault 已知凭据检查；Planner 仅接收白名单操作、去掉描述/示例的 Schema、认证要求和策略适用前提。
- 新增独立 mini_target_nl_v1 策略：允许有界课程名、动态步骤名和角色；固定 201/200/403、创建资源来源、同/异角色、名称及 ID 相等关系。旧策略没有放宽。
- 修正 AGENTS.md 过时的阶段 1 范围。参数化断言仍由既有 Executor/Verifier 执行，未建立第二套执行器。
- 首轮全量 205 passed；增加链路回归后相关测试 54 passed、Ruff 通过。首轮新增测试误将状态码证据列表当作标量，修正测试后通过；业务断言未修改。
- 真实 loopback 检查 PASS：8 步/9 请求，三个 API 检查 200，六个图节点完整，临时服务已关闭。run `ebf41da50042465c8cd3b41450f192e0`。
- 本模块 SHA 在下一提交记录，避免自引用；下一模块为受约束的 OpenAI 兼容 Planner Adapter，无新运行依赖。

## 本轮最终验收（2026-10-11）

- CI 兼容性修复提交：`d42b98fa98fba3dd96e6b23b89f3e241f703911a`。
- 远端 [Actions run 38112168760](https://github.com/mofengyan595/agenttest/actions/runs/38112168760) 四组均 success：Windows/Linux 基础组各 154 passed、1 skipped（可选 Agent 模块）、1 个已有弃用提醒；Agent 组各 189 passed，无跳过。
- 四组 Ruff、pip check、真实回环 HTTP 均通过；每组 8 步 / 9 请求 PASS，health/spec_import/report_query 均 200；Agent 组完整经过六个图节点。
- 本机 Python 3.11.15 / 项目 .venv：189 passed，Ruff All checks passed，No broken requirements found；Windows Agent 安装脚本实际执行通过。
- 已保留原始提交历史，全部修改仅位于 AgentTest；阶段 1 P0 已修复，阶段 2 仅交付最小可信工作流。下一里程碑：人工审查策略/认证配置及工作流接口，再设计受控只读调查或有限重规划，继续保持断言和授权不可由模型改变。

## 首次远端 CI 反馈修复（2026-10-11）

- 首次 run `38112003608` 暴露全新检出无 runs/ 的 smoke 目录初始化问题，以及 Windows Python 3.11.9 内部 socketpair 与本机 3.11.15 的函数组织差异。
- smoke 显式创建报告根目录，并支持 --runs-dir，以全新目录验证；保留已有报告。
- 断网夹具仅兼容两种标准库内部 socketpair 函数代码对象及其回环连接；普通回环 HTTP、公网、DNS 和 connect_ex 继续阻断，新增反向回归证明。
- 未降低执行器目标白名单、预算或业务断言；修复后重新运行全量验收及远端 CI。
- 修复后本机实测：189 passed；Ruff 通过；pip check 无损坏依赖。基础与 Agent 各使用全新 runs 子目录完成真实 HTTP：均 8 步 / 9 请求 PASS，三个 API 检查 200，临时服务关闭。

## 本轮提交与审查边界（2026-10-11）

| 模块 | commit SHA | 本地验收 |
| --- | --- | --- |
| 策略 Registry 与角色认证隔离 | `8ab94c50b358ad2fcdfe4ca3bccddeed4a59544e` | 94 passed，Ruff，真实 HTTP PASS |
| OpenAPI 请求约束 | `137d00f7ae46943171c8ad480f5e92f26e6fa1f4` | 130 passed，Ruff，真实 HTTP PASS |
| 轮询分类与敏感证据 | `9e3afb16a19c36016bb43b9672c94f75196fcbf7` | 152 passed，Ruff，pip check，真实 HTTP PASS |
| Fake LLM LangGraph 最小工作流 | `ebfbd651d37776c2d1411d61295142bb70663de0` | 187 passed，Ruff，pip check，真实 HTTP 状态图 PASS |
| Keyless Windows/Linux CI | `490706219a16b466ed2c81b482527923a824045b` | 187 passed，Ruff，pip check，YAML 结构检查 |

- 五个功能提交已推送至原有 Public 仓库 master，未重写历史，未修改 CourseInsight。
- 推送前复查 15 个提交、51 个跟踪路径、118 个文件版本：无凭据提供商 Key、JWT、私钥或受禁本地产物。字面量候选均为脱敏逻辑或明确的合成测试数据；提交邮箱沿用用户先前明确允许公开的地址。
- `.venv/`、`.python311/`、`.env`、`runs/` 的忽略规则实际检查通过；工作区没有其他未提交成果被覆盖。
- 后续仍需人工维护可信 Registry 和配置文件访问权限；当前无多用户审批系统。未知复杂 Schema 停留 NEEDS_REVIEW。仅实现阶段 2 首模块，不接入真实 LLM、MCP、RAG 或业务系统。

## CI 与交付准备（2026-10-11）

- 阶段 2 最小图模块：`ebfbd65`；Windows Agent 安装入口已实测通过（187 passed，Ruff 通过）。
- 添加 GitHub Actions：Windows/Linux × base/agent，Python 3.11，独立 .venv、冻结依赖、离线 pytest、Ruff、pip check、显式临时回环 HTTP。
- 权限仅 contents: read，checkout 不保留凭据；不使用 LLM Key、Docker 或外部被测服务。
- 基础组仅安装基础依赖，工作流模块明确跳过；Agent 组完整验收工作流。CI 的实际远端结果在推送后核查。
- 提交前本地实测：187 passed；Ruff 通过；pip check 无损坏依赖；CI YAML 可解析，触发器、四组矩阵及只读权限结构核对通过。

## 阶段 2 首个模块：Fake LLM 状态图（2026-10-11）

- P0 最后修复提交：`9e3afb1`；P0 验收完成后才开始本模块。
- 查询官方 Graph API / Reference / PyPI，选定并实际导入 LangGraph 1.2.14，核对 StateGraph、START/END、条件边、compile、ainvoke 与 recursion_limit。
- 图：load_contract -> plan -> validate -> execute -> analyze_or_finish -> report；复用现有 Executor/Verifier 和同一份证据日志。
- Fake LLM 仅返回不可信 JSON；可信配置快照、策略 Registry、角色 Vault 和预算均不从模型输出加载。图状态不含原始计划、认证信息或 HTTP 正文；按官方 tracing_context(enabled=False) 禁用云追踪。
- 拒绝非法/重复键 JSON、非 JSON 数字、未知操作、伪造 reviewed/策略/目标/预算、credential/auth_from、秘密直传和跨步骤 Token 外传。分析不能篡改断言或重复执行。
- 默认单次规划和单次执行器调用，独立模型/工具/图步数/输出大小/超时预算，HTTP 沿用原安全预算；预算/工具异常均有回归。
- 实测：187 passed，无跳过，无警告；Ruff 通过；pip check 无损坏依赖。
- 实际 HTTP 状态图：六节点、8 步 / 9 请求 PASS，health/spec_import/report_query 均 200，临时服务已关闭。
- 安装仅限项目 .venv：langgraph 1.2.14、langchain-core 1.6.9、langsmith 0.14.7 等 25 个必要传递包，单独 requirements-agent.lock；基础版本未改变，未安装模型供应商 SDK 或 MCP。
- 增加可选 Agent 安装参数和 workflow/workflow-demo CLI。基础环境保留独立使用能力，未装 agent 时仅工作流测试模块跳过。
- 已知限制：Fake 返回固定样本，分析为确定性建议；无恢复/重规划/真实模型/MCP/RAG/真实 CourseInsight。阶段 2 仅完成首模块，未宣称整阶段完成。
- 本模块 SHA 在下一检查点登记；下一步为 GitHub Actions 验收及推送。

## P0 验收：轮询可信分类与证据脱敏（2026-10-11）

- 前一模块：`137d00f`，OpenAPI 请求约束。
- 轮询状态、合法中间态/终态、每次响应约束、轮询次数均来自可信 Registry。每次先核对 HTTP 状态和轮询契约，再等待终态。
- 401/403/404/500、非法状态、缺失状态、非 JSON 等违反已审核契约时 ASSERTION_FAILED；连接超时、预算耗尽和工具异常保留独立类别。
- 数值默认脱敏；仅策略 public_evidence 明确批准的非敏感统计字段可展示。密码、验证码、身份信息、自定义 sensitive_fields 和已知秘密别名优先隐藏。
- P0 全量实测：152 passed，1 个已有 Starlette 弃用提醒；Ruff 通过；pip check 无损坏依赖。
- 真实回环 HTTP：8 步 / 9 请求 PASS，health/spec_import/report_query 均 200，服务关闭。正常/ownership/terminal 三场景及隔离回归通过。
- P0 已完成；接下来才开始官方 LangGraph API 核对及 Fake LLM 最小工作流。本模块 SHA 在下一检查点记录。

## 审查修复：OpenAPI 请求约束（2026-10-11）

- 前一模块：`8ab94c5`，策略与认证隔离。
- 请求校验覆盖名称、严格基本类型、required、enum、嵌套对象/数组、长度、数值及独占边界、int32/int64 和只读字段。
- 静态值在预检校验，动态绑定与执行器注入的登录值在发送前校验。
- 仅支持明确的 Schema 子集；联合、组合、正则、开放对象、未知 format 等进入 NEEDS_REVIEW，不声称已验证。未声明的字段拒绝。
- 实测：130 passed，1 个已有弃用提醒；Ruff 通过；真实回环 HTTP 8 步 / 9 请求 PASS，三个 API 检查均 200，临时服务关闭。
- 本模块 SHA 在下一检查点记录。剩余 P0：轮询分类及数值/自定义敏感字段报告；阶段 2 尚未开始。

## 审查修复：策略与认证边界（2026-10-11）

- 基于发布提交 `d8a3b5ca8779930b6b3e55071dccde919578f17e` 继续，保留全部历史。
- 不可信 DSL 只引用 policy_id；独立 Registry 校验接口、角色、可信业务场景、数据绑定前提，未知策略在 I/O 前 NEEDS_REVIEW。
- 删除普通参数 credential 和 auth_from；批准的登录操作由执行器注入凭据，Token 留在每次运行的角色 Vault，认证响应禁止跨步骤绑定。
- 加入伪造审核、策略作用域、审批快照、认证响应与秘密别名泄露回归。
- 实测：94 passed，1 个已有 Starlette 弃用提醒；Ruff All checks passed。
- 真实回环 HTTP：8 步、9 请求 PASS，health/spec_import/report_query 均 200；临时服务关闭。
- 本模块提交 SHA 在下一检查点记录。尚待：请求 Schema 校验、轮询分类、全面脱敏；尚未开始阶段 2。

- [x] 独立 Python 项目骨架
- [x] Python 3.11 独立 .venv 与安装脚本（已在本机运行）
- [x] FastAPI 健康检查
- [x] OpenAPI 3.x JSON 最小接口摘要
- [x] 三个基础测试用例
- [x] 阶段 1：安全契约解析、DSL、Mock target、Executor、Verifier、报告
- [ ] 阶段 2：LangGraph Agent（最小 Fake LLM 工作流已完成，后续模块未完成）
- [ ] 阶段 3：MCP 与 Java 后端真实测试
- [ ] 阶段 4：效果评测
- [ ] 阶段 5：产品化

本文件由 Codex 在每个阶段完成后根据实际测试结果更新，禁止提前标记任务完成。

## GitHub Public 发布检查（2026-10-11）

- 已创建空的 Public 仓库：[mofengyan595/agenttest](https://github.com/mofengyan595/agenttest)。
- 保留原有 9 个提交及全部 SHA；发布前基准为 `08bad70b33abb973469bd08c76395c38238f655f`。
- 已审查原有 39 个已跟踪文件、全部 9 个提交中的 62 个文件版本及提交元数据。
- 扫描候选已核对为测试占位内容；未发现真实 API Key、密码、Token 或业务数据。
- 提交作者邮箱已核对并获得公开授权，未改写任何提交历史。
- `.venv/`、`.python311/`、`.env`、`runs/`、缓存和产物未进入当前或历史版本，忽略规则已验证。
- 发布前重新运行：pytest **78 passed, 1 warning**；Ruff **All checks passed!**；pip check **No broken requirements found.**
- README 增补公开仓库获取步骤，并明确模拟服务也使用项目 `.venv` 启动。
- 本次新增发布文档提交；推送及远程 SHA 的最终核查结果在本次交付回复中提供。
- **阶段 2 暂停在未开始状态；下一步先完成代码审查，等待用户后续开发指示。**

## 阶段 1 最终验收（2026-10-11）

**完成。** 实现提交：`8b79f00f6c2f7c131c792a5652f46e3eba65886a`。
环境/文档交付随后独立提交，不在文件中自引用其 SHA，可使用 `git log -1 --oneline` 查询。

| 实际运行 | 结果 |
| --- | --- |
| `.venv/Scripts/python.exe -m pytest -q` | 78 passed, 1 warning，无跳过 |
| `.venv/Scripts/python.exe -m ruff check .` | All checks passed! |
| `.venv/Scripts/python.exe -m pip check` | No broken requirements found. |
| `scripts/setup_env.ps1` | 实际执行成功；当时 74 tests + Ruff 通过，之后引用回归增加至 78 |
| `.venv/Scripts/python.exe scripts/smoke_local_http.py` | CLI run 真实 HTTP：8 步 / 9 请求 PASS；health/spec_import/report_query 均 200；服务已关闭 |
| `.venv/Scripts/python.exe -m agenttest demo` | PASS，8 步，退出码 0 |
| `.venv/Scripts/python.exe -m agenttest demo --fault ownership` | ASSERTION_FAILED，cross_user，第 5 步，退出码 1 |
| `.venv/Scripts/python.exe -m agenttest demo --fault terminal` | ASSERTION_FAILED，poll_batch，第 7 步，退出码 1 |

实际报告（本地产物，已忽略不提交）：

- 真实 CLI HTTP：`4370658b5fe44d5b8275405cdf4420b8`。
- 离线正常：`66160569edde409b85104c8028ea1c45`。
- 归属故障：`f5aebf44130f4bcbafcf9eeae5e586cd`。
- 终态故障：`5ad9d0c65eff45a1bfa46e221b5a4dc1`。
- 位置均为 `runs/<run_id>/report.json`；报告导出另有 CLI/API 测试覆盖。

本地功能提交：

| SHA | 内容 |
| --- | --- |
| 71e25e5 | OpenAPI 解析与原始骨架保存 |
| 87a5c3d | 目标隔离、角色与 DSL 校验 |
| c94ce7e | 确定性断言与脱敏报告 |
| b93dcc6 | 受限 HTTP Executor |
| e21d721 | 模拟 target、完整链路与两个故障 |
| 80e9ce7 | CLI 与契约/报告 API |
| 0e17f4d | 标准契约校验、安全回归与断网测试 |
| 8b79f00 | 引用展开预算、真实 CLI HTTP 验证 |

环境：Python 3.11.15；精确依赖见 `requirements-dev.lock`。
本机缺少独立 Python 3.11，使用项目内忽略的 `.python311` 运行时支撑新建 `.venv`。
未访问或修改 CourseInsight；未配置远程仓库，未推送。

已知限制：仅 IP/localhost、HTTP bearer、JSON/CSV 和受限 JSONPath；拒绝递归/动态引用。
尚无外部测试数据自动清理、写入幂等恢复或真实服务接入。
Starlette 对 TestClient 使用 httpx 有一条弃用提醒，未抑制，测试通过。
macOS/Linux 安装脚本尚未实际执行；Windows 脚本本次验证的是已有 `.venv` 分支，新建环境已在开发开始时手动完成。
初次依赖安装需要包源或预先准备的 wheel 缓存；运行与默认测试不需要外网。

**下一准确里程碑：阶段 2 首个可验证模块。** 先查询当时官方 LangGraph 文档，
实现 fake LLM 的 `load_contract -> plan -> validate -> setup -> execute -> verify -> analyze_or_finish -> report`，
验证非法 JSON、未知操作、工具故障、预算耗尽和不篡改断言；暂不接真实模型、MCP 或 Java 服务。

以下保留各模块开发检查点，测试数量按当时实际结果记录。

## 2026-10-11 阶段 1 检查点

- 项目本地 Python 3.11.15 和 `.venv` 已建立，依赖仅安装在 `.venv`。
- 原有测试：`python -m pytest -q -p no:cacheprovider` → **3 passed**。
- 已完成安全契约摘要：OpenAPI 3.0/3.1 JSON/YAML、局部引用、参数、请求体、响应、安全要求。
- 拒绝远程/文件引用、循环引用、重复键、超大输入、过深嵌套与别名循环。
- 模块验证：`python -m pytest -q tests/test_contracts.py tests/test_parser.py` → **15 passed**；`python -m ruff check src tests` → **All checks passed!**
- 已知提醒：当前 Starlette TestClient 对 httpx 发出弃用提醒，测试通过。
- 下一模块：目标隔离策略与人工编写的测试计划 DSL；尚未实现执行器或完整闭环。
- 本检查点对应本地提交 `feat: implement OpenAPI parser`；SHA 在下一检查点登记（避免提交内容自引用）。

### 本地提交记录

- `71e25e5`：契约解析及原始骨架。
- 原始 OpenAPI JSON fixture 随目标隔离与 DSL 模块加入版本管理。

### 目标隔离与 DSL

- 完成固定 IP 目标、localhost 固定到 127.0.0.1、操作白名单、隔离写入授权和预算配置。
- 第一阶段不支持 DNS 主机名，防止校验后 DNS 重绑定；拒绝公网、元数据、未授权私网。
- 完成 Scenario / Step / ArgumentBinding / AssertionPolicy / ExpectedStatus，支持前序步骤绑定。
- 预检未知操作、循环/前向依赖、角色、环境凭据、路径参数、媒体类型及危险表达式。
- pytest：**39 passed**；Ruff 首轮发现一处超长行，已修复，提交前复查。
- 下一模块：Verifier 与可脱敏、可审计的报告。

### Verifier 与报告

- 前一模块提交：`87a5c3d`（目标隔离与 DSL）。
- 完成人工策略来源与 reviewed 门禁、状态/结构字段/条数断言，未知预期为 NEEDS_REVIEW。
- 报告保存契约/计划/策略摘要、步骤运行 ID、结果分类；不保存原始请求体、响应体或认证头。
- 7 个针对性测试通过；Ruff 首轮一处超长行，修复后复查通过。
- 下一模块：HTTP Executor，包含超时、只读重试、轮询、CSV 与请求证据。

### HTTP Executor

- 前一模块提交：`c94ce7e`（断言及报告）。
- 完成 HTTPX 执行、连接/读取/整次运行超时、请求预算和间隔、只读重试、JSON/CSV multipart。
- 所有重定向直接阻断；关闭环境代理、清空跨角色 Cookie、路径值仅允许安全标识符。
- 每个 run 独立状态；发送请求前落盘步骤运行 ID，不提供自动恢复或写入重放。
- 保存每次请求状态码、响应长度/摘要、耗时及安全错误码；异常文本不落盘。
- 针对性 pytest：**7 passed**；Ruff：**All checks passed!**。
- 下一模块：FastAPI mini target、登录/创建/查询/上传/轮询链路与两个故障样本。

### 离线模拟链路

- 前一模块提交：`b93dcc6`（HTTP Executor）。
- 完成独立 FastAPI mini target、固定 JSON 场景、角色环境配置及示例策略说明。
- 正常场景 8 步、9 次请求：双账号登录、创建、查询、跨用户拒绝、CSV、两次轮询、结果条数。
- `ownership` 与 `terminal` 两个独立故障均被对应断言检出；重复运行互不污染。
- 针对性 pytest：**4 passed**；Ruff 首轮两处超长行，修复后复查。
- 仅在 `.venv` 增加 python-multipart 0.0.32，用于 mini target 的 CSV 接收。
- 下一模块：CLI、安全文档导入及报告查询 API、启动验证、完整文档与冻结依赖。

### CLI / API

- 前一模块提交：`e21d721`（模拟链路及故障样本）。
- CLI 支持离线 demo、显式配置目标运行人工计划、查询/导出脱敏 JSON 报告。
- API 保留 /health 与原 JSON 包装格式，增加原始 JSON/YAML 和读取时大小限制。
- 提供只读 `/api/runs/{run_id}`，无任意目标执行 API；错误不会回显不可信文档。
- 包含原有接口的针对性 pytest：**7 passed**；Ruff：**All checks passed!**。
- 下一步：安全边界补充回归、真实本地 HTTP 验证、README 与安装脚本、全量质量门禁。

### 安全边界回归与真实 HTTP

- 前一模块提交：`80e9ce7`（CLI / API）。
- 加入标准 OpenAPI 文档校验，先展开并限制本地引用，再验证；不交给验证器远程引用。
- 补齐原始示例/测试文档的响应及路径参数定义，原有行为断言保持不变。
- 加入超时、请求体上限、轮询耗尽、Cookie 隔离、未知策略停止、数值密码脱敏回归。
- 全量测试：**74 passed, 1 warning**；Ruff：**All checks passed!**。
- 默认测试禁止 DNS 和网络连接；Windows 标准库内部 socket pair 除外。
- 断网夹具首轮误拦截 Windows asyncio 内部通信，已精确放行并全量重测通过。
- 显式真实 loopback 验证：8 步 / 9 请求 PASS，health/spec_import/report_query 均为 200。
- 实际 HTTP 验证 run：`737dba777bc649fd860e8aa4e98b15b8`，临时服务均已关闭。
- 下一步：安装脚本与 README、冻结依赖、交付版本记录；阶段 2 不在本次实现范围。

### 引用解析最终检查

- 前一模块提交：`0e17f4d`（安全回归及真实 HTTP 检查）。
- 增加引用展开后的字节预算；拒绝动态引用和会改变引用语义的约束兄弟节点。
- 实际 CLI run 经真实 loopback 读取 JSON 契约、人工计划与授权配置：8 步 / 9 请求 PASS。
- 新增 4 个引用边界测试后：**78 passed, 1 warning**；Ruff 通过；pip check 无损坏依赖。
- 正常 CLI demo 退出码 0，两个故障 CLI demo 退出码 1，分别停止在第 5 / 7 步。
