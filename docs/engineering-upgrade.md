# 工程升级：意图门禁与规划约束（2026-10-12）

本轮是阶段 5 之后的新里程碑：核验现状、修复 P0 意图一致性、改进可离线验证的规划输入。没有收费模型调用、真实 Java 请求、数据库写入或新依赖；提交与推送按用户后续授权执行。以下新用例为开发者构造的回归样本，不是独立人工标注的模型泛化评测。

## 功能覆盖清单与问题核验

| 能力 | 代码与现有证据 | 本轮核验结论 |
| --- | --- | --- |
| LangGraph 编排 | workflow.py；test_workflow / test_natural_workflow | 已有六节点、单次规划、一次 Executor 调用、固定分类、超时/预算和关闭云追踪；没有恢复/自动修复环路 |
| 结构化规划 | llm_planner.py；两轮 2A、4A/4B 原记录 | 已有一次受限调用、严格草案、重复键/用量/输出大小检查和 abstention；结构化输出不能保证角色、顺序或目标正确 |
| 契约与策略授权 | contracts.py / request_validation.py / policies.py / dsl.py | 已有本地引用、约束子集、操作白名单、审核策略、来源 selector、同/异角色及等值关系；没有模型自审策略 |
| 需求满足 | intent.py / courseinsight_nl.py / Workflow.validate | mini 和 Java 有受控意图检查；原通用入口会跳过，且通用自定义校验器被忽略，属于真实 P0 缺口 |
| HTTP 与认证 | executor.py / credentials.py / safety.py | 已有唯一执行引擎、Vault、认证响应禁止绑定、固定 IP、拒绝重定向、写入不重试和请求前审计；不支持任意 DNS 目标 |
| 判定与证据 | verifier.py / report.py | 已有五种 outcome、脱敏检查、attempt ID、摘要/延迟、Windows 原子发布重试；此前缺少明确的任务完成声明 |
| Java 边界 | courseinsight*.py、独立运行脚本、3A/4A 证据 | 已有批准的 admin 登录/创建/查询、14 项断言和一次性 claim；不支持跨用户假设、自动恢复或自动清理数据库 |
| MCP | mcp_service.py / mcp_server.py；3B 证据 | 五项固定 stdio 能力，校验不授权执行，同 invocation 不重放；不能执行任意 DSL 或 Java 写入 |
| 失败调查 | investigation.py / readonly_runtime.py | 已有同 run 不透明资源引用、一次批准 GET、独立预算和原结果不可变；仅 FakeAnalyzer，没有真实模型 Token 门禁 |
| 评测与缺陷注入 | evaluation.py / defect_evaluation.py；T01–T18、B01–B04 | 已有人工/Fake/真实分层、配对正常对照、六种缺陷、未执行/inconclusive 分类；没有广泛独立测试集或契约候选生成 |
| 可观测性 | ModelCallStats / StepResult / AttemptEvidence | 已有真实适配器用量、请求耗时、固定层级诊断；尚无完整节点耗时/端到端耗时、跨轮通用汇总 |
| 离线与兼容性 | conftest.py、showcase.py、ci.yml | 默认禁止意外网络；保留 Windows/Linux × base/agent CI。本轮本地验证是 Windows，不能替代新的 Linux/远端 CI |

没有重建 Executor、Policy Registry、Vault、MCP 或评测引擎。LangGraph 节点/条件分支继续沿用[官方 Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)，不升级冻结依赖。

## P0：必要性、修改与兼容性

修改前用两个新增离线回归复现：合法的列表计划面对“订单状态与库存一致性”需求返回 PASS；操作员传入会拒绝该任务的通用 intent_validator，也仍返回 PASS。两项都不应执行 HTTP。

修复包括：

