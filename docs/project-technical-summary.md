# AgentTest：实习面试技术说明

## 定位与实际贡献

这是独立的 Python AI API 测试项目。解决的具体问题是：模型可以提出测试步骤，但不能同时决定服务地址、身份、业务预期和是否通过。项目把规划与授权分离，使用确定性程序执行经过批准的测试。

CourseInsight 是被测 Java/Spring Boot 服务。AgentTest 阅读实际 Controller、DTO 和 Security 配置建立适配，在隔离 demo 完成真实调用；没有修改 Java 业务代码。描述简历时应区分“Java 服务测试集成”与“实现 Java 业务功能”，不能把 AgentTest 的 Python 模块说成 Java 实现。

## 一次请求如何完成

1. 操作员加载 TargetConfig、契约、Policy Registry、账号环境及预算。它们不属于模型输出。
2. Workflow 过滤 OpenAPI 操作、参数 Schema、认证要求和批准策略目录，只向 Planner 提供公共上下文和有界中文需求。
3. ChatECNU Planner 发送一次受约束的结构化 Chat Completions 请求。返回结果仍是不可信 DSL；不存在“结构化 JSON 就安全”的假设。
4. Pydantic、OpenAPI、策略适用范围、资源来源、认证先后顺序及确定性意图检查全部通过后，才进入执行器。未知意图或预期 NEEDS_REVIEW，不能编造成功。
5. Executor 通过 Vault 登录并保管 Token；动态资源 ID 只可从批准来源绑定到后续请求。发送前记录唯一 attempt ID、检查请求预算和隔离授权。
6. Verifier 对照可信状态码、字段及来源关系做断言；报告输出摘要和固定诊断，不保存原始正文、模型输出或认证。
7. 只有操作员启用调查、原业务断言失败且有同 run 的可信资源时，FakeAnalyzer 才能选择批准只读能力。调查复用原会话和执行器，原失败不可改写。

完整架构和请求流程图见 [README](../README.md)。

## 技术难点与取舍

| 问题 | 实现 | 明确取舍 |
| --- | --- | --- |
| 模型伪造 reviewed/source | DSL 只引用策略 ID，独立 Registry 匹配接口/角色/场景/绑定 | 配置文件权限与人工审阅是信任根，尚无多租户审批平台 |
| 计划能执行却不符合任务 | 封闭中文意图检查，名称/创建者/关系必须匹配 | 保守拒绝开放式需求，不能宣称通用语言推理 |
| JWT 被模型作为业务参数引用 | 认证由 Vault 管理；认证响应禁止普通步骤绑定，已知秘密和敏感字段清理 | bearer / 批准登录流程为主，不支持任意认证插件 |
| Java 返回封装与 mini 不同 | token `$.data.accessToken`、创建 ID `$.data`、详情 `$.data.id`、code=0 | 可信适配配置，不写第二套 HTTP 引擎，不照搬 mini 的 403 |
| 唯一代码与动态 ID | 操作员生成 AT4A 代码锁定输入；查询必须引用创建 ID | 不通过更换 ID、改字段、重试创建掩盖失败 |
| Java 字符串校验 | 明确支持 UTF-16 长度及非空的受控子集 | 不声称实现全部 Bean Validation / JSON Schema |
| 调查需要认证生命周期 | 同 run 受限内存会话；结束后清除 Vault/响应状态 | 不持久化 JWT，不恢复未完成 HTTP，不重新登录或创建 |
| GET 可能有副作用 | 操作员 ReadOnlyCapability Registry 记录安全依据 | 会推进状态的 getBatch 不批准，方法名不能证明安全 |
| 请求可能已写入但响应丢失 | 请求前审计、写入不自动重试、MCP/Java invocation claim 拒绝重放 | 不承诺 exactly-once；网络异常后实际服务器状态仍需人工核实 |
| 模型输出错误/工具错误混淆 | 固定 JSON/MODEL/DSL/OPENAPI/POLICY/DEPENDENCY/INTENT 诊断 | 不保存 Pydantic 输入或原异常文本，部分细节有意不留存 |
| MCP 客户端越权 | 五个工具、严格参数 Schema、固定场景、独立预算、报告命名空间 | stdio 本地连接；不开放 Java 写入、任意 URL 或任意计划执行 |

