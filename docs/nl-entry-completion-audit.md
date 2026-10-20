# 自然语言入口 Goal：逐项验收依据

本清单按原始[nl-entry-goal](nl-entry-goal.md)核对工程交付与质量结果。代理整理证据，不代签独立人审。

| 原始要求 | 直接证据与验证范围 |
|---|---|
| 先查真实状态，保留旧模块/成绩 | 起点b09cbcb、CODEX_TASK/README/规则/配置/归档检查；旧两段72位置封印重新冷核对，零新模型 |
| 项目配置复用，凭据不进模型 | project_goals、planner_input、CredentialVault；两域6份配置、角色/操作/策略/只读能力预检 |
| 支持原文入口，无逐题完整步骤 | goal_derivation在规划前独立派生；新12只提供project+user_task，模型输入禁止labels/rubric/reference_plan |
| 单接口Schema正常/负例/边界 | goal_suite复用发现器；正常/枚举/合法边界执行，缺少批准错误语义的候选待审并保留分母 |
| 多接口认证/ID/角色/状态/顺序 | 原Intent/DSL/Policy门禁；旧开发反例与新12中的不同流程拓扑，未静默补步或重排 |
| 项目分组与固定总预算 | 预览固定分配、共享请求/model ledger；工单六组11次需求受原10次授权限制，不加额或删分母 |
| 业务、模型与规则分层 | 原报告、反馈提案与trusted_symptom分别保存；隔离评分按实际检查与资源/角色关系冷重算 |
| 工具标准化与JSON兼容 | tool_registry、MCP v2与NativeFunctionPlanner；统一参数/权限/资源/结果元数据，原引擎复用 |
| 原生Function Calling | strict/forced single tool/parallel false Mock通过；未声明或chatecnu零请求不支持；没有供应商实测声明 |
| 多轮补充与版本不可变 | GoalConversation/TaskService；8轮/2000字符、有界原文、澄清历史、证据引用与共享剩余额度；配置变化重验 |
| CLI先闭环，再开放受控任务服务 | 先CLI四步原文→执行→导出，再SQLite/API/任务页；公共逻辑与旧入口扩大回归 |
| SQLite元数据与实际进度 | task_store/task_service/task_api；真实SQLite/ASGI、SSE/轮询、元数据不含凭据/原业务正文/模型原文 |
| 并发、重复与run隔离 | 默认串行、8排队/运行、幂等提交/原子状态版本；账号、资源、请求预算、报告和导出权限隔离 |
| 取消、重启和配置漂移 | 已发请求计数与未知状态保留；独占锁后中断未完成任务，不恢复HTTP；每次规划/反馈/派发前核对配置 |
| 本地同源/会话保护 | 服务端项目注册/角色/预算门禁；CSRF、HttpOnly/SameSite、同源/回环、流式请求大小限制；浏览器不可传URL/凭据/策略 |
| 现有控制台关键交互与响应式 | 31项前端测试/typecheck/build、4项Playwright含下载/历史刷新/详情、1440和390px截图实查；Fake/ASGI |
| 新12需求/三轮/隔离评分 | 6单/4流程/2项目、两域、DEVELOPMENT_ONLY/UNREVIEWED；原36位置与所有未执行项保留 |
| 实际模型与成本 | 原排期固定源码/模型/预算/评分；逐调用已知/未知、Token与耗时，不用模拟或规则结论替代模型证据 |
| 首次新需求与无模型回归 | 首次尝试仅原文+项目配置，未Fake调参；完整通过后原固定pytest导出，模型/真实HTTP陷阱回归 |
| 可复制演示、支持范围、失败与简历 | delivery/value-report/resume-interview材料与脱敏JSON；保留首次接入人工配置要求和负面质量结果 |
| 工程门禁与交付 | 各阶段pytest/Ruff、前端门禁、准确SHA六项CI；最终统一9项离线验收、文件链接/摘要/敏感信息与Git范围检查 |
| 不越界 | 无RAG/多Agent/第二框架、CourseInsight改动、真实Java/PocketBase/生产写入、模型代码执行、自动修复/恢复/写重放 |

## 已知工程失败与修正

- StrictMode并发会话初始化使Cookie/CSRF不匹配：合并pending session Promise，保留同源与CSRF保护；
  Windows/Linux网页CI与实际浏览器提交通过，不用延长超时掩盖。
- 历史固定回归目录增加NU01后，原三故障检查仍收集全目录：明确限定原CASE_IDS三组，NU01独立保留；
  新目录统一验收9/9，不改原断言和历史分母。
- 回归子pytest在CI目录外未加载上层Runtime fixture：完整日志确认fixture not found；显式cwd/rootdir/
  confcutdir及不跟随链接的报告收集后，两平台安装环境检查通过；没有放宽原导出验证。

原真实排期36/36、72次/173894 Token、完整计划23/36、完整任务15/36；首次新需求1次/3358 Token通过，
固定pytest模型禁用回归通过。追加尝试与原分母分开，见[实际价值](nl-entry-value-report.md)。
统一离线九项验收9/9 VERIFIED，source_unchanged=true、cleanup_unconfirmed=false；
[直接结果](demo-evidence/nl-entry-offline-delivery.json)。源码冻结bfa36e5的六项CI均success；
交付版本准确HEAD六项CI以本提交Actions及私有runs/ne-entry-final-ci.json核对记录为准。
正式独立人工评测与人工节时没有完成，本轮仅交付真实待审材料，不代签独立人审。

交付资产与四份既有固定回归共同运行 **5 passed / 6.26s**；Ruff全仓通过，git diff --check通过。
34份改动文件的JSON/本地链接/占位检查通过，已知本机密钥值扫描0命中；原四份导出文件与fixture
逐字节一致。原文及六份工单配置JSON语义一致，配套文件按仓库规范保存为LF换行。
逐位置候选与覆盖另存[覆盖记录](demo-evidence/nl-entry-coverage.json)。
配套JSON换行规范化后，新资产独立复核 **1 passed / 1.93s**，原导出与暂存Git对象的字节一致。