1. Workflow 默认 `task_mode="natural_language"`。mini 使用已有内置意图检查，其他场景必须有操作员提供的确定性 intent_validator；缺失则 `NEEDS_REVIEW / INTENT_VALIDATOR_REQUIRED`，零 HTTP。Java 继续使用锁定输入及 ID 来源的现有校验器。内置 mini 校验不能被回调覆盖。
2. 明确批准的固定计划使用 `task_mode="operator_plan"`，限固定 FakeLLM 载入方案；真实/自定义 Planner 不能进入该模式。模型 DSL 不能设置此字段。CLI `run` 仍走人工计划；`workflow` 省略 `--user-task` 使用固定计划，显式给出需求就进入自然语言门禁；`agent` 始终要求意图检查。
3. intent_validator 只接收计划副本，不能充当修补执行计划的钩子。mini 的角色登录必须在该角色受保护步骤之前，即使环境预置 bearer 也不能掩盖任务要求；资源来源及 selector 必须匹配。
4. 报告分别记录 `dsl_valid`（DSL/契约/目标预检）、`policy_authorized`（Registry 授权）、`intent_verified`（受控需求覆盖）。保留 `plan_valid` 为前两层联合结果，兼容原评测口径。
5. `task_satisfied=true` 只用于自然语言意图已验证、业务断言 PASS 且 Workflow 完整结束；失败为 false，人工计划为 null。旧报告新增任务字段默认 null，不追认历史需求完成。固定计划 PASS 的分析码为 `APPROVED_PLAN_COMPLETED`。
6. 最终报告发布失败、或报告节点之后仍耗尽图步数时取消 Workflow 完成声明，防止已算出的任务成功状态残留。完成以整张图正常返回为准；六节点配合 graph_steps=6 的边界已实际复现。

影响范围限 Workflow/CLI 模式、意图校验、规划公共输入和报告附加字段。**受信任策略、状态码、断言、目标配置及真实写入授权均未修改。** 外部直接调用 Workflow 执行固定样本的使用者须显式设置 operator_plan；这是消除默认绕过所需的兼容性收紧。自定义校验器仍属于操作员代码，不能由模型文本加载。

## P1：历史诊断与改进边界

| 历史失败 | 能确认的事实 | 本轮处理 |
| --- | --- | --- |
| 4B B02 | DSL_AUTHENTICATION_ORDER，零 HTTP，归属缺陷未执行 | 在公共输入显式列出每个角色的 login_operation_id 和 before_operation_ids；错误计划仍拒绝 |
| 第二轮 T04 / 首轮 T12 | 创建名称摘要与任务不符 | 继续逐字检查名称，保留 Prompt 精确复制要求；不把模型名称改成正确名称 |
| 第二轮 T05 | 空 steps，明确弃权 | 继续 NEEDS_REVIEW，不自动补计划 |
| 首轮 T01/T05/T09 | 只有粗分类，原文未保存 | 不推测具体顺序、Policy ID、字段或资源错误 |
| 首轮 T18 | 返回/落盘不一致；后续离线复现过 Windows 文件锁 | 沿用既有发布修复；不把后续复现当成原调用的确定根因 |

原目录按 business_scenario 筛选，仍把两个角色及所有四种策略交给单角色任务。本轮复用 interpret_task：已识别任务只暴露必需策略及对应角色；所有候选仍是 Registry 授权的子集。未知任务不猜测目录范围，最终必须通过意图门禁。

精简 mini Prompt 的重复参数说明，明确认证和来源步骤都必须先于消费者。**不自动排序、补登录、补查询、改参数、重试模型或放宽校验。** Java 独立 Prompt 和批准 fixture 不变。

| 可复算的离线指标 | 修改前 | 修改后 |
| --- | --- | --- |
| mini SYSTEM_PROMPT UTF-8 字节数 | 1,701 | 1,301 |
| 新单角色任务的候选策略/角色数 | 4 / 2 | 3 / 1 |
| 同任务候选 policy-role 组合数 | 8 | 3 |
| 新归属任务候选 policy-role 组合数 | 8 | 5 |
| 两项通用入口门禁回归 | 2 项错误 PASS | 2 项待审、零 HTTP |

