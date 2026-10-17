# 陌生项目完整业务验收：PocketBase（2026-10-17）

本轮是用户批准启动的新里程碑，目标是补齐第三方项目的认证、数据修改和权限流程证据。复用原 Generic Planner、TaskContract、Workflow、Executor、Verifier 和报告存储；不修改通用提示、原有任务、断言规则或 CourseInsight。

## 目标与来源

- 被测程序为官方 PocketBase **v0.40.5**，提交 `6f370ea741f33adf889eef02e3c1b884a47aa522`，2026-10-08 发布。上游是 Go／SQLite，不是本项目新写的业务模拟服务。
- Windows x64 官方发布包 SHA-256：`d0980d1f3a8097ff4c03a876e8c98f3008a83c0d42cc03b636bc92b2d4788d1c`；已下载到私人 `runs/pocketbase-preparation-20261017/` 并核对。程序尚未启动时，不能把下载／校验计为真实集成通过。
- 解包得到的 `pocketbase.exe` 为 33,470,976 字节，SHA-256 `a6d3fb3555b64655e22c797aab7f7c396e9a2c48d7de89135d868b1758734860`。运行入口同时核对发布包与程序，不执行未知可执行文件，不安装新的 Python 依赖。
- [接口子集](../examples/portability/pocketbase/openapi.json)由开发者按官方记录／认证接口和本轮集合配置整理，**不是上游原生 OpenAPI 导出**，不表示支持 PocketBase 所有功能。
- [固定迁移](../examples/portability/pocketbase/pb_migrations/1792195200_acceptance.js)只创建两个一次性账号、笔记集合、字段和原生访问规则；无自定义路由、请求处理器或业务替代代码。测试效果属于此明确配置的第三方实例，不扩大为任意项目保证。

