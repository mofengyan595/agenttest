# 自然语言入口：项目配置复用

2026-10-20新Goal按[原始目标](nl-entry-goal.md)推进。阶段1提供CLI预检和同一Workflow执行，
不要求受支持需求另写完整步骤、资源偏序或逐题TaskContract；旧`agent --project`保持兼容。
项目级分组、完整Schema用例执行、工具协议、会话、持久化和工作台尚待后续阶段。

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
使用封闭语法，不宣称任意自然语言语义理解。项目范围当前识别后待审，阶段2负责分组执行。

执行仍走已有DSL/OpenAPI/Policy/Intent/Executor/Verifier；派生TaskContract仅为本地校验快照，
不进入规划上下文或充当独立评测标签。计划遗漏、额外操作、错角色/来源/偏序在HTTP前拒绝。
未知业务预期不会生成可信断言；配置通过、规划通过与业务断言通过分别判断。

预算继续受目标请求数与步骤数限制，单run默认最多4模型/2只读调查；
阶段1仅启用一次规划，不隐式重试。真实模型验证尚未执行，全部开发材料为DEVELOPMENT_ONLY/UNREVIEWED。