字节数不等于 Token 数，候选收窄不等于真实规划成功率提升。真实模型有效计划/意图/端到端改善均未测量。历史 4B 仍是有效计划与端到端 1/2、总体 NL 缺陷覆盖 3/4；两轮 2A 和全部历史 JSON 原样保留。

## 离线验收

实际命令、完整通过数与未验证项见 [本轮进度](progress.md)。新增回归覆盖：通用入口、遗漏本人读取/他人拒绝、额外查询、角色错误、登录顺序、来源/selector 错误、读取顺序、未知任务、模型伪造模式、固定方案兼容、报告失败及旧报告兼容。

模型 HTTP 响应模拟测试只证明适配器及结构化请求格式；FakeLLM 只证明固定计划和门禁。本轮不计算真实模型成功率或虚构 Token/延迟改进。现有冻结任务在默认回归中仅验证兼容性，未用于新增 Prompt 迭代或更新历史成绩。

## 后续里程碑，按收益与成本排序

### 1. OpenAPI 候选发现 MVP：收益高，成本中

新增一个纯函数模块和只输出文件的 CLI，输入现有 ContractSummary、TargetConfig、PolicyRegistry；默认无 LLM、无 HTTP。复用本地引用解析和 request_validation 支持的 Schema 子集。

- 候选记录：candidate_id、operation_ids、scenario_kind、输入草案、schema_basis、policy_refs、缺失依据、审核状态。输出候选文件及静态覆盖摘要，不自动授予执行权限。
- 先支持 scalar enum、数值 min/max/exclusive 边界、字符串长度、required；复杂组合/正则/未知 format 继续待审。正常值也必须满足批准策略的绑定要求。
- 非法类型、缺字段、越界可作为**静态候选**。OpenAPI 未必规定具体业务错误码，不能自行写 400/422 断言，不能复用“正常请求返回 201”策略批准负例。
- 当前 Executor 会拒绝契约外输入，这是现有安全机制。若将来要真正发送负例，须单独设计并审阅“允许指定约束违规”的可信负例策略与安全门禁；不能全局跳过 validate_request。MVP 不开放该能力。
- 从 source_policy_id / source_selector / role_relation 和登录元数据建立批准资源关系图，输出可组合流程与缺失依赖；不从相似接口名猜业务链。禁止新增未知 Policy ID 或自动执行写入。
- 静态 Operation Coverage = 有候选的唯一操作 / 契约操作；另报批准且可验证操作比例、配置排除项和缺失 operationId。Scenario Coverage 分母必须是显式列出的“操作 × 场景类型”清单，不声称穷尽所有业务。
- 缺陷机制覆盖只列已有六类的可信映射；**静态涉及、批准可执行、实际已执行、实际检出**分别统计。
- 验收：本地 3.0/3.1 fixtures、相同输入稳定输出、无远程引用/凭据/HTTP、未知策略待审、越界候选不被执行；先不接新增 Java 写入。

### 2. 新的 Agent Eval v2：收益高，成本中

保留 T/B 集合与历史记录；另建版本化 C 系列候选集，经审核冻结后才做真实评测。建议每类先 3 条、共 27 条：单接口、多步依赖、认证权限、参数边界、错误状态码、一致性、异步、无法支持、注入/越权。尚不支持的 CSV/异步规划和负例执行列为 abstention/待审能力，不能用固定 DSL 冒充模型支持。

沿用 evaluate / detection_metrics / EvaluationFreeze，不重建执行器。独立标签包含所需操作/角色/参数/依赖、可支持性、批准机制和拒绝依据，标签不进入 Planner 输入；本轮新回归是开发集，未来留出集不能重复拿来调 Prompt。

