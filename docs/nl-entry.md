# 自然语言入口：项目配置复用

2026-10-20新Goal按[原始目标](nl-entry-goal.md)推进。阶段1–2提供CLI预检和同一Workflow执行，
不要求受支持需求另写完整步骤、资源偏序或逐题TaskContract；旧`agent --project`保持兼容。
三层分组、Schema候选、工具协议适配、有界会话、SQLite任务服务和工作台入口已实现。
阶段5独立新需求真实验证与最终效果结论尚待执行。

## 首次接入与新增需求

操作员一次提供`registered.project.json`，引用同目录内的OpenAPI、目标授权、PolicyRegistry
与`goals.json`。目标目录只配置可复用动作词、角色、策略引用及状态相关策略选择，
不包含逐题原文、步骤、答案或参考计划。当前[工单](../examples/portability/tickets/registered.project.json)
和[课程](../examples/portability/courses/registered.project.json)复用原有契约与策略。
凭据只在执行环境中提供；模型上下文不包含地址、凭据环境变量或验收Rubric。

```powershell
.\.venv\Scripts\python.exe -m agenttest goal `
  --project examples/portability/tickets/registered.project.json `
  --user-task '测试创建工单接口。' --preview
```

预检需要本地配置的对应角色凭据，未配置返回`PROJECT_CREDENTIAL_PREFLIGHT_FAILED`；
不读取模型设置、不发送请求。执行时以`--real --model-env .env`替换`--preview`；
只能使用已授权的独立测试目标，样例目标自身必须由操作员启动。
`--fake-plan <本地Scenario文件>`用于离线机制验收，不证明真实模型规划质量。

每个新需求只输入原文，例如“验证创建、关闭、查询，以及其他用户不能读取的流程”。
系统先解析受控词汇，保留不能识别的剩余要求；再从批准BindingRule派生动态ID生产/消费、
角色关系、每角色登录前置条件和原文顺序。关闭后的查询选择原批准closed策略；
输入缺省值由Schema与批准字面量规则交集生成，单一自由文本字段可使用原文引号值。
没有从模型计划反推目标，也不为生成后计划增补步骤、换策略或重排。

## 支持边界

当前支持命名动作、唯一operationId或`METHOD /path`定位，普通JSON标量、
批准的资源依赖、预置Bearer/登录认证与状态策略关系。多义读取接口会询问预期；
未知目标、数量、多资源重复动作、任意额外语义、未知角色、CSV/轮询及未批准输入待审。
使用封闭语法，不宣称任意自然语言语义理解。

## 三层自动测试与明确分母

单接口添加`--suite`：复用既有发现器生成正常、必填、类型、枚举和数值/长度边界候选。
当前自动执行的是Schema与原批准规则都接受的正常/枚举/合法边界输入，重复输入合并。
无批准错误响应预期的非法输入仅列待审，不会绕过Executor的请求校验去发送畸形数据。
因此完整单接口套件可能部分待审；正常样例PASS不表示所有负例已经验证。
原文钉定字面量时不替换它，额外变体标记待审。

跨接口仍由原文及依赖派生完整约束，模型漏测/额外操作/错误角色/来源/顺序原样拒绝。
项目级“测试这个项目已授权的核心接口”自动按目标目录及状态变体分组，
子用例都走相同Workflow，不使用逐题任务映射。预览列出分组、待审、预算与写入副作用，
按固定顺序预留请求；超过总请求预算的组在执行前明确排除。
例如工单六组需要11次业务请求，原目标仅授权10次，最后列表组不执行；不会自行增额。

```powershell
.\.venv\Scripts\python.exe -m agenttest goal `
  --project examples/portability/tickets/registered.project.json `
  --user-task '测试创建工单接口' --suite --preview
.\.venv\Scripts\python.exe -m agenttest goal `
  --project examples/portability/tickets/registered.project.json `
  --user-task '测试这个项目已授权的核心接口' --preview
```

总预算从项目配置预先固定：默认套件8次模型/100000 Token、请求数沿用目标max_requests，
单run上限4模型/2只读不变。所有子用例共享请求transport计数和模型额度，超时也计已发送尝试；
真实模型用量未知即停止后续调用，不通过子用例重置预算。调查仅使用项目级批准能力。
报告分别列接口/场景分母、配置通过、候选、实际规划、实际执行、断言通过与未执行原因；
候选数量不等于已执行覆盖，部分预算内通过不冒充完整任务完成。

执行仍走已有DSL/OpenAPI/Policy/Intent/Executor/Verifier；派生TaskContract仅为本地校验快照，
不进入规划上下文或充当独立评测标签。计划遗漏、额外操作、错角色/来源/偏序在HTTP前拒绝。
未知业务预期不会生成可信断言；配置通过、规划通过与业务断言通过分别判断。

预算继续受目标请求数与步骤数限制，单run默认最多4模型/2只读调查；
默认一次规划，不隐式重试。项目可通过本地`capabilities`文件引用批准只读能力；
配置时检查GET、角色与来源绑定关系，失败后复用原有最多两次调查/三次反馈，
原业务、模型建议及可信规则结果分别保存。规划与反馈共享单run和套件预算，未知用量停止。
真实三轮36/36已完成，72次/173894 Token全部已知；完整计划23/36、完整任务15/36。
新增授权重试独立留档，不替换原失败；[价值、失败与分母](nl-entry-value-report.md)。全部为DEVELOPMENT_ONLY/UNREVIEWED。

