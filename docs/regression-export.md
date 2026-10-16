# 固定 pytest 回归导出

将完整通过的自然语言计划保存为固定回归包，后续由 pytest 复用原 Executor 和批准断言执行，无需再次调用模型。初版限定普通 JSON 场景；CSV 上传、轮询和 read_retries 暂不导出。

## 离线复现

```powershell
.\.venv\Scripts\python.exe -m pytest examples/regressions -q
.\.venv\Scripts\python.exe scripts/check_regression_exports.py --output runs/regression-acceptance-new
```

第一条执行三个正常目标，应为 3 passed。第二条还在三个全新的进程内目标注入主题错误、越权泄漏、关闭无效，核对 pytest 实际退出码与原断言；应汇总正常 3 项通过、故障 3 项失败。输出必须是新的仓库内 runs 子目录，原始日志和报告不提交。Windows 受限环境若无法创建 asyncio 本地通信套接字，需在允许该操作的环境执行，不能关闭测试禁网保护。

三个[样例包](../examples/regressions/README.md)来自本轮固定 Fake 工作流的通过报告，真实模型调用为 0，没有 Java 或真实数据库请求。旧真实模型归档只保存参数摘要，且缺少新增的配置来源摘要，不能从中反推完整计划或补造导出资格。此前的真实模型成绩保持独立。

## 两种导出方式

通用 `agent` 命令增加可选 `--export-regression <新目录>`，必须同时提供操作员 TaskContract。先检查输出目录，再沿用原规划、策略、意图和执行流程；只有完整成功才导出。模型回复仅在本次进程内暂存，失败后不导出。目录的父目录必须已存在，末级目录名只允许字母开头及字母、数字、下划线、连字符，禁止覆盖。

已有本版本完整通过报告，并单独持有原始完整 Scenario 时，也可仅导出：

```powershell
.\.venv\Scripts\python.exe -m agenttest export-pytest `
  --contract <契约文件> --target <批准目标配置> --policies <批准策略文件> `
  --task-contract <任务约束文件> --plan <完整Scenario文件> `
  --source-run <通过的run_id> --runs-dir <原报告根目录> --output <新的回归目录>
```

此命令不执行 HTTP 或模型。源报告必须同时满足自然语言模式、任务完成、意图验证、策略授权、完整工作流及所有步骤 PASS；契约、目标配置、完整策略目录、计划参数与步骤证据必须匹配。旧报告继续可读、原哈希不变，但缺少新来源摘要时不能导出。

## 包内容与运行环境

每包包含 scenario.json、manifest.json、README.md 和一个固定模板生成的 Python 测试文件。Python 内容来自仓库常量，模型文本只作为 JSON 数据保存，绝不拼接成代码。scenario.json 是明文合成测试参数；导出器拒绝已知凭据、敏感字段及地址字面量，发布前仍须检查业务数据。包内不含目标配置、凭据、策略定义、HTTP 响应或原模型回复。

操作员在自己的 conftest.py 提供 `agenttest_regression_runtime` fixture，返回 `agenttest.regression.RegressionRuntime`，字段为：

| 字段 | 可信来源 |
| --- | --- |
| contract_document | 已审核 OpenAPI 文档字节 |
| target、registry、task_contract | 已批准 TargetConfig、PolicyRegistry、TaskContract |
| environ | 运行时凭据环境，不能写入回归包 |
| transport | 显式 HTTPX AsyncBaseTransport；样例使用 ASGITransport |
| store | 本次报告的 ReportStore |

可参考[离线 fixture](../examples/regressions/conftest.py)。缺少 fixture 会直接报 pytest 配置错误；没有默认网络目标。真实服务的许可、SSRF 防护和写入授权继续由原安全边界约束。执行前应审阅包及 fixture；哈希用于发现变更，不是来源认证，也不是执行任意 pytest 文件的沙箱。

每次重放重新校验契约、目标、策略目录、TaskContract 和静态步骤策略摘要，创建新 run_id。配置变化会在请求前拒绝，需按原门禁重新验证并生成新包。报告保留 operator_plan、task_satisfied=null、model_call=null；只表示固定计划本次执行结果，不代表模型重新理解了需求。原断言失败使 pytest 失败，不自动改断言、重试写入或恢复旧会话。

新增报告字段 target_config_sha256、policy_registry_sha256 和 policy_template_sha256 记录执行来源；原 policy_sha256 继续记录绑定实际响应 ID 后的策略，二者用途不同。新字段为可选，旧报告序列化时不补 null，保留历史封印的摘要。

## 本轮验证与下一步

已实际执行三个导出包：正常目标 3 passed；指定故障目标 3 failed，退出码 1，分别命中原主题、HTTP 权限及关闭状态断言。公开验收器已核对六份报告，无模型、Java 或真实数据库调用。单元与 CLI 检查覆盖来源不完整、配置变化、敏感数据、篡改文件、文件链接、模板替换和失败不导出；CI 在 Windows／Linux 的基础／Agent 四种环境运行三个固定样例。

本轮不更改规划提示或历史任务评分，未申请正式评测资格。下一准确里程碑：统一项目接入配置与执行前检查，明确契约、目标、策略、TaskContract 的缺失和版本冲突，优先改善新项目接入体验；嵌套 JSON 等规划能力及独立审核验收另行推进。