| 指标 | 明确口径 |
| --- | --- |
| Plan Validity Rate | DSL/契约/策略通过的支持任务 / 预定支持任务；预算未执行仍保留在分母，另报 attempted |
| Intent Satisfaction Rate | 独立标注核验操作、角色、数据及全部依赖正确 / 预定支持任务；不只读取 intent_verified |
| End-to-End Success Rate | 上述通过、正常目标断言 PASS 且完整结束 / 预定支持任务 |
| Fault Detection Coverage | 正确检出机制 / 预定机制；另报已执行条件检出率和未执行数 |
| FP / FN | 正常对照 ASSERTION_FAILED 是 FP；故障执行 PASS 是执行 FN；规划未执行、错误机制和 infra/tool 单列 |
| Latency / Token Usage | 只有真实请求才记模型实际 usage/延迟；未知用量 null，预算停止；有样本才计算样本量、nearest-rank P50/P95 |

结果增加来源声明：人工固定计划、固定 FakeLLM、模拟模型响应、真实模型，不能只凭 baseline="real" 字符串证明真实调用；测试注入的 MockTransport 必须标注。冻结源码、Prompt、Schema、目录筛选版本、任务、模型配置/预算和评测器。真实运行需要新授权、干净冻结提交和新输出目录；不补跑失败抬高指标。

现有 evaluation.py 的原 2A 汇总主要使用 attempted 分母，defect_evaluation 已把预算未执行的支持任务保留在分母；v2 统一 requested/attempted 分母，历史文件不回填。现有套件仅覆盖两个自然语言意图及固定异步机制，不能解释为九类均已实现。

### 3. 本地可观测性补全：收益中高，成本低至中

扩展 RunReport，不引入外部平台。复用已有 ModelCallStats、AttemptEvidence 与诊断层；增加单调时钟的节点/端到端耗时、退出节点/失败码、业务与调查分开的 HTTP 次数和耗时。tool_calls 仍是 Executor 调用次数，不能冒充 HTTP 次数。

汇总只读取脱敏报告，按来源、套件/源码/Prompt 摘要及预算分组跨轮比较；不给不同模型别名/不同用例拼一个提升率。无真实 usage 的 Fake/Mock 不产生模型成本估算。只记录固定字段，继续关闭默认云追踪，不上传目标/凭据/正文；有真实观测样本才报告分位数。

验收包含取消、报告失败、预算耗尽、调查结果不可覆盖原结果和脱敏快照；不新增服务依赖。

### 4. 真实 LLM 只读调查：收益中，成本中，最后启用

当前安全门禁已具备，但 InvestigationBudget 尚无 Token 限制，不能直接把 Planner 接到 FakeAnalyzer 位置后宣称安全完成。

先增加独立 Analyzer 协议/适配器：输入仅现有 failure 固定字段和 choices；输出只允许 AnalyzerSelection 或显式弃权。能力、角色和不透明句柄做结构化约束，本地仍用现有 approved 三元组重新授权。模型不能提交 URL、Headers、凭据、selector、原始 ID 或断言。

增加独立输入/输出/总 Token、响应字节、单次/总耗时预算，最多一次调用、一次批准 GET，无重试；API 失败、用量未知或超预算不发送调查 HTTP。记录独立模型统计，不修改原 ModelCallStats 或业务结果。

离线先测注入、原始正文/密码/JWT 泄露、伪句柄/跨 run/错角色、写入选择、超时/取消/报告故障和不可改判。全部通过后，再申请真实模型单次调查实验；真实 Java 只读能力必须另行审阅无副作用依据，当前 mini 的批准不能迁移过去。

## 可以证明的技术亮点

- 用确定性意图门禁解决“计划合法、接口 PASS、需求却未完成”的工程问题；有修改前失败与修改后零请求拒绝的回归证据。
- 将 DSL/契约检查、策略授权、受控意图覆盖与最终任务完成声明分开，并保留固定人工计划及旧报告兼容。
- 从可信目录与受控任务关系收窄 policy-role 候选，提供认证前置约束；拒绝自动修补和断言降级，能说明为何结构化输出仍需本地校验。
- 保持原有 Python 测试平台对 Java/Spring Boot 隔离 demo 的集成，以及 Vault、可信策略、六类故障对照、MCP 与离线质量门禁。

候选生成、九类 Eval、真实 Analyzer、节点统计仍为后续方案，不能写成已实现；没有新的真实模型成功率、生产吞吐量或自动化提效数据。