## 工具协议与有界上下文

统一注册表描述工具名称、参数Schema、用途、权限、资源关系与结果类型；
既有MCP工具广告/参数门禁与原生规划提交工具共用注册信息及已有引擎。
原JSON动作继续可用。`native_function_calling=true`仅由操作员项目配置启用；
原生工具只能提交不可信计划，不能直接指定任意HTTP或执行代码。
只接收单一`submit_test_plan`函数，拒绝未知工具、并行调用与额外正文；
调用参数继续经过原Generic DSL、授权及意图校验。

实现前于2026-10-20核对[官方Function Calling](https://developers.openai.com/api/docs/guides/function-calling)
和[MCP Python SDK v2结构化输出](https://py.sdk.modelcontextprotocol.io/v2/servers/structured-output/)。
本轮原生函数协议仅有Mock验收；没有真实供应商验证结果。
未显式声明支持或使用chatecnu时，返回`NATIVE_FUNCTION_PROTOCOL_UNSUPPORTED`，零请求，不猜测兼容性。
本轮已配置供应商真实试验采用原JSON协议，不把JSON实测当作原生Function Calling实测。

`GoalConversation`保存最多8轮、累计原文2000字符的内存上下文，支持“先测创建”再“补权限检查”。
保留原始需求、澄清哈希、不可变计划版本、报告引用和共享剩余额度；不保存业务正文或模型原文。
补充需求生成新版本，新执行使用新资源，已开始的计划不能改写或重复执行。
项目/策略身份变化必须重新派生校验，新版本不能提高会话原有预算；
明确澄清可替换未验证需求的有效范围，但原始历史保留，不能授予新断言或权限。
这些是任务内状态，不是长期语义记忆，也不恢复或重放HTTP。

## CLI、任务服务与工作台

CLI已在零模型/真实业务HTTP条件下跑通四步工单流程；无需另写TaskContract。
`--synthetic-target`只可使用已核对的课程/工单契约，创建一次性ASGI应用与临时合成角色凭据。
`--export-regression <新目录>`可在单接口或流程完整通过后直接使用原固定pytest模板导出。
需要对导出提供一次接入的Runtime fixture，复用当前项目配置；无需模型或重新写完整测试步骤。

服务默认仍可在无Key状态使用原只读能力。操作员要启用任务入口时显式设置：

```powershell
$env:AGENTTEST_PROJECTS_FILE = 'examples/registered-projects.json'
$env:AGENTTEST_MODEL_ENV = '.env'
$env:AGENTTEST_SYNTHETIC_TARGETS = '1'
$env:AGENTTEST_RUNS_DIR = 'runs/nl-entry-service'
.\.venv\Scripts\python.exe -m uvicorn agenttest.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers
# 另一终端：Set-Location web；npm run dev；打开 http://127.0.0.1:5173/tasks
```

这是配置供应商真实规划/合成业务目标的模式，按已授权预算产生模型调用。
默认离线演示仍用`--fake-plan`或统一showcase，不需要供应商Key；测试服务使用固定Fake回复，
不能把浏览器验收或模拟调用计作真实供应商验证。

工作台可选择注册项目、输入需求、查看派生约束和预算、接收SSE/轮询进度、取消后续动作、
查看分层证据及下载固定pytest包。浏览器只能提供项目ID、需求和会话标识，不能提交目标URL、
凭据、策略或代码。重试同一内容复用提交ID，避免网络超时后重复创建任务；新独立任务显式重置。
只有项目`require_plan_review=true`才等待对派生约束的审阅；模型计划仍必须匹配已审约束。
CLI相应先`--preview`，显式审阅后用`--review-approved`，普通授权动作不重复确认。

SQLite仅保存任务状态、输入摘要、脱敏约束、用量和报告引用，不保存凭据、原业务正文或模型原文。
原始需求仅在有界任务上下文内存中，补充需求创建新任务并共享原剩余预算；原执行计划和结果保持不变。
进程独占锁禁止两个服务竞争同一数据库；默认一个任务执行、最多八个排队/运行、最多1000条元数据。
取消保留实际请求证据与已发送计数，不声称回滚。服务重启后未完成任务为INTERRUPTED，
不恢复HTTP或重放写入，原始上下文和内存导出候选不恢复；旧元数据/报告仍可读。
本地HttpOnly/SameSite会话、CSRF、同源和回环边界用于本地模式，不是公网多租户认证。

导出只接受当前会话拥有的已完成通过run，且项目身份必须未变；服务端选择新目录，
浏览器不能传文件路径。导出使用可信固定模板及经过全部门禁的DSL，不执行模型代码。
独立任务测试验证账号、ASGI资源、请求预算和报告隔离；同会话后续任务的额度不会刷新。

三层可复制命令见[运行与验收](nl-entry-delivery.md)，首次新需求与固定回归见[实际结果](nl-entry-value-report.md)。