LangGraph 只负责节点顺序和条件分支；不重复执行器，也没有引入自动修复循环。模型服务使用 HTTPX 兼容协议，按学校官方参数处理 json_schema/max_tokens，避免依赖不必要的 SDK。MCP 使用官方 SDK 2.3.0 的 MCPServer，独立 stdio 入口，不默认启动 HTTP Server。

## 真实证据与能说的指标

- 原 2A 人工审核的 18 条任务：首轮端到端 13/18，第二轮 16/18；旧结果和失败保留，后来不再反复优化此集合。它们只覆盖两个意图的措辞/角色/数据变体。
- 4A 真实学校模型 + 隔离 Java：一次登录/创建/查询，200/201/200，动态 ID 和 code/name/teacherName 一致，14 项断言通过。模型一次 1,794 Token / 21,891 ms；HTTP 分别 375.224 / 91.208 / 29.235 ms。
- 4B 新 B01–B04：两条支持任务中真实模型只有 1/2 有效计划和端到端成功。B02 认证顺序错误在发送前拒绝；两条非法意图也零请求拦截。样本为开发者构造，未冒称逐条人工评测批准。
- 真实计划已执行三类缺陷全部检出，误报 0/3，执行漏报 0/3；第四类归属缺陷因规划失败没有执行。总体覆盖 **3/4**，端到端未检出 **1/4（规划失败）**，不能只说 100% 检出。
- 人工固定计划对六类机制检出 6/6、误报 0/6、漏报 0/6，包含异步终态和结果条数。固定 FakeLLM 是链路基线；人工异步数字不属于真实模型规划成绩。
- 本轮共五次学校调用，7,290 Token。4B 平均模型耗时 4,816.5 ms；这是少量实际调用耗时，未做并发/压力测试，不能换成吞吐量、提效百分比或生产 SLA。

数据出处：[4A](stage4a-results.json)、[4B](stage4b-results.json)、[指标分母与失败说明](stage4-evaluation.md)。ecnu-plus 为学校可变别名，跨日期结果不能直接作模型/代码单因素比较。

## 验证与可复现性

完整依赖冻结在项目 .venv；默认 pytest 禁止意外网络，FakeLLM、MockTransport 和 ASGITransport 覆盖规划、凭据、策略、状态机、预算、报告与故障注入。另有真正 loopback HTTP 和 SDK stdio 子进程验证，避免只测函数调用。Windows/Linux × base/agent 四组 CI 执行 pytest、Ruff、pip check 与演示，真实 Java/学校 API 不进入 CI。

执行入口：`python -m agenttest showcase --case all`。查看本机输出的报告路径；真实调用通过显式脚本与人工批准，不是演示命令的默认副作用。最终测试数和 CI SHA 见 [进度](progress.md)，不把历史 CI 当本轮结果。

## 简历表述建议

可以写：

- 独立实现受控 API 测试 Agent，基于 LangGraph 编排结构化 LLM 规划、DSL/OpenAPI 校验、可信策略和确定性 HTTP 执行；隔离模型的目标、凭据和断言授权。
- 对接 Spring Boot CourseInsight demo，完成中文需求到学校 ecnu-plus 规划、动态资源绑定、登录/创建/查询及 14 项一致性断言的真实闭环；提供脱敏审计报告。
- 实现受限只读失败调查和五项 MCP stdio 工具；以六类配对缺陷验证执行器，保留真实模型认证顺序失败及 Windows/Linux 四组离线 CI。

不要写“生产使用”“覆盖全部接口”“LLM 缺陷检出 100%”“自动修复”“微服务平台”或没有测量的效率提升。项目已适合用于 Java 后端/AI Agent 实习简历，但候选人仍需亲自运行演示并能解释信任边界与真实失败。

## 未实现与下一步

没有 RAG、多 Agent、自动修复、前端工程、新数据库或 Kubernetes。真实 Java 只验收 admin 三步，不做跨用户权限推断；模型尚不能规划 CSV/异步流程；调查仅 FakeAnalyzer。没有跨进程事务、断点续传、生产权限系统、独立 wheel 资源打包、自动生成 pytest 脚本或大样本泛化证据。

后续应先审查新的业务目标和只读能力，构造未使用过的任务，再决定是否改进规划。扩大模型能力或 Java 写入范围需要独立授权和新的评测，当前不继续堆功能。
