# AgentTest 收尾与 CourseInsight 接入审查

审查日期：2026-10-13。结论：**AgentTest 已足以作为可展示的实习项目；Java 批处理链在接口层具备接入条件，但当前 AgentTest 适配不能直接执行它。优先完成证据展示与离线契约适配，暂缓新的模型实验和架构扩张。**

归档说明：下文保留 `0e684b2` 阶段的源码审查与待做方案。当前收尾基线为 `e394dfb`，RAG 已作出 [No-Go 决策](rag-necessity-20261013.md)；[现有功能冻结](project-technical-summary.md#功能冻结判断)后不自动启动下文 Java 适配或模型实验。方案与预算建议均不构成执行授权。

架构、模块和模型权限见[项目说明](project-technical-summary.md)；完整历史演示见[批处理 Demo](batch-demo.md)。本报告给出源码依据、实际缺口与下一步取舍，不将接入方案表述为已实现功能。

## 审查基线与证据范围

| 对象 | 本轮核对状态 |
| --- | --- |
| AgentTest | 本地 HEAD 与远端 master 均为 [0e684b2a5522f33ed45bc13f79b6feccc49af7f4](https://github.com/mofengyan595/agenttest/commit/0e684b2a5522f33ed45bc13f79b6feccc49af7f4)；开始时工作区干净 |
| CourseInsight | 本地 HEAD 与远端 main 均为 [fbaf08f585de7839050899065ada3c36de5f3fd1](https://github.com/mofengyan595/courseinsight-cloud/commit/fbaf08f585de7839050899065ada3c36de5f3fd1)；已有 `.gitignore` 修改保留，业务源码未改 |
| mini batch 真实 smoke | 原归档 6 个 JSON 的清单摘要、3 份报告摘要核对通过；1 次调用、2,765 Token、正常 PASS，两项故障均 ASSERTION_FAILED |
| Candidate A/B | 原 A/B 两份归档完整性通过，读取既有 comparison，不重新评分；公开选取[统计副本](demo-evidence/candidate-ab-summary.json)，无私有任务正文 |
| 基线质量记录 | 既有本地最终记录 984 passed / 13 skipped；同 SHA [Python 四组 CI](https://github.com/mofengyan595/agenttest/actions/runs/38354721621)和 [Web 双平台 CI](https://github.com/mofengyan595/agenttest/actions/runs/38354721626)全绿；这些是上一阶段证据 |

本轮仅阅读代码、已有报告和 GitHub 分支元数据，整理 AgentTest 文档与脱敏静态副本。没有启动 Java/Docker，没有探测业务 URL，没有读取 CourseInsight 凭据，没有模型调用或数据库操作。源码支持不等于当前运行环境已就绪；Java/MQ/MySQL/Redis 的实时健康与实际异步耗时本轮未验证。

## CourseInsight 真实接口与权限

以下链接固定在本轮 CourseInsight 提交，避免 main 后续变化。接口来自 Controller、DTO、Service、Mapper，而非根据 README 推断。

| 能力 | 当前源码定义 | 关键语义与依据 |
| --- | --- | --- |
| OpenAPI | 未在 pom/main 配置中发现 springdoc/swagger；现有 AgentTest 契约是手工审核子集 | 不能假定 `/v3/api-docs` 可用。本轮不请求该地址；[旧契约](../examples/courseinsight/openapi.json)只有登录/创建课程/读课程三项，版本为 3a.1 |
| JWT 登录 | POST `/api/auth/login`，200；`ApiResponse<UserLoginResponse>` | Token 在 `$.data.accessToken`；[AuthController](https://github.com/mofengyan595/courseinsight-cloud/blob/fbaf08f585de7839050899065ada3c36de5f3fd1/courseinsight-server/src/main/java/com/courseinsight/server/controller/AuthController.java)、[响应 DTO](https://github.com/mofengyan595/courseinsight-cloud/blob/fbaf08f585de7839050899065ada3c36de5f3fd1/courseinsight-server/src/main/java/com/courseinsight/server/dto/UserLoginResponse.java) |
| 角色 | STUDENT、TEACHER、ADMIN；创建课程与 batch 只准 TEACHER/ADMIN | [SecurityConfig](https://github.com/mofengyan595/courseinsight-cloud/blob/fbaf08f585de7839050899065ada3c36de5f3fd1/courseinsight-server/src/main/java/com/courseinsight/server/config/SecurityConfig.java)的 HTTP 角色检查之后仍有对象归属检查 |
| 课程 | POST `/api/courses` 201，响应 `data` 为 Long；GET `/api/courses/{id}` 200；PUT 与分页 GET 也存在 | 创建需 code/name/teacherName，description 可选；GET 详情不检查创建者归属，不能照搬 mini 的跨用户 403。[CourseController](https://github.com/mofengyan595/courseinsight-cloud/blob/fbaf08f585de7839050899065ada3c36de5f3fd1/courseinsight-server/src/main/java/com/courseinsight/server/controller/CourseController.java)、[CourseService](https://github.com/mofengyan595/courseinsight-cloud/blob/fbaf08f585de7839050899065ada3c36de5f3fd1/courseinsight-server/src/main/java/com/courseinsight/server/service/CourseService.java) |
| 课程评价 | POST/GET `/api/courses/{courseId}/comments`；GET `/api/comments/me`；DELETE `/api/comments/{commentId}` | 普通创建受认证/业务校验约束；删除是评价业务操作，不是 batch 清理 API。[CommentController](https://github.com/mofengyan595/courseinsight-cloud/blob/fbaf08f585de7839050899065ada3c36de5f3fd1/courseinsight-server/src/main/java/com/courseinsight/server/controller/CommentController.java) |
| 单条任务/分析 | GET `/api/comments/{commentId}/analysis-task`；GET `/api/comments/{commentId}/analysis`；POST `/api/analysis-tasks/{taskId}/execute`、`/enqueue` | task 查询学生限本人，教师/管理员检查可管理课程；analysis 仅教师/管理员。手动 execute/enqueue 是额外写操作，最小 batch 计划不加入。[AnalysisTaskService](https://github.com/mofengyan595/courseinsight-cloud/blob/fbaf08f585de7839050899065ada3c36de5f3fd1/courseinsight-server/src/main/java/com/courseinsight/server/service/AnalysisTaskService.java)、[CommentAnalysisController](https://github.com/mofengyan595/courseinsight-cloud/blob/fbaf08f585de7839050899065ada3c36de5f3fd1/courseinsight-server/src/main/java/com/courseinsight/server/controller/CommentAnalysisController.java) |
| CSV 创建 batch | POST `/api/courses/{courseId}/analysis-batches`，multipart `file`，**201** | 响应含 `data.batchId/batchNo/courseId/totalCount`，没有 mini 的 QUEUED 字段；[AnalysisBatchController](https://github.com/mofengyan595/courseinsight-cloud/blob/fbaf08f585de7839050899065ada3c36de5f3fd1/courseinsight-server/src/main/java/com/courseinsight/server/controller/AnalysisBatchController.java) |
| batch 状态 | GET `/api/analysis-batches/{batchId}`，200 | `data.status` 为 PROCESSING/COMPLETED/PARTIAL_FAILED/FAILED；还有 totalCount、waitingCount、processingCount、retryingCount、successCount、failedCount；[进度 DTO](https://github.com/mofengyan595/courseinsight-cloud/blob/fbaf08f585de7839050899065ada3c36de5f3fd1/courseinsight-server/src/main/java/com/courseinsight/server/dto/AnalysisBatchProgressResponse.java) |
| batch 结果 | GET `/api/analysis-batches/{batchId}/results`，200 | `data` 为 page/size/total/totalPages/items；默认 page=1、size=20，size≤100；[ResultService](https://github.com/mofengyan595/courseinsight-cloud/blob/fbaf08f585de7839050899065ada3c36de5f3fd1/courseinsight-server/src/main/java/com/courseinsight/server/service/AnalysisBatchResultService.java) |
| 导出/重试 | GET `/api/analysis-batches/{batchId}/export`，text/csv；POST `/api/analysis-batches/{batchId}/retry-failed`，202 | 导出 UTF-8 BOM、100 条分块查询；重试另有写入语义。两者真实存在，但不纳入最小接入授权。[Controller](https://github.com/mofengyan595/courseinsight-cloud/blob/fbaf08f585de7839050899065ada3c36de5f3fd1/courseinsight-server/src/main/java/com/courseinsight/server/controller/AnalysisBatchController.java) |

JWT 使用 HS256 并验证 issuer/有效期；认证转换器根据 subject 查当前数据库用户的状态与角色，不只信任 Token 中的角色声明。[JwtConfig](https://github.com/mofengyan595/courseinsight-cloud/blob/fbaf08f585de7839050899065ada3c36de5f3fd1/courseinsight-server/src/main/java/com/courseinsight/server/config/JwtConfig.java)、[CurrentUserJwtAuthenticationConverter](https://github.com/mofengyan595/courseinsight-cloud/blob/fbaf08f585de7839050899065ada3c36de5f3fd1/courseinsight-server/src/main/java/com/courseinsight/server/security/CurrentUserJwtAuthenticationConverter.java)。AgentTest 的 role 是操作员账号别名，不能用 mini 的 alice 直接推断 Java 的角色。

batch 上传、进度、结果、导出均通过课程管理权限校验：ADMIN 可管理任意课程，TEACHER 只能管理自己课程，其他角色拒绝。[CourseManagementAccessService](https://github.com/mofengyan595/courseinsight-cloud/blob/fbaf08f585de7839050899065ada3c36de5f3fd1/courseinsight-server/src/main/java/com/courseinsight/server/service/CourseManagementAccessService.java)。首个接入可沿用批准 admin；若以后验证跨教师 403，需两个独立教师 fixture，不能用 admin 充当应拒绝角色。

## mini 与 Java 的实质差异

| 项目 | 当前 mini batch | CourseInsight | 最小适配 |
| --- | --- | --- | --- |
| 操作标识 | login/createCourse/createBatch/getBatch/getBatchResults | Java 无现成 OpenAPI operationId；旧适配仅 ciLogin/ciCreateCourse/ciGetCourse | 在 AgentTest 新的源码审核契约中指定明确 ci batch ID |
| 认证 | alice；`$.access_token` | 批准 admin 或教师；`$.data.accessToken` | 复用现有 Java Vault 配置，凭据不进模型 |
| 创建课程 | body.name；`$.courseId` | code/name/teacherName 必填；新 ID 位于 `$.data` | 锁定操作员生成的唯一 code 与教师名，保留名称要求 |
| CSV | `text` 表头，两条合成文本 | `content,rating`；content 非空≤2000 Java 字符，rating 整数 1–5 | 新建独立批准 fixture，不能只重命名现有 mini fixture |
| 上传响应 | 202；`$.batchId`、state=QUEUED | 201；`$.data.batchId`、totalCount=2 | 新策略检查 code=0、条数及课程来源关系 |
| 状态 | `$.state`；QUEUED/RUNNING → SUCCEEDED/FAILED | `$.data.status`；PROCESSING → COMPLETED/PARTIAL_FAILED/FAILED | 新 PollPolicy；所有失败终态结束等待，再由最终断言拒绝 |
| 结果 | `$.items` count=2；未就绪 409 | `$.data.items` + total；不要求终态才能查询 | 先验证 COMPLETED、successCount=2、failedCount=0，再验证分页条数和项状态 |
| 执行时间 | 确定性两次 GET 推进状态，0.01 秒间隔 | Outbox/MQ/消费者/AI HTTP 链异步推进，GET 只读聚合 | 重新批准有限预算，不能原样用 mini 的 4 次/0.01 秒 |
| 隔离/清理 | 每个 target 内存丢弃 | MySQL/Redis/MQ 持久化，多个关联表及异步消息 | 独立环境 + 操作员清理方案；HTTP 结束不等于数据已清除 |

CSV 校验还限制 `.csv` 文件名、2 MB、1–200 行，支持 UTF-8 BOM。[AnalysisBatchCsvParser](https://github.com/mofengyan595/courseinsight-cloud/blob/fbaf08f585de7839050899065ada3c36de5f3fd1/courseinsight-server/src/main/java/com/courseinsight/server/service/AnalysisBatchCsvParser.java)。AgentTest 的 CSVUpload 最多 100,000 字符且请求字节另受 TargetConfig 限制；两行 fixture 可适配，不意味着已经覆盖 Java 2 MB 边界。上传限流为每用户 60 秒 2 次；不能为了复跑修改服务限流或自动重试。

尤其要避免“两条结果 = 分析成功”的误判。Java 结果 Mapper 从 analysis_task 出发 LEFT JOIN analysis_result，WAITING/FAILED 任务也能出现在 items 中，resultId 可为空；结果查询/导出没有等待 COMPLETED 的前置条件。[AnalysisBatchResultMapper](https://github.com/mofengyan595/courseinsight-cloud/blob/fbaf08f585de7839050899065ada3c36de5f3fd1/courseinsight-server/src/main/java/com/courseinsight/server/mapper/AnalysisBatchResultMapper.java)。这也说明不能把 mini 的未就绪 409 移植为 Java 预期。

现有 Verifier 支持 equals/exists/count/in，Selector 支持字段和固定索引，不支持通配符、过滤与“所有项”量词。两条固定结果可分别断言 `$.data.items[0].taskStatus`、`[1].taskStatus` 为 SUCCESS；通用多页校验要另作能力审查。`exists` 只证明字段存在，不证明非 null 或类型；首个方案不以 resultId 的 exists 代替成功断言。

## 环境、隔离和清理

[compose.demo.yaml](https://github.com/mofengyan595/courseinsight-cloud/blob/fbaf08f585de7839050899065ada3c36de5f3fd1/compose.demo.yaml) 定义独立 `courseinsight-demo` 项目、命名容器和卷，AI 边界替换为确定性 performance stub，MySQL/Redis/RocketMQ 仍是真实组件。[application-demo.yaml](https://github.com/mofengyan595/courseinsight-cloud/blob/fbaf08f585de7839050899065ada3c36de5f3fd1/courseinsight-server/src/main/resources/application-demo.yaml) 指向 demo 数据库；实际隔离仍需核对容器标签、端口、卷、有效配置和源版本，不能仅凭 profile 名称判断。

Java [DemoSmoke.java](https://github.com/mofengyan595/courseinsight-cloud/blob/fbaf08f585de7839050899065ada3c36de5f3fd1/scripts/DemoSmoke.java) 已包含登录、创建/读取课程、CSV、状态等待和分页结果检查，并核对 SUCCESS 与 performance_stub。它提供可参考的真实代码路径；本轮没有执行它，不能宣称本轮跑通。

上传事务会创建 batch、匿名 comment、analysis_task 和 outbox_event；后台还写结果、重试状态并失效缓存。[AnalysisBatchCreationService](https://github.com/mofengyan595/courseinsight-cloud/blob/fbaf08f585de7839050899065ada3c36de5f3fd1/courseinsight-server/src/main/java/com/courseinsight/server/service/AnalysisBatchCreationService.java)。当前 Controller 没有删除课程/batch 的清理接口；评价 DELETE 不能完成该链路的资源回收。外部 HTTP 与异步消费者也不在客户端可回滚事务中。

源码测试使用 MySQL Testcontainers 的 `courseinsight_test`，部分测试有 @Transactional 回滚；其测试配置 Mock 了 MQ、缓存及限流。这些测试有助于确认持久化/授权语义，但不等同 MQ 全链路验收。[测试容器](https://github.com/mofengyan595/courseinsight-cloud/blob/fbaf08f585de7839050899065ada3c36de5f3fd1/courseinsight-server/src/test/java/com/courseinsight/server/testsupport/MySqlTestContainers.java)、[测试配置](https://github.com/mofengyan595/courseinsight-cloud/blob/fbaf08f585de7839050899065ada3c36de5f3fd1/courseinsight-server/src/test/java/com/courseinsight/server/testsupport/DatabaseIntegrationTestConfiguration.java)。本轮未运行任何 Java 测试。

demo stop 保留卷，clean 会删除 demo 卷并要求独立确认。未来优先使用可丢弃的专用实例；共享 demo 至少使用唯一课程 code、运行资源清单、后台任务停止/完成记录及操作员批准的回收过程。不把删除卷、SQL 清理或 retry-failed 加入模型计划，也不自动执行。

## 当前实际阻塞项

1. **来源版本门禁拒绝当前 Java 提交。** [source-review.json](../examples/courseinsight/source-review.json) 固定 `430c23e`，而当前是 `fbaf08f`。[预检](../src/agenttest/courseinsight_env.py)先精确比较 SHA，因此根据代码会返回 SOURCE_VERSION_UNREVIEWED。后续精确核对修正了早期“16 个文件哈希全一致”的表述：原始字节摘要为 14/16 一致，CourseController／CourseCreateRequest 有 CRLF 差异，按 LF 比对后 16/16 一致，见[RAG 来源核查](rag-necessity-20261013.md#来源与边界)。这不批准新 SHA；batch 的 Controller/Service/DTO/Mapper、CSV parser、权限与异步链仍需新审查。没有实际运行预检或访问 Docker。
2. **契约、策略和 NL profile 未覆盖 Java batch。** 旧 Java profile 恰好三步、拒绝 poll；mini batch profile 固定 mini ID/Selector/alice。需要新增独立 Java batch profile，不能修改旧场景使其默许更多操作。
3. **环境和副作用生命周期未验收。** 本轮不知 Docker/消费者实际健康，也没有一次性 batch 写入 claim、资源回收记录或新的批准。创建响应丢失时不能重试上传，仍需操作员判断服务器是否已写入。
4. **CSV 导出内容尚无执行器验证能力。** HTTPX 能读取有界响应，但 Executor 将非 JSON 解析为 None，没有 CSV 解析、表头/内容或响应 Header 断言。只检查 HTTP 200 不能声称导出内容测试成功，最小链明确排除导出。
5. **batch 正式评分缺口仍存在。** Eval v2 的 Rubric/PlanEvidence 缺少完整 fixture、PollPolicy 与执行断言质量标签，`UNLABELED_EXECUTION_OPTION` 保留。Java batch 首次验收也只能 DEVELOPMENT_ONLY，不能靠忽略字段取得意图满分。

这些是“推广到当前 Java 批处理”的阻塞项，不意味着现有 mini 引擎不可展示。本轮没有发现必须为展示而扩大架构的理由；静态审查也不能保证不存在其他缺陷。

## 最小安全接入方案：下一里程碑建议

以下均是待做方案，本轮没有创建契约/策略代码或运行跨项目联调。

**先做离线适配。** 在 AgentTest 内新增独立的源码审核 batch 契约、5 项策略、一个 `content,rating` 两行合成 fixture 和 Java batch Intent Validator。操作范围只允许登录、一次创建课程、一次上传、一次有界轮询、一次结果读取；admin 单角色，不增加 export、retry、execute/enqueue、注册或删除。保留原三步 Java 和 mini profiles。

资源要求为 createCourse 的 `$.data` → upload.courseId，upload 的 `$.data.batchId` → poll/results.batchId，均来自前序成功响应。每次 JSON 响应除 HTTP 状态外还核对 `$.code==0`。状态最终要求 COMPLETED、totalCount=2、successCount=2、failedCount=0；结果 page=1/size=2、total=2、items count=2，两项 taskStatus 均 SUCCESS。此范围验证链路与完成状态，不评分真实 AI 情感/建议质量。

复用当前 Scenario/CSVUpload/PollPolicy、Registry、Executor、Vault、Workflow、报告及 Selector。普通 PlannerDraft 不能表达上传/轮询，现有 BatchPlannerDraft 又硬编码 mini 枚举；未来应新增最小 Java 类型草稿，共用已有转换原语并使用独立批准 ID，不能简单关闭 strict、允许路径或借用 mini fixture。模型阶段可以后置，先用人工 DSL 与 Fake/Mock 验证适配。

可信预算可先提出最多 20 次、间隔 1 秒、最多 24 个业务请求、执行 60 秒、Workflow 120 秒的有限方案（5 步）。这是基于现有类型上限的建议，未测量能否满足 MQ 启动/积压情况，需离线与环境验收后由操作员固定；不得让模型自行增大。PROCESSING 继续等待，三个终态均结束，PARTIAL_FAILED/FAILED 由成功断言发现。耗尽是 TEST_INFRA_ERROR；明确失败终态是 ASSERTION_FAILED。无写入重试。

**之后才考虑一次人工计划真实验收。** 先重新审查版本和运行隔离、固定唯一课程 code/fixture/预算、保存不可重放 claim 和可追踪资源记录，再取得新的 Java 写入授权。使用专用 demo/stub 环境，复用现有 harness 的隔离思路；不要为了启动成功连接 local/共享数据库或放宽版本门禁。先人工 DSL 跑通即可，不需要收费模型来证明 HTTP 适配。

若人工链路和故障/安全回归均通过，再判断是否值得单次真实模型 NL 验收；那需要独立收费授权。本轮不安排或执行该步骤，也不重跑 Candidate A/B。

## 工作量与优先级

以下是工程估算，不是实测工时；假设现有依赖和专用 demo 可用、只做上述五步、不增加账户管理/导出/正式评分。

| 工作 | 估算 | 优先级 |
| --- | --- | --- |
| 阅读并熟练展示现有 Demo，解释边界、失败和真实数字 | 半天左右 | 面试前最高；本轮文档已就位 |
| Java batch 契约/策略/fixture + 人工 DSL/Mock、安全拒绝 | 1–2 个工程日 | 若继续开发，先做这一个里程碑 |
| 专用环境、版本复核、一次性写入保护与人工链路验收 | 约 1 个工程日，环境异常另计 | 有明确实际使用需求且授权后再做 |
| Java batch NL 类型草稿/Intent + Fake/Mock 回归 | 约 1 个工程日 | 人工适配后可选；收费调用另计，非必要面试前置条件 |
| 导出全内容、跨教师、多页、正式 holdout、可恢复执行 | 不包含在以上估算 | 延后，先确定实际使用需求 |

AgentTest 已有明确问题、完整可信引擎、真实小样本、可解释失败和跨平台 CI，足以作为可展示项目。面向 Java 后端实习，优先讲清 CourseInsight 的事务/Outbox/MQ、JWT 与归属检查，以及 AgentTest 如何在不重复写入、不混淆结果的前提下测试它。不要把 Python 项目贡献说成 Java 业务实现。

关键实用限制是目标接入仍需人工契约/策略/意图适配，环境清理不自动化，模型流程高度受 Prompt 约束。它适合受控场景验证，尚不能直接当成通用生产测试平台。

可以停止或推迟：mini 新业务、旧任务调参、Candidate 压缩/A-B、RAG、多 Agent、自动修复、分布式平台和更多 UI。正式 Eval v2 只有在确实要做独立比较时才值得投入人工留出采集；不能为了简历包装先改门禁或凑满分。

## 文档收尾建议与本轮边界

本轮已整理项目说明、静态 batch Demo 和 A/B 摘要，README 连接当前入口并修正“不能规划 CSV/轮询”等过时表述。历史报告、原任务标签、正式评分/审核门禁和私有压缩原型不修改。原进度日志作为历史保留，新的当前状态置顶，避免把当时“尚未调用模型”当成今天的状态。

后续如获接入开发授权，只更新对应新 profile、source-review 与独立适配文档；不要把旧 Java 成绩追认为新版本或新批处理成绩。本页在原审查阶段未提交，现与技术决策、静态证据统一归档；最终发布状态以本次提交和 CI 为准。

## 本轮实际验证

- 已有离线回归 244 passed / 89.62 秒：batch 四模块、课程自然语言/Intent/归属、CourseInsight 人工/NL/环境/Mock harness。使用项目 `.venv`，网络禁用测试夹具保持启用；未安装依赖。
- 已有 `scripts/check_batch_workflow.py --output runs/engineering-review-20261013-batch-demo` 及其 `--verify` 均通过；20 条原分类为 3 PASS、6 ASSERTION_FAILED、9 NEEDS_REVIEW、2 TOOL_ERROR，所有案例符合各自预期，不能将它们统计为 20 个业务 PASS。
- `python -m ruff check .`、`git diff --check` 通过；7 个文档的本地链接/围栏/编码、静态副本与原始字段及摘要核对、敏感 Token/JWT/私钥模式扫描通过。
- 重新读取 GitHub 六项 CI，均为基线 `0e684b2` 成功；本轮没有新提交或 CI。文档改动没有重跑全量 Python/Web 测试，没有运行任何 Java 测试或业务服务。

测试与 Demo 输出保存在忽略目录，正式源码、评分规则和历史运行档案没有改动。本轮交付仅是文档与选取的脱敏展示数据；后续接入工作量与风险仍是基于源码的工程判断，未以真实联调验证。
