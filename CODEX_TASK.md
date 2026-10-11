# AgentTest — Codex 开发任务书（独立仓库）

## 目标和产品边界

开发基于 Python + FastAPI + LangGraph + MCP 的智能 API 测试平台。用户输入自然语言测试需求，系统读取 OpenAPI 3.x 与**人工确认**的业务测试策略，生成结构化测试计划，执行已授权的跨接口测试、确定性断言、失败分析和可复现的 pytest 回归脚本。

不要做成对接口文档聊天的 RAG 应用，不要做成让 LLM 任意调用 HTTP/Shell 的工具。

**采用“Agent 决策 + 确定性执行”的架构。** LLM 负责规划、测试策略建议、决定下一步调查；Executor/Verifier 负责 HTTP 请求、凭据管理、断言、结果保存。没有人工确认的预期不能被系统自动判定为 Bug。

## 环境要求（首个必须完成的操作）

- Python 3.11（可用 3.12），每个项目独立 `.venv`；项目安装 `pip install -e '.[dev]'`。
- 添加新 Agent 依赖时使用 `pip install -e '.[dev,agent]'`，并记录最终准确版本及 lock/requirements freeze，以可复现为目标。
- Mac/Linux：`bash scripts/setup_env.sh`；Windows PowerShell：`powershell -ExecutionPolicy Bypass -File scripts/setup_env.ps1`，或在当前 PowerShell 中执行 `./scripts/setup_env.ps1`。
- 编码规范：类型标注、Pydantic schema、pytest、ruff；不能依赖真实 API Key 才能启动服务和运行单元测试。
- 仓库内 `.env.example` 提供占位符，`.env`、`.venv`、真实令牌和产物不得提交。
- `.venv` 不能从另一台机器拷贝或随 ZIP 交付，必须在使用机器上重新建立。

## 已有 starter 的内容

- `GET /health`、`POST /api/spec/summary` 已实现基础 OpenAPI 3.x 路径摘要（JSON）。
- `tests/test_contracts.py` 有 3 个基础测试。
- 这是最小骨架，不等于已经实现 AgentTest。必须在以下阶段逐步扩充。

## 阶段 1（本次 Codex 执行重点）：确定性基础，可离线演示

请自行确定拆分 commit 的粒度，依次完成以下工作。原则：一次只交付可测试功能，任何一步失败先修复再继续。

1. **契约解析与验证**：支持 OpenAPI 3.0/3.1 JSON、YAML；检查请求体大小、拒绝无效文档；提取 method、path、operationId、参数、requestBody、响应状态、鉴权/安全要求。对 `$ref` 使用安全的解析策略，禁止自动加载远程 URL。以本地文件 fixture 测试解析质量。接口文档输入均视为不可信数据。
2. **测试环境隔离**：显式配置目标服务、允许的接口操作和角色账号别名。仅允许 localhost / 127.0.0.1 或明确授权、严格校验的测试环境地址。实现重定向/SSRF 防护和请求上限；写操作必须明确处于授权的隔离测试模式。不得访问生产网络。
3. **测试计划 DSL**：用 Pydantic 定义 Scenario、Step、ArgumentBinding、AssertionPolicy、ExpectedStatus；最初仅人工编写 JSON 计划。支持前一步的 `courseId` / `batchId` 传到后续步骤；拒绝未知 operationId、循环依赖与无凭据的受限操作。
4. **确定性 HTTP Executor**：使用 `httpx`、明确的连接/读取超时、总步骤上限、可控重试与证据采集。支持 JSON 请求和 multipart CSV 上传。隔离每个 run 的状态；没有 LLM 的情况下就能完成一条跨接口链路。
5. **Verifier + Report**：断言来自人工审查后的契约/策略，支持 HTTP 状态、JSONPath 的安全子集或结构字段、任务终态与条数；错误分为 PASS / ASSERTION_FAILED / TEST_INFRA_ERROR / TOOL_ERROR / NEEDS_REVIEW。结果写到 `runs/`，全面脱敏。禁止静默将异常归类为接口 Bug。
6. **mock 被测服务与验证**：提供 FastAPI 本地 mini target 或 MockTransport（建议都支持），测试登录→创建资源→查询以及异步轮询的模拟链路；增加至少 2 个可重现的故障样本（例如资源归属校验失败、错误终态），验证判定逻辑。
7. **CLI / API**：有明确的命令运行 demo scenario、导出 JSON 报告的功能；FastAPI 提供健康检查、导入契约摘要与运行结果查询。第一阶段不需要 Web 前端。
8. **质量门禁**：`.venv` 中运行 `python -m pytest -q`、`python -m ruff check .`。README 包含启动命令、流程演示、环境要求、明确的已完成/未完成模块。

