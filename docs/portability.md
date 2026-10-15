# 跨项目可迁移性与验收

当前可确认：契约解析、HTTP 执行、凭据隔离、策略断言、依赖绑定、预算及报告不依赖 CourseInsight。过去的自然语言提示与意图识别主要围绕课程，不能把它们描述为导入任意项目即可自动测试。本阶段新增配置式任务约束和通用 JSON 规划提示，并验证课程、工单两个不同业务域。

默认验收使用固定响应。后续明确授权的[真实模型补验](real-model-coverage-20261014.md)通过同一通用提示完成四条正常任务：工单读取、权限、关闭复查及课程权限。工单是仓库内新建、状态独立的被测服务。随后[陌生 Petclinic 实测](petclinic-readonly-20261015.md)通过两条真实模型只读任务，覆盖同一个 GET 的正常和 404；使用人工限定契约，不与前四题合并成泛化成功率。

## 换项目需要准备什么

普通 JSON REST 项目提供四项操作员配置：

| 文件 | 用途 |
| --- | --- |
| OpenAPI | 操作、请求字段、认证声明；不授予执行权限 |
| TargetConfig | 隔离目标、允许操作、角色凭据的环境变量名、请求预算 |
| PolicyRegistry | 已确认的状态／字段断言、角色和参数来源规则 |
| TaskContract | 与原始需求绑定的目标、必要偏序、参数值与引用关系 |

后三项均是可信配置，不能从模型输出加载。TaskContract 绑定契约、目标和策略摘要；任务文本或输入版本变化后不能继续使用。它复用既有 Rubric 和意图评分代码，检查模型计划是否多做、漏做、换角色或改参数，没有新增执行器。

目标约束无需填写模型生成的步骤 ID 或唯一完整顺序；允许重命名步骤、交错无依赖操作。它仍需人工把业务需求转成明确约束，**尚不支持自动理解任意业务规则**。配置文件的可信来源和人工业务确认仍由操作员负责，字段本身不代表独立审核资格。

现有 `agent` 命令新增 `--task-contract <文件>`：执行前核对输入摘要与任务原文，真实模型选择 `generic` 提示，Fake 样本也使用同一确定性目标检查。原 mini 场景的内置意图门禁继续执行，新增约束只能进一步限制它。示例见 [课程任务约束](../examples/portability/courses/ownership.task.json)和[工单任务约束](../examples/portability/tickets/close.task.json)。示例 `.draft.json` 是模型结构化回复夹具；`agent --fake-plan` 仍要求完整 Scenario JSON，两者不能混用。

通用提示不包含课程或工单的完整步骤模板。模型看到所选项目的完整批准操作／策略目录及任务文本，不接收 TaskContract 的 requirements、标准计划或缺陷开关。模型仅能选择步骤，不能提供断言、目标地址、凭据、HTTP Header 或执行授权。

## 本轮对比

| 变化 | 课程目标 | 工单目标 |
| --- | --- | --- |
| 操作与地址 | login、createCourse、getCourse；`/api/courses` | openTicket、readTicket、closeTicket、listTickets；`/v2/tickets` |
| 身份 | 用户名／密码登录，运行时取得令牌 | 预置 Bearer，由执行器从环境读取；没有登录接口 |
| 资源 ID | 整数 courseId | 字符串 ticket_key |
| 响应结构 | 顶层 `$.courseId` | 嵌套 `$.payload.ticket_key` |
| 业务链 | 创建、读取、跨用户拒绝 | 创建、读取、跨用户拒绝、PATCH 关闭及状态复查 |

正常用例包括不同长度的路径与无依赖步骤交错。另有测试同时重命名工单的全部 Operation／Role／Policy ID，保持同一个通用规划适配器及任务校验器，验证它们不依赖硬编码名称。

### 工单演示规则

