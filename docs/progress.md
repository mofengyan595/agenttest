# 开发进度

## 阶段 3B：受控 MCP 能力适配层（2026-10-11）

- 基线 `4ef6acc9f7f01def5406c25a774eee5035d63092`，原 **452 passed**（62.62s）；不重复 Java 写入或真实模型评测。
- 新增 SDK 无关的薄适配层，复用 Executor / Verifier / Vault / TargetConfig / Policy Registry；只运行三个固定 mini target 场景，使用进程内 ASGI 和一次性合成凭据。
- 公共契约沿用筛选器；DSL 只校验不执行，未知目标、凭据、断言、策略、跨步骤 Token 引用均拒绝。客户端无法增加预算或启用 CourseInsight。
- 唯一 invocation ID 在执行前原子占用；同 ID 只返回完成报告，中断/取消/报告失败后不可恢复；新进程也不能重放旧 ID。
- 针对性 **42 passed、1 skipped**（10.57s）；Windows 未授予符号链接创建权限，该项在 Linux CI 再验。Ruff 通过。
- 覆盖创建请求超时、工具异常、创建成功后的报告写入失败，重试/重启均只观察到一次创建发送；所有生成凭据与 Token 不落盘。
- 操作员启动预算默认最多 3 run / 100 tool call，单 run 8 步 / 10 HTTP / 30 秒；进程重启会重置会话总预算，已占用 ID 仍永久不可执行。
- 下一模块：官方 MCP SDK v2 stdio、协议客户端验收；本模块 SHA 在下一检查点记录。

## 阶段 3A：四组 CI 与最终交付检查（2026-10-11）

