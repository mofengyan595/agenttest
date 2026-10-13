# 自然语言异步批量测试最小闭环

开发起始基线 `d34e153`，最终实现提交 `0e684b2`。场景、CSV、草稿和记录均为 **DEVELOPMENT_ONLY / UNREVIEWED**；已有一次真实模型 mini Smoke 证据，没有独立留出成绩或本场景真实 Java/数据库执行证据。[完整 Demo 与脱敏结构](batch-demo.md)。下文离线验收和预检段落保留其阶段范围。

## 已复用与新增

复用 mini target 的 `createBatch`、`getBatch`、`getBatchResults`，以及既有 DSL 的 `CSVUpload`、`PollPolicy`、`Step`，Executor 的 multipart、成功响应资源绑定、有界轮询、超时、失败即停和脱敏报告。Workflow 仍是一次规划、一次确定性执行；没有新图、新 Executor、自动重试写入或额外模型调用。

新增独立作用域 `mini_target_batch_nl_v1`、batch 专用草稿和意图校验、5 项策略、固定两行 fixture，以及离线诊断入口。原有 `mini_target_nl_v1` consistency / ownership 和 CourseInsight 路径保留。

批准路径为：

`alice login → createCourse → createBatch → getBatch → getBatchResults`

CSV 沿用既有离线 demo 的两条合成文本。`examples/batch-csv-fixture.json` 是可信数据，`examples/batch-policies.json` 对上传字段、文件名和内容做完整相等校验。策略中的 `reviewed` 表示本次操作员授权的执行依据，不能证明评测标签经独立人工审阅。

## 可接受需求与模型表达

当前只接受有顺序、完整匹配的受控中文语法。例如：

> alice 登录后创建课程“批量分析验收”，使用批准的两行 CSV fixture mini_two_rows_v1 创建批处理任务，使用创建响应中的 courseId 和 batchId，有界轮询直到 SUCCEEDED，读取结果并验证条数为 2。

课程名须明确引用，长度 1–80，只接受文字、数字、下划线、空格和连字符；可更换名称和步骤 ID。支持语法中规定的少量同义表达，如“新建”“检查”“等待”。完整目标包括登录、创建课程、批准 fixture 上传、创建任务、有界等待成功、读取结果和条数 2。不是关键词集合匹配；顺序改变、否定、缺少目标、额外指令或未识别表达均要求审阅。当前不宣称通用自然语言语义理解。

另外明确支持本次 Smoke Test 原文：“使用 alice 创建课程‘批量分析验收’，使用已批准的两行 CSV 测试数据创建批量分析任务，等待任务成功后读取结果，并验证返回两条记录。”实际输入的课程名使用中文双引号 `“”`。该语法把已批准两行数据映射到唯一 fixture，把“任务成功”映射到 SUCCEEDED；登录和资源来源仍必须满足完整五步校验。原文直接进入模型上下文，不先改写成带答案的计划。

batch 专用 `BatchPlannerDraft` 在原有参数绑定表示上增加两个必填且可空的类型字段：

| 字段 | 唯一批准值 | 使用步骤 |
| --- | --- | --- |
| `csv_fixture_id` | `mini_two_rows_v1` | `createBatch`；其他步骤为 null |
| `poll_policy_id` | `batch_poll_v1` | `getBatch`；其他步骤为 null |

`examples/batch-draft.json` 给出了完整开发样本。模型只生成引用；可信转换组件把 CSV 数据注入 DSL。模型不能设置文件路径、CSV 内容、PollPolicy 参数、超时、URL、凭据、断言或任意请求体；唯一业务字面值是与需求相同的课程名。角色只允许 alice。即使绕过草稿直接提交 DSL，Policy 和 Intent 仍在任何 HTTP 请求前重新校验。

创建 batch 的 `courseId` 必须引用唯一创建课程步骤的 `$.courseId`；轮询和结果的 `batchId` 必须引用唯一创建 batch 步骤的 `$.batchId`。绑定来源不能是登录、轮询、另一个读取、硬编码 ID 或猜测 Selector。Executor 只发布前序成功步骤的绑定数据。

