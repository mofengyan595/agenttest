# AgentTest

独立 Python API 测试平台。已实现阶段 2A 自然语言规划：用户需求 → FakeLLM 或显式配置的真实模型 → 不可信 DSL → 契约及可信策略预检 → 受限 HTTP 执行 → 确定性断言 → 脱敏报告。默认测试及 demo 无需 API Key、Docker 或外网。真实模型调用必须显式启用。

## 环境与启动

使用 Python **3.11**，所有依赖安装在项目 `.venv`。本次验证版本为 3.11.15。

GitHub 仓库：[mofengyan595/agenttest](https://github.com/mofengyan595/agenttest)。首次获取项目：

```powershell
git clone https://github.com/mofengyan595/agenttest.git
Set-Location agenttest
```

Windows PowerShell：

```powershell
.\scripts\setup_env.ps1
# 没有 py 启动器时，可指定本机 Python 3.11：
# .\scripts\setup_env.ps1 -PythonPath 'C:\path\to\python.exe'
.\.venv\Scripts\python.exe -m uvicorn agenttest.main:app --host 127.0.0.1 --port 8000
```

macOS / Linux：`bash scripts/setup_env.sh`，随后使用 `.venv/bin/python`。该脚本尚未在本次 Windows 环境执行验证。

脚本保留已有 `.venv`，检查解释器版本；安装冻结依赖，再执行任务书要求的 `pip install -e '.[dev]'`（禁止构建隔离以沿用冻结的构建工具），最后运行 pytest 和 Ruff。不会修改全局 Python。

`requirements-dev.lock` 是本次 Windows/Python 3.11 环境的准确版本快照，未包含机器路径。首次安装需要包源或预先准备的 wheel 缓存；安装后的默认测试与演示不需要外网。离线安装可事先下载这些依赖到 `artifacts/wheels/`，再使用 `pip install --no-index --find-links artifacts/wheels -r requirements-dev.lock`。

本机没有独立安装的 3.11，因此额外使用项目内、被忽略的 `.python311/` 运行时作为 `.venv` 的基础解释器。另一台机器应重新创建环境，不能复制 `.venv`。

阶段 2 可选安装：Windows 使用 `.\scripts\setup_env.ps1 -Agent`，macOS/Linux 使用 `bash scripts/setup_env.sh --agent`。已有 `.venv` 也可执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-agent.lock
.\.venv\Scripts\python.exe -m pip install --no-build-isolation -e '.[dev,agent]'
.\.venv\Scripts\python.exe -m agenttest workflow-demo
```

Agent extra 固定 LangGraph 1.2.14；完整版本在 `requirements-agent.lock`。安装时需要包源或缓存，安装后测试与工作流无需外网。仅基础环境运行 pytest 时，工作流测试模块会明确跳过；完整验收应安装 agent extra。

## Fake LLM 最小工作流

图顺序为 `load_contract -> plan -> validate -> execute -> analyze_or_finish -> report`。执行节点直接复用 Executor/Verifier；校验失败跳过执行。`workflow-demo --fault ownership` / `--fault terminal` 保留两个已知故障样本。显式本地目标可运行：

```powershell
.\.venv\Scripts\python.exe -m agenttest workflow --contract examples/demo-openapi.json --plan examples/demo-scenario.json --target examples/demo-target.json --policies examples/demo-policies.json
.\.venv\Scripts\python.exe scripts/smoke_local_http.py --workflow
```

这里 `--plan` 是 Fake LLM 返回的 JSON 样本，仍按不可信模型输出处理。操作员另行加载目标配置与 Policy Registry；Planner 接收 user_task、白名单操作、参数/请求 Schema、认证要求及策略适用前提，不接收目标环境、账号凭据或批准权。原始规划结果、凭据、HTTP 正文不进入图状态，云追踪显式禁用。

每次运行至多一次规划、一次执行器调用；HTTP 请求继续受目标预算限制。`WorkflowBudget` 由操作员构造，模型不能修改。失败分析是固定分类建议，不重写断言、不自动重试整条场景；报告记录节点序列、调用次数和完成状态。当前不支持恢复、重复规划或动态调查。

## 阶段 2A：真实自然语言规划

模型和 Base URL 没有默认值，来自本机环境变量，或显式 `--model-env .env`。文件只读取 OPENAI_API_KEY、OPENAI_BASE_URL、AGENTTEST_MODEL，进程环境优先；不执行、不展开文件内容。学校 ChatECNU 按其官方协议使用 `max_tokens` 和 `response_format.json_schema`，不会自动切换到 OpenAI 官方地址。

```powershell
# 零生成 Token 的连通性与模型存在检查
.\.venv\Scripts\python.exe scripts/probe_model.py --model-env .env
# 两类真实规划，目标为本次运行的一次性 mini target
.\.venv\Scripts\python.exe -m agenttest agent-demo --real --model-env .env --user-task '用户 A（alice）创建“线性代数”课程，再查询并核对 ID 和名称一致。'
.\.venv\Scripts\python.exe -m agenttest agent-demo --real --model-env .env --user-task 'alice 创建“概率论”课程并查询核对，再让已登录 bob 查询同一课程被拒绝。'
# 无模型、无网络，验证自然语言规划入口与执行链路；这不测试自然语言理解
.\.venv\Scripts\python.exe -m agenttest agent-demo --fake-plan examples/nl-consistency-plan.json --user-task 'alice 创建“线性代数”课程并查询核对。'
```

`user_task` 最多 2000 字符/8000 UTF-8 字节，拒绝控制字符、URL、明显凭据及 Vault 已知秘密。自然语言仍是不可信数据；提示词本身不提供授权。参数化策略只开放有界合成名称和审核过的来源/角色关系，状态码与比较语义固定。真实结构化格式目前支持标量 JSON 参数及步骤引用，不支持由模型规划 CSV、复杂对象或轮询；原确定性 DSL 的这些功能继续保留。

真实接口 `agent` 与 `agent-demo` 共用同一 Workflow/Executor/Verifier；前者要求显式 `--contract --target --policies`，目标账号仍来自执行器环境。详细配置及预算见 [模型配置说明](docs/model-configuration.md)。

18 条 [人工确认任务](docs/natural-language-tasks.md) 仅代表两个意图的措辞、角色和合成数据变体。评测分别统计计划有效、策略选择、意图满足及端到端成功；执行报告 PASS 只表示所选策略通过，不能自动证明自然语言需求完整满足。

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_planners.py --baseline manual --output runs/evaluation/manual.json
.\.venv\Scripts\python.exe scripts/evaluate_planners.py --baseline fake --output runs/evaluation/fake.json
# 显式启用真实学校模型；最多 18 次、累计 60000 Token，每次输出最多 2048
.\.venv\Scripts\python.exe scripts/evaluate_planners.py --baseline real --model-env .env --max-live-calls 18 --max-total-tokens 60000 --output runs/evaluation/real.json
.\.venv\Scripts\python.exe scripts/smoke_natural_http.py
```

三基线边界、实际首次评测和失败证据摘要见 [阶段 2A 评测记录](docs/stage2a-evaluation.md)。真实评测脚本不在 pytest 自动收集或 GitHub CI 中调用。

API 已按 [官方 Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)、[API 参考](https://reference.langchain.com/python/langgraph/graph) 和 [禁用追踪文档](https://docs.langchain.com/langsmith/conditional-tracing) 核对，并实际运行 `StateGraph`、条件边、`compile()`、`ainvoke()`；版本见 [PyPI 1.2.14](https://pypi.org/project/langgraph/1.2.14/)。

## 运行完整演示

```powershell
.\.venv\Scripts\python.exe -m agenttest demo
.\.venv\Scripts\python.exe -m agenttest demo --fault ownership
.\.venv\Scripts\python.exe -m agenttest demo --fault terminal
```

正常场景 8 步、9 次请求：alice/bob 登录 → alice 创建课程 → 查询课程 → bob 跨用户访问得到 403 → 上传两行 CSV → 轮询 RUNNING / SUCCEEDED → 核对结果条数。每次演示创建独立内存状态和随机临时凭据，不改变进程环境。

正常返回 `PASS`、退出码 0；两个故障分别在 `cross_user`、`poll_batch` 检出 `ASSERTION_FAILED`、退出码 1。故障仅注入独立模拟服务，未改动 CourseInsight Cloud。

终端返回 run_id 与报告位置 `runs/<run_id>/report.json`：

```powershell
.\.venv\Scripts\python.exe -m agenttest report <run_id>
.\.venv\Scripts\python.exe -m agenttest report <run_id> --output runs/export.json
```

证据包含步骤运行 ID、请求次数、HTTP 状态、响应字节数/摘要、耗时、断言结果以及契约/计划/策略 SHA-256。默认不保存原始请求体、响应体、URL 参数、认证头或 Cookie；字符串、容器及数值断言值默认隐藏，仅可信策略 public_evidence 批准的非敏感数值可展示。密码、验证码、身份信息、自定义 sensitive_fields 和已知秘密始终优先脱敏。进程中断留下的 `completed: false` 报告可供审计，不支持自动恢复或重复写入。

## API

- `GET /health`：健康检查。
- `POST /api/spec/summary`：原始 JSON、YAML，或兼容原有 `{"document": {...}}`。JSON 使用 application/json；YAML 使用 application/yaml。读取时限制 1 MB。
- `GET /api/runs/{run_id}`：只读查询已保存的报告；必须与 CLI 使用同一工作目录/runs 目录。

本地 API 没有公开的任意目标执行入口。Swagger UI 的静态资源默认来自 CDN，离线时请直接调用 API。

## 目标授权与人工 DSL

`examples/demo-scenario.json` 是不可信计划格式，只引用策略 ID。操作员分别管理 `examples/demo-policies.json`（Policy Registry）和 `examples/demo-target.json`（目标、业务场景、角色认证和预算）；两者不能从模型输出加载。`docs/demo-policy.md` 记录策略依据。文件审查及访问权限是目前的信任根，尚无多用户审批系统。

真实本地实例使用项目虚拟环境启动：

```powershell
.\.venv\Scripts\python.exe -m uvicorn agenttest.mini_target:local_app --factory --host 127.0.0.1 --port 8080
```

启动前在环境变量设置 `.env.example` 中的四个 DEMO_* 值，CLI 通过同样的环境变量读取账号。第一阶段不自动加载 `.env`。

```powershell
.\.venv\Scripts\python.exe -m agenttest run --contract examples/demo-openapi.json --plan examples/demo-scenario.json --target examples/demo-target.json --policies examples/demo-policies.json
```

安全边界：

- 仅接受固定 IP 或 localhost；localhost 固定成 127.0.0.1，不进行 DNS 查询。非本地 IP 必须是配置中逐个授权的隔离私网地址；拒绝公网、云元数据、未授权私网和特殊地址。
- 不使用契约 servers 选择目标，不接受 DSL URL；所有 HTTP 重定向直接阻断，关闭环境代理。
- 每个 operationId 必须在白名单；写操作须同时启用 isolated 和 allow_mutations。执行器只为批准的登录操作注入角色凭据，保管 Token，并在受限接口认证头使用。计划禁止 credential/auth_from，认证响应不可绑定；跨步骤数据移除敏感字段和已知秘密别名。
- 每次运行限制步骤、请求、请求间隔、输入/响应大小、连接/读取及总时长；只有 GET/HEAD 可以有限重试或轮询，写入不会自动重试。
- 轮询中间态、终态和每次响应约束来自可信策略；业务响应违反审核契约时为 ASSERTION_FAILED，等待耗尽为 TEST_INFRA_ERROR。
- 仅支持 HTTP bearer、JSON 与 CSV multipart、path/query 参数。必填 header/cookie 或其他认证方式会被拒绝。
- 请求校验覆盖字段名、严格基本类型、必填项、枚举、对象/数组、长度和数值边界；静态值预检，动态值发送前复检。未声明字段拒绝；复杂组合/联合、正则、开放对象及未知 format 进入 NEEDS_REVIEW。当前支持 format 为 binary/password/int32/int64，不声称支持完整 JSON Schema。
- 仅解析 OpenAPI 3.0/3.1，先拒绝远程/文件/循环引用和不受控扩展，再进行标准结构校验。第一阶段要求每个操作声明非空 responses；不支持递归/动态引用及带约束兄弟节点的引用（允许 summary/description）。
- JSONPath 仅支持 `$.field[index]`，不支持代码、过滤器或通配符。绝不执行模型生成的 Python/Shell。
- 计划禁止 assertions/reviewed/source，只能引用独立 Registry 的 policy_id。策略必须匹配接口、角色、可信业务场景及数据绑定前提；未知、不适用或未批准策略在发送请求前进入 NEEDS_REVIEW。

| 结果 | 含义 |
| --- | --- |
| PASS | 所有已审查断言通过 |
| ASSERTION_FAILED | 已审查断言与实际结果不符，仍须结合证据定位 |
| TEST_INFRA_ERROR | 超时、请求预算、轮询耗尽、重定向或大小限制等运行问题 |
| TOOL_ERROR | 非法计划、绑定或工具错误 |
| NEEDS_REVIEW | 缺少审查来源或明确预期 |

外部测试数据清理由操作员负责，第一阶段未实现外部环境自动清理、幂等恢复或生产访问能力。

## 验证与结构

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pip check
# 可选：开启临时 loopback 服务验证真实 HTTP，完成后自动关闭
.\.venv\Scripts\python.exe scripts/smoke_local_http.py
```

默认 pytest 使用 MockTransport/ASGITransport，主动阻断 DNS 和出站连接；Windows 异步事件循环内部 socket pair 除外。真实 HTTP 检查独立显式运行。

GitHub Actions 在 master 推送和 PR 时运行 Windows/Linux × base/agent 四组 Python 3.11 检查：独立 .venv、冻结依赖、pytest、Ruff、pip check 和临时回环 HTTP。无需 LLM Key 或 Docker；基础组明确跳过可选工作流模块，Agent 组运行全部测试。安装步骤需要包源，测试不访问外部服务。

```text
src/agenttest/
  contracts.py       OpenAPI 输入、安全引用、标准校验与摘要
  safety.py          操作员目标、角色与预算配置
  dsl.py             不可信计划及执行前校验
  policies.py        独立可信 Policy Registry 与作用域匹配
  credentials.py     执行器角色认证与跨步骤秘密隔离
  request_validation.py 明确的 OpenAPI 请求约束子集
  selectors.py       安全 JSONPath 子集
  executor.py        确定性 HTTP、绑定、重试、轮询和证据
  verifier.py        审查后确定性断言
  report.py          分类、摘要与持久化
  mini_target.py     一次性模拟服务及故障开关
  demo.py            离线演示组装
  workflow.py        Fake LLM、受控 LangGraph 状态图与预算
  planner_input.py   有界自然语言与筛选后的公共规划上下文
  llm_planner.py     真实模型结构化 Adapter 与学校协议适配
  model_probe.py     显式模型列表探测
  natural_demo.py    一次性自然语言测试目标组装
  evaluation.py     人工标注任务、三基线与分类指标
  __main__.py        CLI
  main.py            FastAPI 契约导入、健康与报告查询
tests/               单元、安全边界和完整链路测试
examples/            JSON 场景、目标与契约
docs/                策略、进度及本地提交记录
scripts/             环境准备、真实 loopback 检查
runs/                本地报告（忽略，不提交）
```

已完成阶段 1 安全修复、阶段 2A 真实结构化规划及首批 18 条受控任务评测。未实现 Function Calling 工具循环、MCP、RAG、CourseInsight 接入、阶段 4 的 60 条/多轮缺陷评测或前端。下一里程碑是根据保留的失败证据改善规划可靠性及诊断，继续固定业务断言与授权边界。详见 `CODEX_TASK.md` 与 `docs/progress.md`。

实现依据：[OpenAPI 3.1 规范](https://spec.openapis.org/oas/v3.1.0.html)、[文档校验器](https://openapi-spec-validator.readthedocs.io/en/latest/python.html)、[HTTPX transport](https://www.python-httpx.org/advanced/transports/)、[超时](https://www.python-httpx.org/advanced/timeouts/)、[FastAPI 文件上传](https://fastapi.tiangolo.com/tutorial/request-files/)。