来源：[上游发布](https://github.com/pocketbase/pocketbase/releases/tag/v0.40.5)、[记录接口](https://pocketbase.io/docs/api-records/)、[访问规则](https://pocketbase.io/docs/api-rules-and-filters/)、[迁移](https://pocketbase.io/docs/js-migrations/)、[固定版本认证中间件](https://github.com/pocketbase/pocketbase/blob/v0.40.5/apis/middlewares.go)、[记录处理器](https://github.com/pocketbase/pocketbase/blob/v0.40.5/apis/record_crud.go)。上游明确支持带 Bearer 前缀的认证；不为适配目标改变现有凭据处理。

## 六条新任务与分母

| 任务 | 业务链路 | 固定预期 |
| --- | --- | --- |
| PB901 | owner 登录→创建→使用实际 id 读取 | 标题、state、owner 与创建输入一致 |
| PB902 | owner 登录→创建→修改→使用修改响应 id 回读 | 新标题与 done 状态已保存 |
| PB903 | 两个账号登录→owner 创建→other 读取→owner 读取 | other 返回 404，owner 仍可读 |
| PB904 | 两个账号登录→owner 创建→other 尝试修改→owner 回读 | other 返回 404，原文及 draft 状态未变 |
| PB905 | owner 登录→创建→删除→同一 id 再次读取 | 删除返回 204，再读返回 404 |
| PB906 | owner 登录→将归属误填为 other 的已知夹具账号→按精确标题查询 | 创建返回 400，owner 可见结果数为 0 |

本项目支持的请求 Schema 会提前拒绝非法字段类型／枚举，不能为了请求到达服务器而删除这些保护。PB906 选择 Schema 允许表达、但违反原生创建访问规则的归属值，不假装完成所有异常参数测试。其后列表只证明 owner 不可见该标题记录；不能单凭列表为空断言数据库绝无隐藏记录。

账号 ID 是迁移中固定的公开合成夹具值，不是模型猜测或从生产获取。新笔记 ID 均从创建／修改响应传递，禁止写死。登录响应由原执行器隔离，不把其中的 token 或账号资料交给模型引用。账号和密码仅经环境变量进入进程，密码随机生成，不写入正式配置。

先运行六条开发者手写固定计划，**六条全部完成才进入真实模型阶段**。再进行三轮，每题每轮一次，共 **18 个预排规划位置**，按轮次轮转题目顺序。固定基线与模型结果分开统计，共保留 **24 个位置**；模型失败、拒绝和未执行均保留，不增加自动重试，不重用某题的成功计划填其他位置。

任务与预期是本轮开发者编写并固定，仍为 `DEVELOPMENT_ONLY / UNREVIEWED`，`human_reviewed=0`、`formal_eval_v2_score=null`。这不是独立盲测，也不并入旧 Golden／Eval v2／Petclinic 成绩。未做第三方源码故障注入，不报告缺陷检出率或误报率。

## 隔离写入范围与预算

操作员已在本会话明确批准这个**新的隔离环境**；按以下范围启动并执行，原模型额度自主批准授权继续有效。

- 只监听 `http://127.0.0.1:18095`；启动前端口必须空闲，不复用、接管或终止已有服务。不提供目标地址参数，业务请求仍走原 SSRF／重定向保护。
- 每次仅在新的 `runs/<本轮目录>/service/data/` 初始化 SQLite；加载固定迁移，建立两个账号和笔记集合。即使只运行固定基线，启动迁移也会写数据库。
- 每条工作流最多 8 个步骤、8 次 HTTP 请求，无写入重放。正常完整四轮（固定基线＋三轮模型）预期 96 次业务 HTTP 请求，最多 192 次；健康探测单列计数，不冒充业务测试。
- 正常完整执行预期有 20 次笔记创建成功、4 次修改成功、4 次删除成功；其他归属创建被拒绝。此处是计划范围，不是已观察数据，也不等于 SQLite 底层写入次数。
- 模型沿用当前 `ecnu-plus` 与既有提供方配置，最多 18 次，每次最坏预留 24,096 Token，总预留 **433,728 Token**。这是上限，不是实际用量；实际用量未知时保留未知并停止后续调用，已预留位置不退款重试。
- 模型输入仅含同一份通用提示、任务、受支持接口／字段和完整批准策略目录；不包含参考计划、TaskContract 的评分细目、业务账号密码、目标地址、数据库文件或测试故障开关。
- 基线失败、服务退出、源码变化、执行基础设施故障或模型用量未知时停止。未完成位置仍写入记录。中断中的调用／请求保留 `ATTEMPT_OUTCOME_UNKNOWN`，不记作零调用、零副作用或已通过。
- 本轮关闭 PocketBase 自有 API 日志，原生控制台输出不保存，避免首次启动链接中的安装 token 泄漏。AgentTest 继续保存脱敏报告、计划证据、模型用量和来源摘要。
- 无 Java、H2、CourseInsight、Docker、邮件、远程业务服务或 MCP 写入。结束仅回收本次启动的程序；保留专用 SQLite 和脱敏证据供复核，不删除用户目录或其他服务的数据。

现有模型额度自主批准授权继续有效；本轮单独确认的是新第三方 SQLite 实例及其测试写入范围。依据 [AGENTS.md](../AGENTS.md) 第 6 条：`Execute mutating tests only in explicitly authorized isolated environments.`

## 可复现入口

以下入口始终离线，只运行合成响应和固定规划回复，**不启动 PocketBase、不访问 SQLite、不调用真实模型**：

```powershell
.\.venv\Scripts\python.exe scripts/check_pocketbase.py --output runs/pocketbase-offline-new
.\.venv\Scripts\python.exe scripts/check_pocketbase.py --verify runs/pocketbase-offline-new
```

六份 `.project.json` 可分别使用已有 `check-project` 预检；账号环境缺失时应报告缺口。其 `allow_mutations=true` 是隔离执行配置，不等于对新的真实实例授予操作员许可。

真实入口已准备，但只在本轮隔离写入获准后使用；要求工作区干净、代码已提交，输出目录从未存在。运行器只接受上述官方 Windows 发布包，密码只在内存／子进程环境，服务不继承模型 Key。

```powershell
.\.venv\Scripts\python.exe scripts/run_pocketbase_live.py `
  --archive runs/pocketbase-preparation-20261017/pocketbase_0.40.5_windows_amd64.zip `
  --output runs/pocketbase-live-new --isolated-approved
```

真实运行完成后，仍使用 `check_pocketbase.py --verify <真实目录>` 冷核对全部 24 个位置，不再调用模型／业务服务。SQLite 和原生程序不作为公开产物；公开结果只能根据脱敏报告汇总，不从“程序启动成功”推导全部验收通过。

## 当前状态

已准备固定输入、原生迁移、离线检查及受控真实运行入口，官方发布包与代码已固定。首次真实运行位于私人 `runs/pocketbase-live-20261017/`，对应 `68e2cac`。

首轮完整检查发现将排他创建的证据写入器误用于同名检查点，结果 **1 failed／19 passed**。检查点改为追加文件，每个位置单独保留，不放宽原有禁止覆盖保护。修正后专项 **20 passed／80.57s**，其中六条固定基线与十八个模拟规划位置均完成；包含越权放行检出、凭据隔离、错误资源／顺序拒绝、未知调用记账、程序回收失败和重封印错误成绩拒绝。仍只是合成协议与固定回复证据。

扩大回归 **102 passed、1 skipped、1 deselected／29.46s**，覆盖本轮边界和原项目预检、CLI、DSL 安全及模型适配器；跳过项已核对为 POSIX 链接语义，完整归档用例前一轮已单独执行，本轮由最终 CLI 验收覆盖。最终入口 `runs/pocketbase-offline-final-20261017/` 的六条基线与十八个模拟规划位置全部完成，共 96 次合成业务请求；24 个位置冷核对通过。2,700 Token 是模拟值，真实调用为 0。

Ruff 全仓、pip check、两份文档本地链接检查及 25 份新增发布文件的模型密钥扫描通过。准备阶段没有真实模型或数据库请求，未重跑本机全仓／Web；这些离线结果不计为真实第三方或独立泛化成绩。

### 首次真实运行与停止条件修正

六条真实固定基线 **6/6 完成**，原生实例创建、修改、读取、权限规则与删除链路均按批准断言执行。随后实际三次 ecnu-plus 调用：PB901／PB902 完成，PB903 因生成 `body.body` 而非平铺的 title／state／owner，被原 OpenAPI 门禁以 `OPENAPI_UNKNOWN_BODY_FIELD` 拒绝，**该题业务 HTTP 为 0**。三次调用均已知用量，合计 **11,478 Token**；原始失败、其余 15 个未尝试位置和两个成功结果已封印，24 位置冷核对通过，原生进程退出已确认。

原运行器把所有 TOOL_ERROR 都归为执行基础设施问题，导致这种执行前的计划拒绝也停止原排期。修正仅针对验收驱动：`PLAN_REJECTED`、dsl_valid=false、步骤／执行工具调用均为 0 时保留失败并继续下一预排位置；实际执行／基础设施故障、用量未知或进程回收未确认仍停止。专项 **10 passed／2.37s**，扩大 **73 passed、1 skipped、1 deselected／17.70s**，跳过为 POSIX 语义。原归档再次冷核对，全部规划输入摘要未变。未修改提示、接口字段、任务、策略、预期或原 Workflow／Executor 的分类。

首次原实例已关闭、原证据不改。按同一已批准隔离范围，用新的目录及新账号执行六条固定基线；仅在通过后完成原排期中**尚未尝试的 15 个模型位置**。前三次报告原样继承，不再次调用，也不把失败补成成功。新归档嵌入旧归档的 JSON 证据、清单摘要和原报告，冷核对逐项比对；原 SQLite 和程序不复制、不恢复旧写请求。这是本次验收驱动的范围限定，不提供业务请求重放。

两次运行合计仍最多 **18 次模型调用／433,728 Token 预留**、最多 **192 次业务 HTTP**。新实例固定基线增加 24 次预期请求，若其余计划全都通过，预计两实例合计 115 次业务请求；这仍是计划值，最终以证据为准。实际账号／数据库写入仍只在已批准的本机新目录，结束回收自有程序。不能将这次已见题目的排期补完称为新的独立评测。

```powershell
.\.venv\Scripts\python.exe scripts/run_pocketbase_live.py `
  --archive runs/pocketbase-preparation-20261017/pocketbase_0.40.5_windows_amd64.zip `
  --output runs/pocketbase-live-remaining-new --isolated-approved `
  --prior-archive runs/pocketbase-live-20261017
```

### 最终真实结果与接入成本

`06a0164` 完成剩余十五次新调用，继承前三次原报告，合计 **18 次真实调用、16/18 完成、0 重试、0 未尝试**。原 CLI 以退出码 1 表示未全部通过，不是基础设施中断。第二轮六题全部通过；PB903 第一、三轮同为 `OPENAPI_UNKNOWN_BODY_FIELD`，第三轮失败同样业务请求为 0，原失败没有被修成通过。

| 任务 | 三轮完成数 |
| --- | --- |
| PB901 创建／读取 | 3/3 |
| PB902 修改／回读 | 3/3 |
| PB903 跨账号读取拒绝 | 1/3 |
| PB904 跨账号修改拒绝与原文保护 | 3/3 |
| PB905 删除／再次读取 | 3/3 |
| PB906 错误归属创建拒绝 | 3/3 |

十八次模型 HTTP 均为 200；总输入 **59,895**、输出 **8,905**、合计 **68,800 Token**，未知用量 0。有效 DSL／策略授权／意图验证／任务完成均为 16，工作流结束为 18，不能互相替代。模型工作流耗时中位数 13,937.5ms，最短 8,157ms、最长 21,265ms，仅描述这十八个位置。

两个真实实例的固定六题均全部完成。合计 **110 次业务 HTTP**（两次固定基线 48＋模型工作流 62），健康探测另 3 次，均在原额度内。两份归档分别冷核对全部 24 个位置；最终归档逐字节保留原前三次报告并核对父归档摘要。两个原生进程退出均已确认，测试端口已释放，未新增 Java 请求。

结束后以 SQLite `mode=ro` 和 `query_only` 聚合核对，两个实例分别有 2／2 个夹具账号、6／12 条剩余笔记；无 other 归属笔记、越权改写标题、删除后标题或错误归属标题残留。data.db 与 WAL 的读取前后 SHA-256 均相同。这是额外的最终状态核对，不把数据库内容或密码哈希上传模型、发布到仓库。

接入实际需要 **18 份操作员输入／夹具、1,950 行**，包含 6 个接口操作、11 个策略、6 份任务约束及 6 个项目入口；新增 Python 运行依赖为 0。没有准确的人工作业计时，不报告接入分钟数。当前能够迁移到此明确配置的第三方 JSON／Bearer 业务，接入成本仍高；不能承诺导入任意 OpenAPI 即自动理解业务。

[公开脱敏统计](demo-evidence/pocketbase-live-summary.json)保留来源提交、四层证据、失败、用量和只读数量核对，不包含原始模型响应、账号密码或业务正文。完整报告、两个 SQLite 和原生程序仍在私人 runs 下。仍为未独立审核的开发样本，十八次重复不是十八条独立新题，不报告任意项目泛化成功率。

下一准确里程碑：从接口 Schema 收紧模型可选的参数名，减少 body.body 这类格式错误；必须用新的开发任务验收，保留本轮 PB903 的 1/3，不用旧题调提示。后续再复用候选发现减少人工接入配置。
