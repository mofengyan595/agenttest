# AgentTest RAG 必要性离线评估

日期：2026-10-13。分类：**DEVELOPMENT_ONLY / UNREVIEWED**。独立留出集：否。

**决策：当前生产路径保持 No-RAG，不开发 Embedding／向量数据库。允许保留独立的离线检索实验。**
当前扩展任务存在业务信息缺口，但还有契约、可信策略和意图适配缺口；RAG 无法代替后者。
这次 BM25 基线也未达到可靠补齐业务证据的程度。C 未运行，不能推断其收益。

## 来源与边界

- AgentTest 基线：`0e684b2a5522f33ed45bc13f79b6feccc49af7f4`。开始时已有 README、batch、Candidate Discovery、进度及技术总结修改和静态证据，本轮保留。
- CourseInsight：通过 GitHub 只读连接核对 `main` 为 `fbaf08f585de7839050899065ada3c36de5f3fd1`，本地 HEAD 相同。语料中的当前 Java 文件均与该提交逐文本核对，随后重新核对全部 27 份来源的实际字节摘要。
- [旧来源清单](../examples/courseinsight/source-review.json) 固定 `430c23e`；[旧 OpenAPI](../examples/courseinsight/openapi.json) 仅三项课程链操作。现有 Java profile、Prompt 和来源门禁仍保持原状。它不是当前 Java 全量 OpenAPI，不能用它证明 batch／评论分析已经适配。
- 本轮重新核对旧清单：当前磁盘和当前提交 Git blob 各有 14/16 的原始字节摘要与旧清单一致；CourseController／CourseCreateRequest 的 CRLF 换行导致另两项不同，当前磁盘按 LF 比对后 16/16 一致，且16个当前文件与当前提交按 LF 比对均相同。既有三步范围没有被故意删减。提交 SHA 仍未批准，批处理／评论分析文件也未因此自动纳入可信契约；实验不能靠旧契约宣称当前 Java 可执行。
- 当前 Java API 依据实际 Controller、Service、DTO、Mapper、测试及文档审查。`pom.xml` 未检出 springdoc/swagger；本轮未获取运行时 OpenAPI，未启动 Java，也没有声称运行验证了这些规则。
- [冻结语料](../experiments/rag_necessity/corpus.json) 包含真实原文快照、文件路径、行号、系统、目标修订、来源版本、原文件与原文 SHA-256、可见性和来源类型。已有未提交文档用 `WORKTREE:<内容摘要>` 表示，不能冒充已提交版本。
- 真实历史案例取自已有脱敏 batch 静态证据。旧版本样本取自真实 `430c23e` 的进度 DTO；其规则与新版本相同，仍应按精确版本过滤。冲突／私有样本明确标注为人工构造的负例，不能当作真实业务事实。

本轮零收费模型调用、零真实 Java 请求、零数据库写入；没有安装依赖、修改 CourseInsight、生产 Planner／Executor、正式 Eval v2 或历史 A/B 成绩，没有提交推送。

## 信息需求审查

| 信息 | 目前所在位置 | 是否需要额外检索 |
| --- | --- | --- |
| method/path、请求参数、必填约束、鉴权、允许的 Operation/Role/Policy | OpenAPI、TargetConfig、PolicyRegistry、planning_context | 现有批准范围不需要 |
| 资源依赖来源、Selector、同角色要求、关闭参数集合 | Policy binding_rules，完整传入 planning_context | 不需要；不能删除后制造检索优势 |
| mini 两行 fixture、五步链、终态 SUCCEEDED | 固定 BATCH_PROMPT、草稿 Schema、Policy、Intent | 不需要；公开 catalog 不暴露全部断言，但可信引擎持有它们 |
| mini 轮询次数、状态、超时、结果数量与成功判断 | 可信 Policy／Executor／Verifier；catalog 仅提供 poll_allowed | 执行参数不得由检索补写；若需展示，优先可信结构化投影 |
| Java CSV content/rating、BOM、1–5 整数、200 行 | 最新解析器、解析器测试和 README；旧三步契约未覆盖 | 扩展规划需要额外业务信息，但可直接提供版本化资料包 |
| FAILED 等待重投与终止失败的区别，100% 包含失败 | 最新进度 Mapper、DTO、开发文档 | 有额外语义；Schema 的字段类型不能表达完整计算规则 |
| 结果列表含未成功任务，条数不足以证明成功 | 最新结果 Mapper 的 LEFT JOIN、结果 Service | 有额外语义；不能移植 mini 的未就绪 409 |
| analysis 与 analysis-task 的权限差异 | 最新业务文档、Service 与 Controller | 有额外语义；检索只提供待审依据，不能授予新角色 |
| terminal/result_count 的失败表现与诊断 | 历史脱敏案例、测试规范 | 有解释与案例发现价值，不产生新的 PASS/BUG 或断言 |

