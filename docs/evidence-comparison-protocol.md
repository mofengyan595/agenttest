# 证据可靠性与模型价值：执行前固定协议

2026-10-18，DEVELOPMENT_ONLY / UNREVIEWED；独立人审0，formal_eval_v2_score=null。
本轮由用户新设增量里程碑授权。历史PV/FC/FE任务、两段真实排期、原失败和全部旧分数保持。
新任务ED01–ED06开发、EV01–EV12验证、EE01–EE06自然语言确认均进入开发重合排除表。
三轮是同12题的重复位置，不能当作36个独立语义样本，也不主张统计显著或通用泛化。

## 三组与隔离

- A：冻结前 `FeedbackModel` 的原提示、原Schema、原观测摘要，反馈决策由同一已配置模型承担。
- B：`EvidenceFeedbackModel`；增加从已批准绑定和当前实际检查派生的资源/角色/字段关系、
  工具操作与策略检查用途、完整动作组合及双引用约束。关系中没有finding、评分或缺陷标签。
- C：`FixedEvidenceControl`，同一LangGraph反馈入口和Executor执行批准GET；固定规则从相同
  公开观测、关系和批准规则选择调查及结论。没有模型、第二执行引擎或标签读取。

A/B初始计划由相同已验证DSL固定输入，不调用真实规划；每次新建目标、凭据、报告和会话。
目标只在ASGI内存中运行，不访问真实Java/PocketBase/生产业务，不写数据库。
同一任务每组具备相同工具权限、故障条件和预算。B/C得到的关系只是原批准规则的结构化投影，
不包含额外目标返回值或答案。C回调计数是确定性决策次数，不记为真实模型请求。
反馈结论不能改变原ASSERTION_FAILED，仍须通过原确定性支持检查。

## 新任务结构

完整任务和固定计划在 `evidence_tasks.py` 以及每次调用前生成的 `freeze.json` 中保存。
两域包括课程的显式双角色登录、角色对调、由另一角色创建的前置资源与主资源依赖，
工单的owner/visitor列表阶段、关闭后使用既有closed读取策略的阶段，以及瞬时回读和不可用响应。
不是只更改名称或编号。多个题仍复用某些结构，不能声称每题都代表独立的新能力。
TaskContract禁止重复操作/角色/策略签名；工单没有新增重复创建能力，采用已有列表阶段，
课程的两个创建者角色不同。没有放宽Intent、策略或业务断言。

| 验证ID | 类别 | 域及结构/失败条件 |
| --- | --- | --- |
| EV01 | 访问边界 | 课程：另一角色前置资源→主资源→所有者读取→拒绝读取，归属泄露 |
| EV02 | 访问边界 | 课程：对调创建者及受限角色，并创建前置资源，归属泄露 |
| EV03 | 访问边界 | 工单：创建→关闭→closed读取→拒绝读取，归属泄露 |
| EV04 | 访问边界 | 工单：双角色列表前缀→创建→所有者及受限读取，归属泄露 |
| EV05 | 重复回读 | 课程：双登录及另一角色前置资源→主资源名称回读错误 |
| EV06 | 重复回读 | 课程：观察者登录→创建→ID回读错误 |
| EV07 | 重复回读 | 工单：创建→关闭→closed读取，主题持续错误 |
| EV08 | 重复回读 | 工单：双列表阶段→创建→主题持续错误 |
| EV09 | 证据不足 | 课程：双资源流程的首个创建状态错误；无已验证依赖供GET调查 |
| EV10 | 证据不足 | 课程：双角色登录→创建→GET 503；不能按字段失败证明症状 |
| EV11 | 证据不足 | 工单：列表→创建→关闭未生效；PATCH失败与允许GET操作不同 |
| EV12 | 证据不足 | 工单：visitor列表→创建→首次主题错误，后续读取正常 |

批准菜单保留所有者/受限角色读取；有权限不代表有助于结论。只允许失败步骤的已验证资源依赖，
前置资源不得被任意调查。无GET能支持规则时主动停止是正确行为，不要求耗尽两次请求。