**验收：** 在无 LLM key、无 Docker、无外网的情况下，创建 `.venv` 后本机运行一段安全的确定性多接口测试，能够输出可审计的 PASS/FAIL 结果与测试证据；所有测试可重复执行。

## 阶段 2：LangGraph Agent（第一阶段完成后再做）

- Agent 读取 OpenAPI 与业务策略，使用模型结构化输出生成 **DSL 计划**。
- LangGraph 节点：`load_contract -> plan -> validate -> setup -> execute -> verify -> analyze_or_finish -> report`。
- 在节点级跟踪状态，限制最大步骤、模型请求预算、最大工具调用和重复规划轮次；失败时不会自动降低/篡改断言。
- 用 fake LLM / deterministic stub 测试 Agent 工作流，不依赖付费 key；可选配置 OpenAI 兼容 API 使用真实模型。仅把 API key 放在本机 `.env` 中。
- 验收：用户输入“验证课程创建和跨用户访问”，Agent 输出合法计划并在 mock 环境执行；工具失败、模型返回非法 JSON 都有可靠兜底。

## 阶段 3：MCP + 真实 Java 被测系统

- 使用 MCP Python SDK **v2**，独立 MCP Server 暴露 `list_operations`、`get_operation_schema`、`execute_case`、`poll_async_task`、`read_test_log`，客户端严格校验工具名与参数。MCP 不承担安全边界，服务端自己负责授权、限流、环境白名单与审计。
- 接入 `mofengyan595/courseinsight-cloud` 的**独立运行实例**，严禁更改其原有仓库业务代码以迎合测试。
- 基于该项目 `scripts/DemoSmoke.java` 的真实演示链路（具体请求/字段需由 Codex 重新读取验证）：
  1. `POST /api/auth/login`；
  2. `POST /api/courses`；
  3. `POST /api/courses/{courseId}/analysis-batches`；
  4. `GET /api/analysis-batches/{batchId}`；
  5. `GET /api/analysis-batches/{batchId}/results`。
- 当前 Java `pom.xml` 不能确认已有 springdoc-openapi；先使用从真实接口整理并人工核对的契约文件，不要假设 `/v3/api-docs` 已存在。
- 当前公开 Demo 账户只保证 admin 角色；跨用户与多角色测试需要专门的测试 fixture、账号、授权策略。不应使用数据库直改生产用户，也不要写死凭证进仓库。
- 建立隔离测试数据库或测试命名空间，保存每个 run 的测试数据清理记录；外部副作用可追踪。
- 验收：在授权环境演示 JWT 登录、多步接口依赖、CSV 上传、异步状态轮询和结果断言；能在 MCP 工具失败时安全停止。

## 阶段 4：Agent 评测和回归脚本

- 预先人工标定 60 条 Golden Tests，四类各 15：正常业务、异常参数、权限/归属、异步状态。
- 注入已知缺陷到独立测试 target，记录 ground truth。严禁为展示效果修改原始正常业务的主分支。
- 与固定 pytest、基于 OpenAPI 的传统测试工具（如 Schemathesis）进行同预算对照，衡量任务成功率、计划有效率、缺陷检出率、误报率、P95 延迟和模型成本；至少三轮重复结果。
- 已确认且有稳定断言的场景导出 pytest，测试运行必须自动化、可重复；记录工具版本和结果证据。
- 可选使用 Langfuse 记录 Trace；剔除凭证与敏感数据。
- 验收：有自动生成/导出的 pytest、完整证据报告、对照实验，数字真实可重现。

## 阶段 5：可展示和可复用

- 简洁测试工作台（上传文档、输入需求、执行进度、问题复现卡片）。
- 第二个独立 HTTP 被测服务，验证本项目并非 CourseInsight 特供脚本。
- Docker 化、README 录屏/截图、架构图、运行说明、失败注入操作和测试数据说明。

## 关键安全/工程原则

1. AI 不是最终测试裁判：断言依据可追溯，证据不足标记 NEEDS_REVIEW。
2. LLM 输出任何代码、URL、Shell 命令均不直接执行；只使用受约束 DSL 和批准的操作。
3. 不访问未知服务、私网主机或云元数据地址，不追随重定向跳出允许域；严格限制请求大小、频率和步数。
4. 凭据脱敏，默认不保存 JWT/密码、请求体里的敏感字段和真实用户数据。
5. 失败分类准确：断言失败不是基础设施故障；超时不能直接证明业务存在 Bug。
6. 任务中断/重试必须识别写操作副作用；保存 step run id，不能因重启重复写数据而产生假结果。
7. 绝不编造提高百分比、缺陷数量、运行耗时或测试覆盖率。

## Codex 每次交付须汇报

- 本次完成的功能和文件清单；
- 实际运行的命令以及成功/失败结果；
- 可复制的运行/演示命令；
- 当前已知局限、下一步具体任务；
- 变更只在当前 AgentTest 仓库，不修改 Java 项目。