- 功能提交 `b159bf4cb191f093f7800170e9f36909c528fdd3`、`d558df18a806f9e6e591c02ec85406b753a2f461`、`bb8860bdd60e04de5a4d23af7d3bff78f9e8879b`；验收文档与安全统计 `2d9f1d3e8a6d3fb563fbe3ef9d2caf2397617561` 均已推送原 Public master。
- [Actions 38141751635](https://github.com/mofengyan595/agenttest/actions/runs/38141751635) 对应 `2d9f1d3`，Windows/Linux × base/agent **全部 success**，已逐组读取真实日志。
- agent：Windows **452 passed / 26.75s**，Linux **452 passed / 29.31s**；base：Windows **381 passed、5 skipped、1 warning / 21.83s**，Linux **381 passed、5 skipped、1 warning / 13.49s**。跳过可选图模块，保留既有 Starlette 提醒。
- 四组 Ruff / pip check / 基础 8 步 9 请求回环均通过；两个 agent 的 Fake 自然语言和只读调查回环也通过，学校 API 调用均 0。
- `2d9f1d3` 的最终就绪探测另经真实 Java 验证 PASS：8 次就绪尝试，**0 业务请求、0 创建**；自有进程已关闭。本轮始终只有一次真实课程创建。
- 最终公开检查：107 个文件、269 个历史 blob，实际学校 Key / demo 密码 / 私钥 / Token / JWT 命中 0；任务、两轮评测、四组 CI 文件逐字节未变，.env/.venv/.python311/runs 正确忽略。
- 全部六个 demo 容器的 Compose 来源已统一为当前 `D:\Desktop\project\courseinsight-cloud`，五个原卷保留，初始化容器仅创建未运行。CourseInsight 业务代码未改。
- 本次最后提交只记录上述 CI 与健康探测证据；功能范围结束于阶段 3A，等待审查后再讨论阶段 3B，不提前实施 MCP。

## 阶段 3A：真实 Java 三步验收（2026-10-11）

| 模块 | commit SHA |
| --- | --- |
| 人工契约 / 可信策略 / 固定 DSL | `b159bf4` |
| 隔离门禁 / 显式 Java 执行入口 | `d558df1` |
| 有界就绪探测 | `bb8860b` |

- 原 402 项保留，新增 50 项；最终 **452 passed**（65.00s），Ruff **All checks passed!**，pip check **No broken requirements found.**
- 真实 CourseInsight 源码 `430c23eec8bc31023d8fb43d767bf9d8de164591`，执行代码 `d558df1`，绑定 127.0.0.1:18080；run `d1becff562a149f68452861c95423164`。
- 登录 **200 / 518.687ms**、创建 **201 / 69.665ms**、查询 **200 / 34.583ms**，各请求一次，14/14 检查 PASS。三次 code=0，动态 ID 与 code/name/teacherName 全部一致。
- 仅一次合成课程创建，code `AT3Ae2ecc268599b46d3b9464dbd`；凭据/Token/正文不落盘，原卷保留，课程保留供操作员追溯/清理。Java 自有进程已停止。
- 用户批准将全部 demo 容器来源迁至当前目录；五个数据卷所有已核对元数据保持不变。CourseInsight 跟踪文件仍仅有原先 .gitignore 修改。
- 旧真实回环 8 步/9 请求 PASS；Fake 自然语言两类 3/3、5/5 PASS；调查正常零次、ownership 一次且原失败保留，各创建一次。无学校 API 调用。
- 保留原任务、两轮评测与四组 CI，实际响应只发布安全摘要：[Java 验收](stage3a-courseinsight.md)、[原始脱敏统计](stage3a-http-results.json)。
- 支持边界：只批准固定 ASCII 输入；未完整实现 Java NotBlank/UTF-16 长度语义或通用响应 Schema。无跨用户、异步、自动清理、MCP 或阶段 3B。
- 公开前检查：107 个文件、265 个历史 blob，实际学校 Key/本地 demo 密码/私钥/Token/JWT 命中均 0；原任务、两轮评测及 CI 文件逐字节不变，四类本地产物正确忽略。推送 CI 结果下一检查点记录；不重复真实创建来补写文档。

## 阶段 3A：就绪探测安全边界（2026-10-11）

- 隔离门禁提交 `d558df1`；真实三步 Java 验收已 PASS（run `d1becff562a149f68452861c95423164`），详见后续验收文档。
- 就绪探测增加流式 64 KiB 正文上限、120 秒总时限和重定向立即拒绝；业务仍仅使用 Executor。
- 新增四项离线就绪回归，针对性 **50 passed**（3.67s）；Ruff 通过。未再次发送真实创建请求。
- 最终全量、公开前检查与 CI 在最后检查点记录。

## 阶段 3A：隔离门禁与显式 Java 验收入口（2026-10-11）

- 契约模块提交 `b159bf4`。新增源码版本/文件摘要门禁、容器/端口/数据卷/数据库/Compose 来源预检，以及默认只读的显式验收脚本。
- 脚本拥有并限制 Java demo 进程：回环 18080、固定 demo 数据库/Redis/MQ/AI Stub、清除继承的 Spring/Java/model 配置，Maven 离线启动，结束后停止自己启动的进程。
- 新增 **46 passed** 针对性测试（4.14s）；全量 **448 passed**（62.47s）；Ruff、pip check 通过。原 402 项与四组 CI 保留。
- 初始预检发现 Docker Compose 来源仍指向已迁移的旧目录。用户明确批准按新目录重新创建 demo 容器并保留数据卷；五个依赖已健康，5 个卷名称、创建时间、挂载点及元数据全部未变。未执行卷删除或重建。
- 严格隔离预检通过，尚未发送业务 HTTP；下一步仅一次显式三步真实验收。
- 进度文档的换行格式将在最终文档提交恢复为原仓库 LF，内容与历史均保留。模块 SHA 下一检查点记录。

## 阶段 3A：人工契约与固定三步适配（2026-10-11）

- 基线 `405a39d20d9f1a43780c15fef062ef55ac857a3f`，原 **402 passed**（91.41s）。
- 新增 CourseInsight 三操作契约、默认禁写目标、独立可信策略、一次性 ASCII 合成代码与固定 DSL。
- 复用 Executor/Vault/Verifier，无核心引擎重构、无依赖变化；登录 Token 与标量创建 ID 已适配。
- 针对性 **19 passed**（3.53s），Ruff 通过；覆盖四字段一致性、code=0、角色、动态依赖、凭据泄露、重复代码、超时与禁止写重试。
- Docker 已恢复可连接，五个 demo 依赖健康；尚未发送业务 HTTP。隔离门禁与真实 Java 验收下一模块完成。
- 本模块 SHA 在下一检查点记录；保留 CourseInsight 现有修改及两轮评测。

## 阶段 2B：远端四组验收（2026-10-11）

| 模块 | commit SHA |
| --- | --- |
| 可信能力目录 / 零默认预算 / 报告类型 | `dc2b268` |
| 受限会话 / 单次调查 / 故障注入 | `0d7d31b` |
| 工作流 / 演示 / 真实回环 / CI / 文档 | `6aa283ddaa89bbee9506b1382c156a57d2258b2f` |

- 上述功能提交已推送现有 Public master，工作区干净；[Actions 38130960971](https://github.com/mofengyan595/agenttest/actions/runs/38130960971) 对应 `6aa283d`，四组全部 success，已读取四组真实日志核对。
- Windows/Linux agent 各 **402 passed**；base 各 **331 passed、5 skipped、1 warning**（无可选图依赖时跳过五个模块，既有 Starlette 提醒）。四组 Ruff、pip check 和原 8/9 回环均通过。
- 两组 agent 的原自然语言 3/3、5/5 以及新增调查回环全部通过：正常 5 HTTP / 0 调查；ownership 6 HTTP / 1 调查，原 ASSERTION_FAILED 保留；各创建 1 次、登录 2 次，真实模型调用 0。
- Windows CI 调查 attempt `3e549fcaaa444ea8a89620a9c1aba42c`（200、1.589ms）；Linux `de8276b24cf741179ced11ca80ed5ebb`（200、42.829ms）。均与本地响应摘要一致，不保存正文。
- 公开检查：95 个文件、246 个历史 blob，实际学校 Key / 私钥 / 云 Token 命中 0；.env/.venv/.python311/runs 四类产物均被忽略；六个任务与两轮评测文件相对 `7003f66` 逐字节未变。
- 当前交付范围完成。下一步先审查本次能力边界及故障证据，再决定是否扩大只读目录；不自动启用真实模型调查、修复或重规划。

## 阶段 2B：工作流与本地 HTTP 验收（2026-10-11）

- 运行会话提交 `0d7d31b`，能力目录 `dc2b268`；原 `7003f66b77018897b0c12b55cd9a46051b1b0eb8` 及全部提交保留。
- Workflow.execute 仍只调用一次 Executor，调查在原会话关闭前完成；新增显式 investigation-demo，普通入口仍默认禁用。FakeAnalyzer 可选择本人或他人的批准能力，原业务断言不变。
- 新增四组 CI 中 agent 的真实只读回环检查，Windows/Linux × base/agent 矩阵保持；无新增依赖、无学校 API 调用。更新 README、AGENTS 与设计文档，保留两轮结果和原任务。
- 最终全量 **402 passed**（81.48s）：保留原 327 项，新增 75 项；Ruff **All checks passed!**，pip check **No broken requirements found.**
- 真实 HTTP：旧工作流 8 步/9 请求 PASS、health/spec_import/report_query 均 200；旧 Fake 自然语言路径 3/3 与 5/5 PASS；新调查正常 5 请求/零调查，ownership 6 请求/一次调查，均创建 1 次、登录 2 次。
- 最新调查 run `4506ffc1ffc747ddbcd1fbfe3488098d`；attempt `27181d313db54f1090cba0c6058910fb`，200、5.639ms、58 字节。原 ASSERTION_FAILED 保留，补充读取 PASS。完整脱敏记录见 [验收证据](stage2b-validation.md)。
- 故障覆盖独立预算、原 ID/名称绑定、非法能力/策略/写操作/状态 GET、跨 run、配置漂移、认证过期、超时/工具/取消、发送前落盘、永久报告失败。读取已有 run 不能恢复执行。
- 已知限制：仅单次 FakeAnalyzer GET 调查；未测试操作系统硬杀进程或物理内存擦除，永久报告失败不能保证最终证据落盘；无自动恢复、修复、真实 LLM 调查或重规划。
- 本模块功能提交后推送原 Public master，远端 CI 待核对；模块 SHA 与 CI 结果在下一检查点记录。

## 阶段 2B：受限运行会话与单次调查（2026-10-11）

- 能力目录提交：`dc2b268`。复用原 Executor._step / _request、Verifier 和 Vault，未新增 HTTP 引擎或依赖。
- 调查仅在原 ASSERTION_FAILED、有同 run 已通过来源响应时进入；FakeAnalyzer 只得到固定诊断和经过全套校验的能力/角色/临时资源句柄。
- 原业务结果先落盘；调查独立请求预算、单轮/单次选择、整体时间限制，沿用目标限速/正文限制/禁止重定向。已建立认证可复用，不重发登录或创建。
- 冻结目标/契约/策略/预算/原结果并检测漂移；结束及取消时清除状态、Vault JWT/环境副本；旧 run/report 一律不可恢复执行。
- 针对性 **69 passed**（19.13s），Ruff 通过，覆盖名称/ID 失败、伪造选择、跨 run、零预算、超时、取消、报告失败及发送前 attempt ID/预算落盘。创建次数均保持 1。
- 较早全量 **378 passed**（60.96s）、pip check 通过；新增细分故障测试后将在工作流连接完成时再做全量验收。
- 尚未完成工作流及真实 HTTP 验收；未调用学校 API，未调整两轮评测任务或原始结果。运行会话提交 SHA 下一检查点记录。

## 阶段 2B：能力目录与零默认预算（2026-10-11）

- 起点 `7003f66b77018897b0c12b55cd9a46051b1b0eb8`；工作区干净，原 327 项测试、Ruff、pip check 实际通过。
- 新增操作员 ReadOnlyCapability Registry、最多单轮/单次的独立预算和严格 AnalyzerSelection；默认禁用且所有预算为零。
- 首批仅批准隔离 mini target 的 getCourse 本人读取及跨用户查询；getBatch 状态推进查询不在目录内。能力不包含模型自行声明的审批字段。
- 报告向后兼容地增加 investigations，HTTP attempt 增加唯一 ID；资源引用只允许会话生成的不透明句柄。
- 针对性测试 **25 passed**（1.60s），Ruff 通过；尚未连接执行会话，不能宣称调查链路可用。提交 SHA 在下一检查点记录。

## 第二轮远端验收（2026-10-11）

- 结果交付提交 `22387cccea4002552c8d5289a80f2e4ffd134b75` 已推送原 Public 仓库 master；相对起点新增五个小步提交，旧提交完整保留。
- [Actions 38128347535](https://github.com/mofengyan595/agenttest/actions/runs/38128347535) 对应上述提交，四组全部 success：Windows/Linux agent 各 **327 passed**；base 各 **266 passed、4 skipped、1 warning**（可选图模块跳过、既有 Starlette 提醒）。
- 四组 Ruff、依赖检查、旧真实回环通过；两个 agent 组 3/3 与 5/5 自然语言回环通过，CI 真实模型调用 0。已读取四组实际日志核对。
- 冻结版本 e5b44b9 与交付提交的 src/scripts/tests/依赖/CI/任务书逐字节一致，后续只有报告和文档；没有将离线修复或新结果回填首轮。
- 本次完成安全诊断、意图门禁、第二轮 16/18 实测及 2B 最小设计；仍未实现动态只读调查、恢复或重规划。下一里程碑按设计先做独立能力目录/预算/FakeAnalyzer，避免重放创建。

## 第二轮真实评测完成（2026-10-11）

| 本次模块 | commit SHA |
| --- | --- |
| 固定安全诊断 | `2a7e4f6` |
| 确定性意图门禁 | `b0d8e16` |
| 冻结与逐条证据 | `d24eeea5f32adf336e865bbea5023d8ffc5cfe41` |
| 2B 最小设计 / 第二轮冻结版本 | `e5b44b9a201156d4544d3d77948466499e37300f` |

- 冻结后串行执行原 T01–T18，18 次 ecnu-plus 生成、无重试/补跑，25595 Token（19191 输入 / 6404 输出），预算 60000；额外 GET models 200、零生成 Token。
- 计划有效及策略正确 17/18，意图及端到端成功 16/18；consistency 成功 7/9、ownership 9/9。首轮 13/18 及任务、标注、所有历史提交保留。
- T04 INTENT_NAME_MISMATCH（计划合法但错名）、T05 MODEL_NO_APPROVED_PLAN（显式弃权），均 NEEDS_REVIEW、业务请求 0。T12/T18 本轮通过，未倒改首次失败。
- 均值 11625.83ms、P95 17390ms；Token 比首轮多 9.70%，平均延迟多 33.37%。不做单因素因果推断或广泛泛化声明。
- 实际核对 18/18 返回/落盘/逐条日志/冻结清单一致；隔离目标共 66 次 HTTP 请求。冻结代码、Prompt、Schema、配置、任务集逐条检查未变。
- 全量 327 passed，Ruff、pip check、8/9 + 3/3 + 5/5 回环通过；沿用 Python 3.11 .venv，无新增依赖。四组无 Key CI 文件原样保留。
- 发布前检查 84 个代码/设计文件和 211 个历史 blob：实际 Key、私钥/云 Token 格式命中均 0；本地产物忽略正确。新增公开统计提交前再次检查。
- [第二轮报告](stage2a-round2-evaluation.md) 与 [原始脱敏指标](stage2a-round2-results.json) 独立新增，首轮原文件不变。T01/T05/T09 首轮精确原因保持未知。
- 2B 只提交设计与验收，尚未实现调查/恢复；本轮预检失败禁止发调查请求。下一步为能力目录/独立账本/FakeAnalyzer 的受控最小实现。
- 当前文档记录完成后推送，远端 CI 结果随后单独核对并记录。
- 结果文档提交前再次检查：86 个公开文件、214 个历史 blob，Key/私钥/云 Token 命中 0；四类本地产物被忽略，首轮任务/结果/评测文档及 CI 原文件逐字节未变。历史保护 8 passed（8.59s），Ruff 通过。

## 阶段 2B 最小设计 / 第二轮冻结准备（2026-10-11）

- 冻结模块：`d24eeea5f32adf336e865bbea5023d8ffc5cfe41`；诊断 `2a7e4f6`、意图 `b0d8e16`，全量 327 passed，Ruff / pip check / 三条真实回环路径通过。
- [2B 设计](stage2b-readonly-design.md) 定义只读能力审批、原 run 资源引用、Vault 内认证、独立预算、不可覆盖的原失败和不自动重放的恢复语义。GET 也须审阅无副作用，状态推进查询不自动获批。
- 本次只提交设计及十类最小验收场景，不实现分析器/恢复/MCP/RAG/Multi-Agent。设计细节将在第二轮结果记录后补充依据。
- 提交前冻结/历史保护测试再次 **8 passed**（10.50s），Ruff 通过；代码无新增修改。
- 准备固定本提交作为第二轮版本；保持已批准任务和首轮结果文件不变，最多 18 次学校生成调用、60000 Token 总预算、2048 输出、30 秒单次、temperature=0、零自动重试。

## 第二轮可靠性：冻结与证据留存（2026-10-11）

- 意图模块提交：`b0d8e16`，前一模块全量 318 passed，未修改原任务或 Golden Labels。
- 真实评测前要求干净提交，固定完整 commit/tree、Prompt、Schema、任务集、模型参数和预算；逐条及结束检查漂移，串行、零自动重试。
- 结果、冻结文件禁止覆盖；逐条写脱敏 JSONL。保存请求摘要及细粒度失败码、输入/输出 Token，不保存原始模型响应或秘密。
- 学校协议显式 temperature=0，其余继续使用官方 json_schema/max_tokens/thinking disabled；不引入新 SDK/依赖。
- 冻结/漂移/历史保护及适配器相关测试实际 **44 passed**；全量 **327 passed**（62.01s），Ruff 通过，pip check 无损坏依赖。
- 真实本地 HTTP：旧图 8 步/9 请求 PASS，health/spec_import/report_query 均 200；自然语言两路径 3/3 与 5/5 PASS，临时服务已关闭，真实模型调用 0。
- 第二轮尚未开始，首轮 13/18 文件及已批准任务保持不变。下一步提交 2B 最小只读设计，冻结后执行完整 18 条。

## 第二轮可靠性：确定性意图边界（2026-10-11）

- 安全诊断提交：`2a7e4f6`；保留全部历史，未接入新依赖。
- mini_target_nl_v1 在执行前检查明确课程名、创建者、同一动态资源和跨用户拒绝关系；不读取 Golden Labels，不修改计划、Policy Registry 或业务断言。
- 开放/歧义/额外目标 NEEDS_REVIEW。T12 的错名、错创建者、缺少跨用户路径均在零 HTTP 请求时阻断。
- 18 条原始任务的解析由独立测试与人工标注比对，另测试未见课程名和反向权限关系。固定 Fake 的意图不符现在为 NEEDS_REVIEW，能力指标仍独立计算。
- 相关测试第一轮 55 passed，关系规则补充后 36 passed；全量 **318 passed**（28.83s），Ruff 通过。详见 [可靠性说明](planning-reliability.md)。
- 下一模块：冻结评测运行配置与保护历史输出；未进行第二轮真实调用。

## 第二轮可靠性：安全诊断（2026-10-11）

- 起点 `9dd50de1871f25e5aeecab63df21410707a238be`，原有 264 项测试实际全通过。
- JSON、模型 Schema、DSL、OpenAPI、策略及依赖增加固定诊断码；重复步骤与未知操作独立区分。Executor 运行时依赖失败同样记录安全诊断。
- Pydantic 只取错误类型和受控字段路径，未知键遮蔽；不保存错误输入、context、原始响应或异常文本。
- 新增 18 项回归；全量 **282 passed**（34.82s），针对性 **82 passed**，Ruff 通过。
- 首轮 T01/T05/T09 的精确原因无法恢复，继续标记未知；原 13/18 及历史文件不变。本模块 SHA 在下一模块记录。
- 下一步：执行前的确定性意图检查；本次未调用真实模型。

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
