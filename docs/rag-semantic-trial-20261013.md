# RAG 本地语义检索补充试验

日期：2026-10-13；业务基线 `37eca48`。用户在原 No-Go 归档后授权“试试看”，本次仅验证独立、默认关闭的规划参考层。**可以接入，但现有证据仍不足以启用生产 RAG。** 没有修改 `src/`、正式 Eval v2、历史 A/B 或原 RAG 实验，未调用收费模型、Java 服务或数据库，也没有提交推送。

原[必要性评估](rag-necessity-20261013.md)的 C = NOT_RUN 是当时事实，原文件与成绩保持原字节。本次另存[协议](../experiments/rag_semantic_trial/protocol-v2.json)、[结果](../experiments/rag_semantic_trial/results.json)、[诊断](../experiments/rag_semantic_trial/diagnostics.json)和[回放摘要](../experiments/rag_semantic_trial/replay-summary.json)，全部为 DEVELOPMENT_ONLY / UNREVIEWED，不是独立留出实验。

## 实际实现及信任边界

`experiments/rag_semantic_trial/` 内的 `ReferencePlanner` 在 Planner 外添加可选 `retrieval_notes`。默认关闭时不调用检索；开启时只接受已有向量的冻结问题，先按操作员指定的系统、精确版本和 PUBLIC 可见性筛选，再添加最多 12,000 字节的来源引用。未知问题、系统、引用来源或向量缓存身份不匹配直接拒绝，不自动联网或改用其他模型。

每条引用记录系统、来源文件、源版本、目标版本、行号及文本摘要，标记为 `UNTRUSTED_PLANNING_REFERENCE_ONLY`。其余 A 组输入原样保留；同系统冲突文档仍可能通过元数据过滤，不能把 PUBLIC 理解为内容正确或获得授权。

执行依据继续来自人工审阅的契约、可信 Operation／Role／Policy、目标配置、批准 fixture、确定性 Intent／DSL 校验及 Executor／Verifier。检索命中、源码片段、历史案例和未审核标签均不能授予执行权限、改变目标地址或断言。没有把 CourseInsight 规则写入公共 Planner，也没有新增向量数据库或默认运行依赖。

## 冻结方法与本地模型

沿用原来的 **12 条问题、27 份语料及 Golden Labels**，B/C 使用完全相同的原始文本，不调问题、标签、分块或检索参数。B 的每条 raw/scoped 指标均与归档逐字段相同。A 保留完整既有 `planning_context`，B/C 请求共用 Prompt、DSL、Policy、模型占位配置与 12 KB 证据预算，只序列化请求包，没有执行 `generate()` 网络调用。