## 可信执行限制与失败分类

所有策略关闭额外 body/query/path 参数，要求 `read_retries=0`。仅状态步骤必须开启轮询，其他步骤必须关闭。批准参数为：

- 最多 4 次；间隔 0.01 秒；进度状态 QUEUED/RUNNING；终态 SUCCEEDED/FAILED。
- 允许 FAILED 结束等待，但最终断言必须是 SUCCEEDED，并检查 `$.count == 2`。
- 结果 HTTP 200，`$.items` 的 count 必须为 2。
- 目标配置最多 5 步、8 次请求；连接超时 1 秒、读超时 2 秒、执行总超时 15 秒。

`terminal` 在最终状态断言失败，并停止读取结果；`result_count` 在结果数量断言失败。RUNNING 耗尽或传输超时属于 `TEST_INFRA_ERROR`；未知状态、错误 HTTP 状态和结果条数属于 `ASSERTION_FAILED`。资源来源、上传内容、角色或额外操作在规划/校验阶段拒绝，没有执行副作用。

mini target 的未就绪结果接口返回 409。独立人工计划可以用明确批准的 409 策略测试这个业务分支，但这种接口 PASS 不等于本轮自然语言任务成功；本轮批准路径必须完成成功轮询并获取 200 结果，不接受该替代策略。

离线 `reject_role` / `reject_extra` 的 TOOL_ERROR 是输入拒绝，不是内部工具异常：前者在 DSL 层得到 `DSL_UNKNOWN_ROLE`，后者因第 6 步超过批准上限得到 `DSL_STEP_LIMIT`，均为 `PLAN_REJECTED`、0 工具执行、0 HTTP 请求。输入 JSON/类型错误由 Model/DSL Schema 诊断区分；可信策略和意图拒绝保留 NEEDS_REVIEW；传输超时等环境问题保留 TEST_INFRA_ERROR。不把这些分类改成统一 PASS 或 NEEDS_REVIEW。

报告沿用既有脱敏规则，状态字符串不作为原文记录。可核查失败断言类型和 Selector、请求状态、响应摘要与策略哈希；批准公开的数字证据仅包含 `$.count`、`$.items`。

## 离线入口与验收

已有项目 `.venv` 安装 agent extra 时，在仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe scripts/check_batch_workflow.py --output runs/batch-development-001
.\.venv\Scripts\python.exe scripts/check_batch_workflow.py --verify runs/batch-development-001
.\.venv\Scripts\python.exe -m pytest -q tests/test_batch_plan.py tests/test_batch_workflow.py tests/test_batch_diagnostics.py
.\.venv\Scripts\python.exe -m ruff check .
```

输出目录必须是新目录，拒绝覆盖历史证据。脚本只用进程内 ASGI target 与 MockTransport，不能通过参数切换真实模型、外部目标或 Java。手工、FakeLLM、MockTransport 各验证正常、terminal 和 result_count，再验证 11 项规划/意图拒绝。`checks_verified` 仅表示离线检查观察到预期行为；每条记录仍保留实际 `runtime_outcome`，故障和拒绝不会被改成 PASS。

归档包含源码/任务冻结哈希、Selector 和绑定证据、fixture 上传摘要、完整批准 PollPolicy 和断言、逐条 RunReport、结果及哈希清单。只读复核不重放请求；成员增加、证据修改和报告替换都会导致完整性失败。哈希用于发现归档变化，不能防范拥有目录控制权的人同时重写证据和清单。

## Eval v2 评估与后续边界

当前 `ExpectedStep` 无法标注上传/轮询，`StepEvidence` 只保存 upload/poll 布尔值；PollPolicy 和结果断言来自独立 Registry，也没有进入完整质量评分证据链。若只给 Rubric 添加布尔值，仍无法识别错误 fixture、轮询参数或策略断言。安全扩展需要同步设计类型标签、冻结审批配置、证据捕获、归档重评分和错误机制分类，并保持旧版成绩语义。

因此本轮采用独立开发诊断，**保留 `UNLABELED_EXECUTION_OPTION`，不修改 Eval v2 评分器或历史标签**。`eval_v2_intent_score=null`；诊断中的可信策略核查与运行意图校验不能冒充独立评分。现有 Eval v2 PublicTask 配置及 fault 标签也尚未接入 batch；归档使用单独 schema，不混入 C 数据集和历史 A/B。

下一轮建议先人工审阅 1 条正常需求和冻结的 fixture/策略，再明确授权最多 1 次真实模型 Smoke Test：仅 mini target、alice、五步路径、无重规划和重试，固定输入/输出 Token 预算。取得一次草稿后，可在重新创建的离线 target 上复用它运行 normal/terminal/result_count，观察模型能否表达 fixture、poll 引用和真实资源依赖。收费调用前另行授权；它仍是开发 smoke，不能称为独立留出或统计成绩。

## 单次真实模型预检（尚未授权运行）

提交后的同一 SHA 必须先完成 Python Windows/Linux × Base/Agent 及 Web 双平台 CI。随后只运行无网络预检：

```powershell
.\.venv\Scripts\python.exe scripts/batch_smoke.py prepare --model-env .env --output runs/batch-smoke-preflight
```

预检核对干净工作区、完整 Git 内容摘要、任务原文、可信配置、Schema 和实际编码请求；读取 `.env` 但不输出 Key，不调用模型或探针。使用既有 chatecnu 参数：`ecnu-plus`，`https://chat.ecnu.edu.cn/open/api/v1/chat/completions`。输入上限 20,000、输出 2,048、总预留 22,048 Token；模型/Workflow 超时 30/120 秒；最多 1 次调用，无重试、重规划或探针。输入上界沿用既有 UTF-8 字节加余量估计，不冒充实际 usage。