这只是批准的合成演示 fixture：owner 可新建主题和优先级，在 `payload` 中得到唯一字符串键；本人读取时键、主题、优先级必须与创建请求一致，visitor 读取应为 403。关闭请求只批准 `state=closed`，PATCH 响应及之后读取均须为 closed。列表接口保留在目录内，未要求列表时不能额外执行。

课程规则沿用 mini 的合成断言，复制到新的 business_scenario 命名空间；旧 T／B／C／P 任务、原策略文件、正式 Eval v2 和历史成绩不改。上述跨用户规则不能套用于真实 CourseInsight。

## 可复制离线验收

不读取 `.env`，不调用学校模型或 Java，不安装依赖。使用项目已有 `agent` 环境；Linux 替换为 `.venv/bin/python`。

```powershell
.\.venv\Scripts\python.exe scripts/check_portability.py --output runs/portability-demo
.\.venv\Scripts\python.exe scripts/check_portability.py --verify runs/portability-demo
```

显式启动临时回环服务并经完整 `agent --task-contract` 命令行验收：

```powershell
.\.venv\Scripts\python.exe scripts/smoke_portability_http.py --output runs/portability-http
```

该命令分别验证课程权限链及工单关闭链，使用固定 Fake 计划；服务仅绑定 127.0.0.1 的临时端口，凭据为进程内生成的合成值，结束时关闭服务，不使用真实模型或数据库。

输出目录必须是新的、仓库内忽略的 `runs/` 路径。运行器仅接受 Fake／Mock，无真实调用开关。每种来源十五条：六条正常链、五条故障、四条执行前拒绝；合计三十条。`verified=true` 表示结果符合该用例预期，故障仍保留 ASSERTION_FAILED，拒绝仍保留 NEEDS_REVIEW，不能把三十条都称为业务 PASS。

五条故障分别核对课程名称不一致／越权泄漏，以及工单主题不一致／越权泄漏／关闭无效；必须由相应 HTTP 状态或字段断言检出，其他错误不能冒充检出。四条错误计划包含额外列表、漏掉关闭、错误 Selector 和改主题，均要求零业务请求。

归档包含源码身份、冻结的任务约束、脱敏计划证据和 Workflow 报告、逐项结果及封印。`--verify` 从归档重新核对身份、来源、完整分母和判定，不重新运行目标或模型。哈希用于发现修改，不能防御有系统写权限的人重写整个归档。

Mock 的 Token 数仅为 SIMULATED，Fake 不冒充真实模型调用。本协议保持 `human_reviewed=0`、`formal_eval_v2_score=null`；不合并既有真实模型成绩。

## 能承诺到哪里

- 可配置迁移范围：当前受支持的 OpenAPI JSON REST 子集、登录或预置 Bearer、批准的参数／响应引用、确定性状态／字段断言。通用 PlannerDraft 的字面量目前限字符串、整数、布尔和 null，嵌套对象／数组及浮点字面量尚未支持；底层 Executor 能处理的范围不等于此规划入口全部支持。
- 未覆盖：陌生第三方项目的鉴权／写入／复杂链路、跨项目大样本／多轮真实模型可靠性、真实模型对第二域缺陷的端到端检出、复杂认证、OAuth 刷新／签名、GraphQL／gRPC、通用 CSV／异步规划、自动生成可信业务预期。
- Schema 仍按既有严格子集处理；如 const、oneOf／anyOf、类型联合或未支持格式，需要审查，不能为迁移直接跳过校验。mini 批处理的独立批准路径仍保留。
- TaskContract 复用的 Rubric 要求 Operation／Role／Policy 组合可唯一匹配，尚不支持同签名重复步骤；不能把复杂重复流程默认为已覆盖。

陌生 Petclinic 的固定版本、隔离只读实例及两条首次真实生成已完成，见[配置与边界](petclinic-readonly-20261015.md)。后续准确里程碑：定义新的跨接口业务任务并核对第三方鉴权／资源依赖或写入授权，再进行同预算、保留首次失败的验证；不根据已用两题改提示或预期。仍不承诺任意项目的成功率。