`planning_context` 会剔除自由描述、examples/defaults 等内容，并只投影批准操作；这是一项既有安全边界。
缺少自然语言业务注释不等于删除了 A 有权获得的信息。实验保存完整现有上下文，也没有把隐藏断言改成检索内容来获得执行权限。

敏感或有权威性的信息始终来自操作员配置：真实凭据、目标地址、允许主机、写入许可、Operation／Role／Policy、CSV 字节、断言、重试和轮询限制。文档中即使写了这些内容，也只是引用，不能合并进可信对象。

## 实验方法

[12 条问题及逐条证据标签](../experiments/rag_necessity/suite.json) 在首轮运行前建立；
[协议](../experiments/rag_necessity/protocol.json) 冻结语料、问题、实验代码、Planner、DSL、Policy、Executor 和相关配置。
首轮后没有根据分数改问题、关联文档、分词、BM25 参数或预算。

语料共 27 个文档片段：24 个当前来源、1 个真实旧版本、1 个同版本冲突样本和1 个 PRIVATE 样本。
真实来源既包括业务文档、测试规范与历史案例，也包括用于验证规则的源码。
关联标签由同一代理编写和计分，均未独立审阅，不能当作正式 Golden Tests。

- **A**：完整现有 No-RAG `planning_context`。mini 使用既有 batch profile；Java 使用既有三步课程 profile。固定随机课程 code 仅用于可复现，三个方案共享。
- **B**：标准库实现 BM25，`k1=1.5, b=0.75`，正值平滑 IDF。ASCII 标识符小写，中文单字与相邻二元字组；不使用答案标签、题目专用同义词或模型扩写。参数与长度归一化依据 [Stanford IR 教材](https://nlp.stanford.edu/IR-book/html/htmledition/okapi-bm25-a-non-binary-model-1.html)，具体公式保存在协议中。
- 同时报出全库 raw 和系统／精确目标版本／PUBLIC／敏感字面量过滤后的 scoped 排名。过滤发生在排名选择前；Top 3／5 不以无匹配文档填满。冲突语义不在此过滤器的能力内。
- B 的片段只添加到独立 `retrieval_notes`；原上下文每一项、DSL Schema、允许枚举和共同系统 Prompt 均保持一致。实验对各组添加相同的证据隔离说明，生产 Prompt 未修改。
- notes 上限 12,000 UTF-8 字节，包含引用元数据；只加入完整片段，超预算时记录省略，不偷偷截断或改变引用。
- **C**：与 B 使用同一冻结语料、任务、过滤、Top K、上下文预算和隔离构造。项目环境和 Codex 工具环境未发现 sentence-transformers／transformers／torch，标准 Hugging Face 模型缓存不存在。未安装或下载模型；C 状态为 `NOT_RUN_NO_LOCAL_MODEL`，所有成绩为空。

Recall 是每题「召回的标注关联文档数／该题关联文档总数」后做宏平均。共 26 个关联标签。
“无关”在此指不在该题标注集合中，可能包含背景资料；不是独立审查的相关性判定。
负例文本刻意密集使用业务词汇，污染计数是压力测试结果，不能推算真实环境发生率。

## 实测结果

完整逐题数据见 [results.json](../experiments/rag_necessity/results.json)，版本探针与复现核查见 [diagnostics.json](../experiments/rag_necessity/diagnostics.json)。

| BM25 条件 | Recall@3 | Recall@5 | Top 5 返回数 | Top 5 未标注关联 | 跨系统 | 旧版本 | PRIVATE | 冲突样本 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| raw 全库 | 33.33% | 33.33% | 60 | 51 | 24 | 0 | 2 | 11 |
| scoped | 37.50% | 63.89% | 58 | 41 | 0 | 0 | 0 | 7 |

污染分类可重叠，例如 PRIVATE 样本也是故意冲突样本。A 无检索，Recall 不适用；C 未运行，不填零分。

| 问题 | 系统与内容 | 信息类别 | scoped @3 | scoped @5 | 实际送入上下文 @5 |
| --- | --- | --- | ---: | ---: | ---: |
| R01 | Java CSV/BOM/评分/条数 | 额外业务规则 | 0% | 100% | 50% |
| R02 | Java 完成率与成功率 | 额外业务规则 | 50% | 50% | 50% |
| R03 | Java 未完成结果与 409 | 额外业务规则 | 0% | 0% | 0% |
| R04 | Java 状态／完整分析 API 选择 | 额外业务规则 | 50% | 100% | 100% |
| R05 | Java 死信恢复／零重排 | 额外业务规则 | 33.33% | 66.67% | 66.67% |
| R06 | Java batchId/courseId 绑定 | 缺少可信适配 | 0% | 0% | 0% |
| R07 | mini 创建响应依赖 | A 已有 | 50% | 100% | 100% |
| R08 | mini 固定 CSV／两条结果 | A 已有 | 50% | 100% | 100% |
| R09 | mini FAILED 后停止 | A 已有 | 50% | 50% | 50% |
| R10 | mini 数量故障历史 | 可选历史诊断 | 50% | 50% | 0% |
| R11 | Java 重投失败的中文改写 | 额外业务规则 | 50% | 50% | 50% |
| R12 | Java 归属／冲突授权负例 | 额外业务规则 | 66.67% | 100% | 66.67% |

加入上下文字节预算后，实际送达 Recall@3 为 **33.33%**、@5 为 **52.78%**。
21.8 KB 历史案例无法整体放入 12 KB notes；固定安全扫描还排除了含合成 api_key 的测试片段。
R03/R06 没召回关键英文源码，而较短中文文档及故意冲突文档排名靠前。
这支持先改进资料组织、分块和语言表达的判断，尚不能区分具体是哪项改进有效，更不能据此证明 Embedding 有效。

主问题中旧版本 Top 5 命中为 0，**不代表没有过期风险**。独立控制探针使用
`AnalysisBatchStatus resolveStatus total success failed COMPLETED PARTIAL_FAILED`，raw 第 2 位返回真实旧 DTO；scoped 排除它。
旧规则本身未过期，只是来源版本不在当前许可集合中。版本过滤不能识别所有同版本错误信息。

27/27 引用通过原文、行号、来源快照摘要、版本和可见性一致性检查；27/27 来源又与磁盘文件或旧版本 Git blob 的实际字节摘要核对。
两次独立新目录运行的排名、召回、成本、上下文／请求摘要完全一致，实测延迟除外。

## 上下文与成本

以下为 12 题请求编码的合计，含相同 Prompt、DSL Schema 和封装；模型没有收到请求。

| 方案 | 请求字节 | UTF-8 字节/4 的粗估 Token | 相对 A 字节 |
| --- | ---: | ---: | ---: |
| A | 72,270 | 18,073 | 1.00 |
| B Top 3 | 134,102 | 33,531 | 1.86 |
| B Top 5 | 182,819 | 45,708 | 2.53 |
| C | 未生成 | 未测 | 未测 |

字节/4 只是混合中英文资料的粗估，不是目标模型 tokenizer，也不用于宣称实际省钱。
每份请求另记录 `字节数+512` 的保守输入 Token 上界，沿用现有适配器的预检规则。
首轮 12 题 raw/scoped 排名的合计墙钟约 39.3 ms，限本机小语料，不是模型／网络／E2E 延迟，也不是容量或 P95 指标。

## 规划与执行对照准备

[planning-comparison.json](../experiments/rag_necessity/planning-comparison.json) 固定三组的共享输入、评分维度、后续预算和阻塞项。
本轮为同一 12 题准备了 A/B3/B5 共 36 份无网络请求包，位于 `runs/rag-necessity-20261013/`。
C 不生成假检索上下文。

这些是信息问题，不冒充可执行测试需求。八条 Java 问题的业务 API 不在批准 profile 中；
四条 mini 诊断问题也还需要独立的执行需求映射。当前专用 Prompt 和 Intent 有固定三／五步范围，
即使检索到答案也不能突破它们。正式计划能力对照前，必须独立审阅任务映射、预期及新适配，
不能为了比较 RAG 让 A 使用删减资料或让 B/C 获得额外权限。

本轮只验证输入构造和现有可信链的安全行为：Fake 的管理员角色、未批准 Operation、自写断言、任意资源 ID、变更 CSV 均须在执行前被拒绝；
MockTransport 产生固定批准草稿，在有冲突 notes 的进程内 target 上，normal 应 PASS、terminal/result_count 应保持 ASSERTION_FAILED。
这些夹具只证明隔离和回归行为，不证明模型能规划，也不能作为本轮 12 题的模型成功率。

严格区分以下结果：

| 维度 | 本轮状态 |
| --- | --- |
| Retrieval 命中与引用一致性 | 已实测 |
| 请求包和安全门禁 | 离线 Fake／Mock 回归验证 |
| 模型规划正确率 | 未测，null |
| 12 题意图满足程度 | 未测，null |
| 12 题真实端到端成功率 | 未测，null |
| 模型 Token／模型延迟／货币成本 | 未测；本轮收费为 0 |

后续若资料与任务已就绪，可另行授权一次 **12题×A/B×1轮=24次** 的纯规划开发试验，
每次输入／输出上限 20,000／2,048 Token，总预留 **529,152 Token**，不执行 Java。
C 必须先有冻结的本地模型版本、权重摘要、运行依赖、归一化／池化／距离设置，再用同语料／任务比较；
三组一次共 36 次、793,728 Token。只有首轮值得继续，才另批三轮 108 次、2,381,184 Token。
未选择真实模型与价格，不虚构人民币预算；费用公式与停止条件已写入协议。**当前建议不支出。**
本轮无网络编码使用各组相同的 100,000 输入上限，以记录完整请求成本；它不是收费运行预算。
未来建议的 20,000 上限需要逐包预检，超限即停止或在新协议中统一调整片段预算，不能临时只给某组加预算。

## Go / No-Go

1. **有信息缺口，但不是现有成功链必须靠 RAG 才能运行。** 七题涉及额外 Java 业务语义，一题缺可信 batch 适配；三题已有信息，一题历史诊断可选。有限流程可以通过操作员提供的小型版本化资料包和可信结构化输入补齐。
2. **本次 BM25 不够可靠。** scoped Top 5 召回 63.89%，预算内 52.78%，两题完全漏掉关键证据；只能作为离线资料发现工具。这个结论针对当前分块、词法和未审开发集，不代表 BM25 算法对所有系统都不够。
3. **Embedding 的额外收益未知。** 无可用本地模型，没有 C 成绩。中英文语义差异是合理的待检假设，不能作为投入理由或宣称收益。
4. **RAG 确实增加污染入口。** 本实验观察到跨系统、PRIVATE 和同版本冲突命中；精确元数据过滤消除了前两类及控制探针中的旧版本，不能消除内容冲突。执行权仍由原可信链控制。
5. **当前 No-Go：不投入生产 RAG 开发。** 优先补最新契约／业务资料包与可信适配，明确可执行需求和审阅标签。当前实验没有证明模型规划或 E2E 质量提升，而上下文成本已经明显增加。
6. **保留 No-RAG 默认。** 若后续独立多系统样本证明资料量和变化频率确实使静态资料包不足，最小方案才是 Planner 外的只读、按系统和精确版本过滤、带来源的限字节证据提供器；先 BM25，与同语料本地 Embedding 对照后再选择。不得把 CourseInsight 规则硬编码进公共 Planner，也不需要先部署向量数据库。

多系统方面，现有通用 Contract／Policy／DSL／Executor 机制可复用，检索器没有业务系统专用分支；系统归属来自操作员元数据。
但 mini 是独立的一次性 FastAPI 测试目标，与 CourseInsight 共享课程领域，不能证明订单、库存等独立真实业务系统的泛化。
下一实验应先获得第二个真实业务域的代码／契约／规则包与版本；当前不新增虚构业务服务来美化多系统可行性。

## 复现与验证

在仓库根目录使用现有 `.venv`，输出目录必须不存在；不会读取 `.env` 或发起模型／目标连接：

```powershell
.\.venv\Scripts\python.exe experiments\rag_necessity\evaluate.py run --output runs\rag-reproduce-new
.\.venv\Scripts\python.exe -m pytest -q tests\test_rag_necessity.py --basetemp=runs\rag-tests-new
.\.venv\Scripts\python.exe -m ruff check .
git diff --check
```

协议冻结内容发生变化即拒绝复跑，不自动刷新摘要。重新采集必须建立新的协议和结果，不能覆盖本轮分数。
实验目录的 Git 属性保留原始字节，避免 Windows 检出时改变冻结 JSON 的换行。外部源码若因检出设置改变原始字节，运行器仍会拒绝；应使用保持原始字节的检出，不能自动更新旧协议。
`capture.py` 只在显式提供 CourseInsight 根路径时读取来源，不执行 Java；默认复现直接使用包含的原文快照。

实际完成两次冻结检索运行、来源核对和 36 份请求包逐字节复核；Ruff 全项目与 diff 检查通过。
检索、Planner 输入、可信边界、batch plan/workflow、CourseInsight NL/env 相关回归为 **199 passed / 49.26s**。
首个安全回归因测试夹具访问不存在的 assertions 字段而有 1 项失败，修正为主动添加禁止字段后纳入上述最终通过结果；没有修改 DSL 或门禁。
Windows 沙箱中的 asyncio 内部 socketpair 曾阻塞，停止并取得栈后，在沙箱外运行同一断网保护下的回归；没有放行真实目标／模型网络。
后续仅增加历史冻结漂移的独立测试，最终本实验测试集 **22 passed / 7.76s**；历史协议不会迫使未来生产修改重写旧成绩。
没有执行全量 Python／Web／Java 测试或新的 CI；本轮没有新提交 SHA。