收到操作员对收费调用的另行批准后才可执行：

```powershell
.\.venv\Scripts\python.exe scripts/batch_smoke.py run --real --model-env .env --prepared runs/batch-smoke-preflight/preflight.json --output runs/batch-smoke-once
```

一个预检记录只可消费一次；失败也不退还调用槽位，拒绝覆盖输出。调用前和业务执行前检查冻结状态。真实计划由本次模型响应生成；不读取开发标准计划构造答案。保存原文、脱敏结构和 Selector、fixture/hash、upload/poll、逐步骤报告、真实 usage、耗时与诊断，不保存原始模型正文或凭据。仅原始流程成功后复用内存中同一计划在两个新的进程内 target 上重放 terminal/result_count，不追加模型调用；单独保存回放报告，手工回放不声称自然语言任务成功。所有记录保持 DEVELOPMENT_ONLY / UNREVIEWED、Eval v2 意图分数为空。

兼容 Schema 的字段必填与禁止额外字段规则已按 [OpenAI Structured Outputs 文档](https://developers.openai.com/api/docs/guides/structured-outputs) 核对；这不能替代学校服务兼容性的真实 Smoke Test。入口开发阶段仅做预检与 Mock 验证，之后在 CI 全绿及操作员另行批准后完成一次真实调用。

## 真实 Smoke 验收补记（2026-10-13）

`0e684b2` 上的唯一真实 ecnu-plus 调用生成合法五步计划，正常流程 PASS / task_satisfied=true；实际输入 2,155、输出 610、合计 2,765 Token，模型/正常 Workflow 耗时 13.375/15.657 秒。可信 fixture、`$.courseId`/`$.batchId` 来源、upload/poll 和全部断言通过；轮询 2 次，业务请求 6 次，全部进程内 ASGI。

同一计划 terminal 重放在状态断言失败后停止读取结果；result_count 重放在 count 断言得到实际 3、预期 2；两项原业务结果均为 ASSERTION_FAILED，重放没有模型调用。原始归档完整性已核对，展示用[静态副本](demo-evidence/batch-smoke.json)不包含原始模型响应或秘密。累计本次 Smoke 模型调用 1、真实 Java/数据库访问 0，不产生完整 Eval v2 意图分数，不授权再次调用。
