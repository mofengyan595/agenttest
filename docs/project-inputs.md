# 项目接入配置与离线预检

`agent --project` 将契约、目标、批准策略和任务约束集中到一个入口。它只引用操作员已经准备好的四份文件，不生成断言、自动批准策略或修改版本摘要。现有分别指定文件的参数继续可用。

## 项目配置

```json
{
  "schema_version": "agenttest.project.v1",
  "contract": "openapi.json",
  "target": "target.json",
  "policies": "policies.json",
  "task_contract": "read.task.json"
}
```

路径相对于此配置所在目录，可使用 `config/target.json` 等子目录；使用 `/` 分隔，禁止绝对路径、URL、`..`、目录链接和硬链接文件。项目配置最多 16,000 字节，引用文件各最多 1,000,000 字节。文件内容仍按原类型解析：OpenAPI 不授予权限，TargetConfig、PolicyRegistry 和 TaskContract 必须来自操作员审核。

现有样例：[课程](../examples/portability/courses/read.project.json)、[工单](../examples/portability/tickets/read.project.json)、[Petclinic](../examples/portability/petclinic/present.project.json)。不同任务应选择绑定该任务的 TaskContract；更换需求、目标或策略后，需重新核对并更新审核后的配置，预检不会替你重算并覆盖原摘要。

## 只检查配置

```powershell
.\.venv\Scripts\python.exe -m agenttest check-project --project examples/portability/petclinic/present.project.json
```

该命令可在基础依赖环境运行，不需要 LangGraph、模型 Key 或运行中的 Petclinic。只读取本地文件和当前进程的凭据环境，HTTP／模型调用均为 0，不创建运行报告，不修改输入。它不读取 `.env` 或 `--model-env` 中的业务凭据。

`configuration_ready=true`、退出码 0 表示本轮配置检查通过；否则为 false、退出码 2，并逐项报告 component、code 和 field。例如 `task_contract / PROJECT_VERSION_MISMATCH / policy_sha256` 表示任务约束与所选策略目录版本不符。错误只输出固定代码及字段位置，不回显异常、文件内容、任务原文或凭据值。

检查覆盖：文件缺失或拒绝解析、三个输入摘要是否匹配、任务引用的操作／角色／策略是否存在并获准、写操作许可、步骤预算、HTTP Bearer 认证支持及本任务所需凭据。未使用角色不强制提供凭据。常见代码：

| 代码 | 处理 |
| --- | --- |
| PROJECT_FILE_MISSING | 补齐 component 对应的本地文件 |
| PROJECT_INPUT_REJECTED | 检查结构、大小、路径与文件链接；不公开原始解析异常 |
| PROJECT_VERSION_MISMATCH | 对照 field 检查审核版本，不能直接忽略或自动重绑 |
| PROJECT_OPERATION_UNKNOWN / PROJECT_OPERATION_NOT_PERMITTED | 核对契约与目标允许操作 |
| PROJECT_POLICY_REFERENCE_REJECTED / PROJECT_ROLE_UNKNOWN | 核对任务引用及批准作用域 |
| PROJECT_LOGIN_CREDENTIALS_MISSING | 为对应任务角色提供登录环境变量 |
| PROJECT_BEARER_CREDENTIAL_MISSING_OR_INVALID | 提供该角色的非空、无换行 Bearer 环境变量 |
| PROJECT_TASK_TEXT_MISMATCH | 使用配置绑定的任务；新需求需另行审核 TaskContract |

配置通过不证明服务可连接、计划可行、任务完成或模型可靠，也不授予新的写入权限。原 DSL、Policy、Intent、SSRF、请求预算与执行门禁仍在计划生成后运行。

## 接入原工作流

专用隔离目标具备相应执行授权后，使用 `agent --project <项目配置> --real`，或使用 `--fake-plan <完整Scenario>`。默认沿用配置中 TaskContract 的任务文本；可显式传相同的 `--user-task`，不同文本会在模型调用前拒绝。`--project` 与 `--contract`、`--target`、`--policies`、`--task-contract` 单项参数互斥，避免混用两套来源。

`--fake-plan` 仅替换规划来源，agent 仍会执行配置的 HTTP 目标；它不会自动启动样例服务或切换进程内模拟。本轮集成测试显式使用一次性 ASGITransport，课程、工单均通过原 Workflow，并成功导出固定回归包；没有访问现有服务。

预检在读取模型设置、导入可选工作流和执行目标之前完成；失败不会消耗模型额度。通过后使用本次已读取的配置与凭据快照，后续文件变化不会被静默重新加载。可继续追加 `--export-regression <新目录>`，保留上一阶段的完整通过门禁。其他 run／discover／export-pytest 入口暂沿用原参数。

本阶段无新增运行依赖，没有修改通用规划提示、旧样本、业务预期、Executor 或正式评测资格。真实模型和 Java 调用均为 0；三个项目的配置检查与课程／工单离线执行属于接入机制证据，不增加跨项目真实模型成功率。下一里程碑优先处理通用规划入口的嵌套 JSON／数组／浮点字面量限制，再在新的开发任务上验证。
