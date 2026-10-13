# 真实模型开发 A/B 诊断

该入口仅用于 1–2 条 AI-generated / UNREVIEWED 开发任务，固定标记
`DEVELOPMENT_ONLY`。它不调用 Eval v2 的正式 real runner，不生成独立人工审核锁，
不替代 `independent_holdout`、真实审核记录和 `validate --formal`。
原有正式门禁、Workflow、Executor、Policy、Intent 和评分公式保持不变。

## 运行方式

`scripts/development_ab.py` 在同一次调用内依次执行 A 的选定任务，再执行 B 的同一批任务。
任务顺序由 `--case` 决定；不混入旧 Smoke Test。原始 Suite 不会被改写。
输出必须位于 Git 忽略的 `runs/`，并且使用全新目录；不支持覆盖或恢复执行。

A 使用现有公开 `planning_context`；B 复用现有 Eval v2 的 Candidate Discovery
过滤与上下文构造，只追加 `candidate_discovery`。只提供匹配当前公开 Operation、
Role、Policy 的正常候选及依赖元数据；候选仍是建议，不能授权执行。
两个上下文通过 `ContextPlanner` 进入同一个 `OpenAICompatiblePlanner` 和
`Workflow / Executor`。不读取 Rubric 生成真实模型计划，不向模型提供答案或故障标签。

先做无联网准备。以下私有 Suite 路径是占位符，应换成实际登记文件；不要把题目和标签提交到 Git：

```powershell
.\.venv\Scripts\python.exe scripts/development_ab.py prepare `
  --suite runs/private/development-suite.json --case C101 --case C105 `
  --model-env .env --output runs/development-ab-preparation-NEW
```

准备操作只读取本机配置和可信输入，输出公开配置、上下文和预算，
始终记录 `operator_authorized=false / ready_for_paid_run=false`。

不收费的运行必须显式提供固定夹具，不能从标签自动回退到正确计划：

```powershell
.\.venv\Scripts\python.exe scripts/development_ab.py run `
  --suite runs/private/development-suite.json --case C101 --case C105 `
  --source mock_transport --responses runs/private/mock-responses.json `
  --output runs/development-ab-mock-NEW
```

`fake` 模式夹具为 Case ID → Scenario；`mock_transport` 为 Case ID → 模型响应 envelope，
其中 usage 必须有效。模拟非 200 响应时可用 `{"http_status":401,"body":{}}`。
离线模式拒绝 `--model-env` 和 `--real`，使用合成凭据，真实调用计数为 0。
Fake/Mock 只能证明链路和门禁，不能证明模型规划成功或候选增益。

只有操作员另行明确授权后，才可执行真实命令：

```powershell
.\.venv\Scripts\python.exe scripts/development_ab.py run `
  --suite runs/private/development-suite.json --case C101 --case C105 `
  --source real --real --model-env .env `
  --allow-model ecnu-plus --allow-endpoint https://chat.ecnu.edu.cn/open/api/v1 `
  --max-calls 4 --max-total-tokens 88192 `
  --output runs/development-ab-real-NEW
```

## 预算与停止条件

双组共享一个计数器，最多 4 次调用；每次调用前预留 22,048 Token，
合计最多 88,192。即使未发送或 usage 未知，也不退还预留以继续调用。
每次输入上限 20,000、输出上限 2,048，模型超时 30 秒、节点超时 31 秒、
Workflow 超时 120 秒；无重试、重新规划、缺陷配对或额外模型探针。

业务目标只能是新建的进程内 mini ASGI fixture，每个 Workflow HTTP 硬上限 40；
两条任务双组合计保守上限 160 次。正确计划通常合计 16 次，但这不是运行保证。
模型网络仅使用操作员固定的 HTTPS Endpoint，禁用代理、重定向和 transport 重试。

预算不足、鉴权/连接/限流/超时/协议错误、usage 无法确认、任一任务未完成，
或者代码、Suite、响应夹具、Prompt、策略、契约或上下文漂移，都停止后续组和任务。
保留尚未执行的行与原因，不静默重跑。进程异常退出时未封存目录视为不完整，不能恢复付费执行。

## 证据与解释

复用现有开发冻结快照、传输来源计数、`score_report`、`capture_plan`、归档 seal、
`verify_archive`、`rescore` 和同 Case `compare`。每组记录上下文、代码和模型公开配置摘要，
以及计划参数摘要、依赖、Selector、执行报告、真实/合成 usage 和耗时。
模型原始输出、密钥和业务凭据不落盘。
正常完成后输出 `comparison.json`；停止后仅输出逐 Case 诊断配对，明确不可比较。

冻结快照记录开发工作区的实际内容，允许未提交但禁止运行中漂移；
它不代表人工审核或正式版本锁。归档哈希用于发现文件变化，不能防御拥有操作系统写权限的恶意改写者。
私有数据需要操作员独立介质备份。价格、货币扣费和账户余额不在该入口查询范围内。

两个已使用过且受明确 Prompt 引导的开发样本，只能提供描述性配对观察。
固定 A→B 顺序还可能受服务负载、缓存或模型服务变化影响；不能据此宣称统计显著性或泛化增益。