## 固定顺序、预算与冻结

开发6题每组一轮（18位置）；验证12题每组3轮（108位置）；自然语言确认6题B组每题一次。
均串行。每轮按题号，每题按轮数加题序轮换A/B/C顺序，以免所有A都在所有B之前。
开发与验证每run至多3次反馈模型/2次GET；含初始规划时总计至多4次。
开发最多36次模型、验证最多216次、E2E最多24次；Token预留按冻结的单请求配置上限乘次数，
不以额度充足扩题。模型配置相同，timeout=60s，反馈180s，Workflow300s；无隐式重试。

每次排期先保存提交/源码哈希、任务、计划、能力/策略摘要、提示和Schema源版本、模型、
评分源版本、完整顺序和预算。真实调用要求干净已提交代码及同split的完整Mock验收。
开发真实排期完成后选择候选；本轮计划只检验这一候选，不无限调参。
验证阶段必须匹配开发候选提示、Schema源、模型与评分版本；自然语言阶段同样绑定验证候选。
每次使用新输出目录，旧位置不能替换、补跑。已知HTTP200结构错误保留当前失败并继续下个
独立位置；未知用量、传输、工具、超时等外部失败停止后续付费位置，未执行仍计完整分母。
C是离线控制，不因模型外部阻塞而停止。

## 评分、成本与可复核性

主指标 `evidence_strict_v1`：指定业务检查必须实际失败；支持结论需符合相应症状、
原与调查引用完整并通过确定性支持检查。证据不足需明确finish和
FEEDBACK_EVIDENCE_INSUFFICIENT，不能把引擎拒绝猜测当作模型主动正确弃权。
额外报告有效证据、已有证据错误弃权、不当断言、直接相关调查选择、原业务判定和预算安全。
直接相关选择定义为同操作同来源资源，访问边界为生产者角色，字段回读为原角色；
异操作GET即使安全执行也不算直接支持当前规则的调查。证据不足题可以零调查正确结束。
不支持的症状猜测与错误引用可由候选的脱敏proposal区分；A中结构拒绝未保存字段时继续未知。

每组每类给出完整分母、实际位置、模型请求、实际输入/输出/总Token、工具和业务请求、
每run耗时累加与未知用量。Mock Token明确SIMULATED；credits、货币和人工时间未测即null，
不换算、不代填。安全统计是本轮范围内的观测，不代表任意攻击下的安全证明。

归档包括冻结、逐位置行、脱敏报告、结果、摘要和封印。冷复核只读取封印和报告，不启动目标/模型。
正式独立评测仍需真实独立人审，不能把这批开发验证转成正式留出集。

## 自然语言确认

EE01–EE06为新目标文本；覆盖课程/工单健康流程、课程访问失败、关闭后工单字段失败、
课程不可用和工单瞬时回读。先固定计划与Mock核对，再在冻结候选上每题规划一次。
规划拒绝或错误照实保留，不用固定计划替换。单独报告，与反馈子系统分母不合并。

## 命令

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_evidence.py --split development --output runs/<new-mock>
.\.venv\Scripts\python.exe scripts/evaluate_evidence.py --split development --output runs/<new-real> --model-env .env --mock-acceptance runs/<new-mock>
.\.venv\Scripts\python.exe scripts/evaluate_evidence.py --split validation --output runs/<new-validation-real> --model-env .env --mock-acceptance runs/<validation-mock> --prior-phase runs/<development-real>
.\.venv\Scripts\python.exe scripts/evaluate_evidence.py --split end_to_end --output runs/<new-e2e-real> --model-env .env --mock-acceptance runs/<e2e-mock> --prior-phase runs/<validation-real>
.\.venv\Scripts\python.exe scripts/evaluate_evidence.py --verify runs/<archive>
```

沿用锁定LangGraph API，已复核[官方Graph说明](https://docs.langchain.com/oss/python/langgraph/graph-api)。
没有新增依赖、MCP API或外部观测平台。