C 使用固定的 [Xenova ONNX 导出](https://huggingface.co/Xenova/bge-small-zh-v1.5/tree/75c43b069aac4d136ba6bc1122f995fedcfd2781)，上游为 [BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5)。按上游说明使用查询前缀、CLS 向量与 L2 归一化；文档不加查询前缀，以余弦相似度排序。模型 revision 为 `75c43b069aac4d136ba6bc1122f995fedcfd2781`，量化权重 SHA-256 为 `15b717c382bcb518ba457b93ea6850ede7f4f1cd8937454aa06972366cd19bcc`。七项资产的完整字节数与摘要见[模型清单](../experiments/rag_semantic_trial/model-manifest.json)。下载阶段只访问公开资产；推理阶段禁用 Python 网络连接并关闭 ORT 遥测，未运行远端 Python 代码。

利用既有独立工具环境的 Python 3.13.12、ONNXRuntime 1.20.1、NumPy 2.4.6，通过 [CPUExecutionProvider](https://onnxruntime.ai/docs/api/python/api_summary.html) 实际执行模型。AgentTest 主程序与回归继续使用项目 `.venv`，没有安装或升级依赖。实现严格限定到下载配置的标准库 WordPiece 子集，**未获得官方原生分词器逐 token 一致性结果**；因此这不是标准 BGE 基准成绩，也不能代表所有 Embedding 方案。

首次预检发现 `splitlines()` 错误地将词表中的 U+2028 当作换行，输入一致性校验在模型加载前阻止运行。改为按字面 LF 分割并加入回归后重新冻结 `protocol-v2.json`，然后才首次产生 C 分数；没有根据 C 分数调参。初次失败的协议摘要保留在诊断中，原协议副本留在私有运行目录。

## 检索结果

Recall 为 12 题逐题 Recall 的宏平均；数量为所有题 Top K 返回的文档位置总计，同一文档在多题命中会重复计数。污染类别可以重叠，“不相关”依据未经独立审核的原标签判定。

| 检索范围 | B Recall@3 | C Recall@3 | B Recall@5 | C Recall@5 |
| --- | ---: | ---: | ---: | ---: |
| 不做元数据过滤 | 33.33% | 34.72% | 33.33% | 41.67% |
| 系统／精确版本／可见性过滤 | 37.50% | 41.67% | 63.89% | 55.56% |
| 实际送入 12 KB 证据预算后 | 33.33% | 37.50% | 52.78% | 51.39% |

| Top 5 总计 | B raw | C raw | B scoped | C scoped |
| --- | ---: | ---: | ---: | ---: |
| 返回位置 | 60 | 60 | 58 | 60 |
| 不相关 | 51 | 49 | 41 | 45 |
| 跨系统 | 24 | 21 | 0 | 0 |
| PRIVATE | 2 | 6 | 0 | 0 |
| 过期版本 | 0 | 0 | 0 | 0 |
| 合成冲突文档 | 11 | 17 | 7 | 7 |

C scoped @3 比 B 高 4.17 个百分点，@5 低 8.33 个百分点；预算后 @5 仍低 1.39 个百分点，未观察到稳定优势。C raw 的 PRIVATE 和冲突命中更多，过滤后冲突并未消失。主问题集均未命中过期文档，**不能据此声称模型能识别过期信息**；C 没有运行额外的旧版本检索探针，版本排除由元数据边界测试验证。原 B 的旧版本控制探针仍保留在原实验中。

## 上下文及本地耗时

| 十二题合计 | A | B3 | C3 | B5 | C5 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 请求 UTF-8 字节 | 72,270 | 134,102 | 135,690 | 182,819 | 186,018 |
| 各请求 bytes/4 向上取整后求和 | 18,073 | 33,531 | 33,926 | 45,708 | 46,509 |

C5 请求为 A 的约 **2.57 倍**，B5 为约 2.53 倍。这是序列化请求与粗略 Token 估计，包含 Schema 等开销，**不是 tokenizer 实测、计费记录或 LLM 延迟**；各请求还保存 `bytes + 512` 的保守上界。A 没有为制造优势而删除资料。

模型资产共 24,561,772 字节，量化权重约 24 MB。首次加载 139.342 ms；27 份文档推理合计 1,062.759 ms，13 条查询合计 82.854 ms；40 条序列的推理合计 1,145.613 ms。额外一条查询仅用于既有 batch Demo 回放，明确记为 DEMO_ONLY，排除于十二题检索指标。以上为单机 CPU 的 ONNX 调用、向量校验及归一化计时，不含下载、分词、文件读取或完整请求延迟，也不是线上吞吐基准。

最大输入 512 token，8/27 份文档被截断，ID 列于诊断。文档编码位置共 8,814，包含 1,150 个 `[UNK]`；13 条查询编码位置共 815，包含 22 个 `[UNK]`。技术源码／中英文混合内容、量化、截断与自实现分词均限制解释范围。B 使用全文词法统计，C 只编码前 512 token；原始文档来源相同不等于实际利用信息量相同。没有为提高 C 结果临时追加分块或更换模型。

同环境重复完整推理一次：向量最大绝对差为 0，排名、逐题指标及成本相同，48 个 B/C 请求包逐字节一致；耗时不相同，未混用两次计时。27 条引用均通过保存的文件快照／版本／行号／摘要校验。这是冻结快照的一致性，不把原 Java 三步契约升级为当前版本授权。

## 规划和执行边界回放

使用真实本地 C 向量、既有 mini batch 需求、**固定 Fake 计划**及进程内 ASGI 目标：A/C 正常均 PASS，terminal 与 result_count 均 ASSERTION_FAILED；角色、Operation、断言、资源绑定、CSV 五类越权输出均在执行前拒绝，工具调用为 0。实际接入共 11 次回放，详细分类见摘要。默认测试另用合成向量验证关闭开关、缓存身份、引用隔离和异常数据，不把它们当作语义检索成绩。

这些回放证明参考层没有绕过现有执行校验，**不测量模型规划正确率、意图改善或真实端到端 RAG 收益**。`planning_accuracy`、`real_end_to_end_success` 继续为 null。没有将 Fake 的固定答案包装为模型能力。

## 复现及公开范围

在本次冻结源码基线上，用[模型清单](../experiments/rag_semantic_trial/model-manifest.json)列出的固定 revision 下载七项文件至 `runs/rag-semantic-trial-20261013/model/`（保持 `onnx/` 子目录），并将清单原字节复制为该运行目录的 `model-manifest.json`。下载 URL 格式为 `https://huggingface.co/Xenova/bge-small-zh-v1.5/resolve/75c43b069aac4d136ba6bc1122f995fedcfd2781/<文件名>`，保持 TLS 验证；小型文本也可使用相同 revision 的 `/raw/` 路径。每个文件须通过字节数和 SHA-256 校验。模型权重、向量和完整请求包留在忽略的 `runs/`，公开目录只保存实验代码、协议、清单及脱敏指标／回放摘要。

在已有 NumPy／ONNXRuntime 的独立工具环境运行编码；没有该环境时明确停止，不自动安装或回退收费 API。以下输出均排他创建；再次复现请使用新输出文件或目录。协议会拒绝与冻结源码不一致的版本，不能为通过检查改写旧协议。

```powershell
$embeddingPython = 'C:\path\to\embedding-env\Scripts\python.exe' # 替换为已有独立环境
& $embeddingPython experiments/rag_semantic_trial/encode.py --assets runs/rag-semantic-trial-20261013 --protocol experiments/rag_semantic_trial/protocol-v2.json --output runs/rag-semantic-trial-20261013/embeddings-reproduce.json
& .\.venv\Scripts\python.exe experiments/rag_semantic_trial/trial.py run --vectors runs/rag-semantic-trial-20261013/embeddings-reproduce.json --protocol experiments/rag_semantic_trial/protocol-v2.json --output runs/rag-semantic-trial-20261013/comparison-reproduce
& .\.venv\Scripts\python.exe experiments/rag_semantic_trial/replay.py --vectors runs/rag-semantic-trial-20261013/embeddings-reproduce.json --protocol experiments/rag_semantic_trial/protocol-v2.json --output runs/rag-semantic-trial-20261013/replay-reproduce
```

## 决策

最终相关离线回归 **122 passed / 49.93s**（新参考层、原 RAG、batch workflow／plan／diagnostics）；全项目 Ruff 通过。5 份文档的 110 处本地链接／锚点、17 份公开文件的 UTF-8／敏感模式、27 条来源引用、7 项模型资产摘要、原／新冻结协议及公开结果与运行输出一致性检查通过，`git diff --check` 通过。权重、向量、完整请求包、回放原始报告和预检失败记录均留在忽略的运行目录。本轮未重跑全量 Python／Web／Java 或 CI，没有新提交。

仍保持 **No-RAG 默认**，独立参考层仅作为可关闭的试验代码。现有受控演示功能的冻结判断不变；没有新增生产接入阻塞，也没有证明需要为它开发向量数据库。BM25 尚不够可靠；本次 C 小幅改善 @3，未改善 @5 或预算内 @5，不足以证明新增复杂度值得。

重新考虑的前提仍是：第二个独立真实业务域、独立审核的新问题和标签、已确认静态资料不足、最新契约／Policy／Intent 的可信适配，以及同输入同预算下真实规划或端到端收益大于成本。届时需新冻结实验，先验证原生 tokenizer 和合理分块，再决定是否继续；不对本次已使用的十二题反复调参。本次没有收费预算消耗，后续预算建议沿用原报告，真实调用仍须另行授权。
