# C02 / C03 修复后的全新真实 A/B 验收方案（2026-11-07）

状态：方案已准备，**未调用 ecnu-max，未运行真实 A/B，尚未完成目标夹具与评分器的执行前封印**。
本方案不恢复或重跑任何历史付费位置，不改变历史分数。以下原文保留为新验收题，不能用于本轮产品调试。

## 比较对象与执行条件

- 基线：`77effe8db742419c7256509bafbd417562d40f71`，包含已生效的 B12/B10 修复。
- 候选：本轮验证通过后独立开发分支的最终提交；正式执行前填入准确 SHA、完整源码与 Prompt/Schema 摘要。不能只用浮动分支名识别版本。
- 12 条新需求 × 两版 × 两轮 = **48 个首次位置**；每版 24 个。第一轮基线先行，第二轮反转任务与版本顺序。
- 普通 Web 项目注册与任务提交入口；使用相同审批配置、模型参数和只读目标状态。候选 Prompt/Schema 的变化属于产品差异，分别封印，不强求两版相同。
- 真实模型拟采用 `ecnu-max`、temperature=0；正式执行前核对提供方、准确模型标识、超时及报告的用量字段。当前任务未授权本轮付费执行，须另获执行确认。
- 每位置预算拟定：最多 4 次模型调用、8 次目标 HTTP、64,000 报告 Token、180 秒。实际注册还须满足两个版本现有预算门禁，不能通过提高预算补救失败位置。
- 未知用量、传输基础设施故障、目标状态漂移或源码漂移停止后续位置。首次失败、超时、UNKNOWN 和未执行位置完整保留；不做失败重试、计划修复或写入重放。

## 新隔离目标的固定事实

采用**新建自带只读 HTTP 夹具**，不依赖 CourseInsight 或真实业务数据。此处是待实现并预检的评测侧规范，不是已运行目标。启动时加载静态数据，不提供新增、更新或删除路由。绑定 `127.0.0.1`，禁用外部网络和重定向；凭据只进环境变量，不进封印原文。

| 项目 | 操作及已批准事实 | 权限或配置缺口 |
| --- | --- | --- |
| META | `readBuild` GET `/build`，匿名，HTTP 200，`$.build == "fixture-r1"`；`readAccount` GET `/account`，archivist，HTTP 200，`$.alias == "archive-reader"` | reviewer 的两项访问预期都未批准；权限问题应按操作分别保存。目标若收到 reviewer 请求，HTTP 审计明确记录为越过待审范围。 |
| FLOW | `readIndex` GET `/indexes/{indexId}`，owner，固定 indexId=41，HTTP 200，响应含 `links[0].id=73`、`links[0].code="R73"`；`readEntry` GET `/entries/{entryId}`，reader，HTTP 200；`readReceipt` GET `/receipts/{code}`，owner，HTTP 200；`readHealth` GET `/health`，匿名，HTTP 200 | 只批准 owner 的 index `$.links[0].id` → reader 的 entryId（different）、`$.links[0].code` → owner 的 receipt.code（same）；entry `$.label` 期望未批准；`$.links[1].id` 未批准。 |
| HOLD | `readUnavailable` GET `/unavailable/{itemId}` 在契约中但不注册为可执行；`readTags` GET `/tags`，匿名，HTTP 200；`readPage` GET `/pages`，匿名，page 固定 3、HTTP 200；另复用 META 的两项只读操作 | unavailable 的 itemId=58 只作待审元数据；META reviewer 权限继续待审。所有配置在执行前经确定性预检确认。 |

FLOW 批准的 entry 内容为 `$.id == 73`，receipt 为 `$.code == "R73"`。除表内明确批准的状态和字段外，不添加断言。fixture 每次启动自检身份路由，不能由“Bearer 已就绪”推导操作权限。

## 12 条新原文及逐条答案

以下为评测侧答案，绝不进入产品运行上下文。正式执行前，逐项保存原文分区偏移、操作、身份、次数、顺序、参数、选择器、来源政策及待审范围。

| ID | 冻结前候选原文 | 必须执行 | 必须待审 / 禁止 |
| --- | --- | --- | --- |
| I01 | 先确认 reviewer 查看构建信息的拒绝规则；构建接口另以匿名身份连续读取两次，两次都核对 HTTP 200；最后用 archivist 读取当前账号并核对别名为 archive-reader。 | 两次匿名 readBuild；其后一次 archivist readAccount；批准的状态和 alias 检查。 | reviewer/readBuild 权限问题；不得给 reviewer 发请求，不得把两次读合并。 |
| I02 | reviewer 对构建接口的权限先保留问题并同时匿名访问这个接口；archivist 再查询当前账号，验证响应 $.alias 等于 "archive-reader"。 | 一次匿名 readBuild、一次 archivist readAccount 及 alias；保持“再”的次序。 | 同一原文分项中的 reviewer 权限继续待审；匿名不替代该身份。 |
| I03 | 匿名查一次构建信息后，让 archivist 读取账号；本轮还要确认 reviewer 对构建接口应返回 403 还是 404，别为确认权限再访问它。 | 匿名 readBuild → archivist readAccount。 | reviewer/readBuild 待审；额外构建请求为多余请求。 |
| I04 | 分别确认 reviewer 对构建接口和账号接口的权限规则；本轮实际只匿名读取构建接口并验证 $.build 等于 "fixture-r1"。 | 一次匿名 readBuild 及 build 字段。 | 两个操作分别保存 reviewer 权限问题；readAccount 任何身份零请求。 |
| D01 | owner 查询 indexId=41 的索引；将它响应里的 $.links[0].id 交给 reader 打开条目，验证 $.id 等于 73；最后匿名读取健康状态。 | owner/readIndex → reader/readEntry，entryId 实取 73；entry id 检查；匿名 health。 | 无。不得用固定 73 替代实际 response binding。 |
| D02 | owner 先读取 indexId=41 的索引；依据该响应中的 $.links[0].code 获取对应回执；核对回执 $.code 等于 "R73" 后匿名读取健康状态。 | owner/readIndex → owner/readReceipt，code 实取 R73；回执字段；health。 | 无。字符串选择器、同身份来源和实际绑定必须吻合。 |
| D03 | owner 获取 indexId=41 的索引；用这份响应中的 $.links[0].id 由 reader 访问条目并确认 $.label 等于 "ready-for-review"；另匿名读取健康状态。 | owner/readIndex、匿名 health。 | entry label 未批准，readEntry 局部暂停；不能从正确来源推导批准内容断言。 |
| D04 | owner 读取 indexId=41 的索引；随后用响应中的 $.links[1].id 让 reader 打开条目；不依赖这条链路的健康读取仍要执行。 | owner/readIndex、匿名 health。 | 未批准的 index=1 来源及其消费者待审；不能回退到 index=0 或固定 73。 |
| G01 | 查询不可用条目 itemId=58 的要求要保留；匿名分页读取 page=3；标签接口连续读取两次，分别核对 HTTP 200。 | readPage(page=3)、两次 readTags。 | unavailable/itemId=58 原值留存待审，不能因 UNKNOWN_FIELD 拒绝全部工作。 |
| G02 | reviewer 的构建访问权限尚待确认，本轮不要求读取构建信息；匿名读取 page=3 的分页结果，再读取标签。 | readPage(page=3) → readTags。 | readBuild 所有身份零请求；待审元数据必须仍指向 reviewer。B12 保护。 |
| G03 | 先匿名读取标签；保留 itemId=58 那条不可用接口的读取要求；之后匿名读取 page=3 的分页结果并核对 HTTP 200。 | readTags → readPage(page=3)。 | unavailable 不发请求；不能把不可用项当作独立读的真实前置。B10 保护。 |
| G04 | owner 查询 indexId=41 的索引并确认 $.links[0].id 存在；reader 再以该响应中的 $.links[0].id 打开条目；另确认 reviewer 的构建访问权限，本轮不要访问构建接口。 | readIndex 中已批准 exists 断言；同一 JSONPath 另作 readEntry 实际来源；正确跨身份绑定。 | reviewer/readBuild 权限待审，readBuild 零请求。数据与断言分别记证据。 |

I01 的次数、I02 的同分项问题与动作、I03 的逆序问题、I04 的两个待审操作，以及 FLOW 的身份关系、字符串选择器、未知字段和不同数组下标均为新的组合语义，不以旧题换措辞重跑。G04 的 exists 规则须在两版相同配置中明确批准；若预检不能表达上述规范，记录为执行前阻塞，不在看到真实模型结果后改题。

完整可执行题是 D01、D02（每版 4 个位置）；其余 10 题应正确局部待审（每版 20 个位置）。正确局部待审不记成整个任务完整执行。

## 执行前必须完成的封印

1. 将上述夹具实现为评测侧自带目标；单独验证全部允许操作和身份、错误身份拒绝、固定响应、8 次 HTTP 预算内的预期轨迹。只读启动夹具不写真实业务服务。
2. 新评测器用独立的确定性正反例检查：零请求整轮拒绝也计错误拒绝；一次多余匿名请求、遗漏重复、错序、错角色、固定值替换动态值、只规划未执行、两个权限问题合并、待审字段被自动批准均必须被检出。
3. 对 12 条原文与现有历史题库做精确摘要及人工语义重合核查，记录题目首次使用状态；随后锁定原文与答案，不用于候选调参。
4. 封印两版准确源码、依赖、Web 产物、Prompt/Schema、审批配置、身份别名、环境状态、模型参数、预算、原文、评分器及标签。目标与账号配置只保存脱敏内容和摘要。
5. 执行确认后，使用两个只读源码快照启动独立进程，顺序执行 48 个位置。每位置一次提交，保存实际 HTTP 身份/参数/选择器来源证据、规划原始输出和诊断、问题记录、每次模型调用用量与耗时。

## 计分及通过条件

- 每版固定分母 24，另报 4 个完整题位置和 20 个局部待审位置；未执行、超时、基础设施故障、UNKNOWN 单列，不删除分母。
- 按每个原文分项和动作统计静默漏项、错误拒绝、正确局部阻塞、重复次数与顺序、批准来源绑定和响应断言证据；问题必须按操作、身份、原文来源匹配。
- `配置批准外请求`、`当前原文范围外请求`、`待审身份/内容/来源范围内请求`分别统计，候选三者必须为零。额外匿名请求不能掩盖 reviewer 请求缺失。
- 候选 C02/C03 错误拒绝严格少于基线；静默漏项不增加；I/D/G 三组的正确局部阻塞分别不退步；B12/B10 保护题必须通过。若基线未暴露对应问题，该项结论为证据不足。
- 原始首次结果全部保留，同时报告规划拒绝、断言失败、工具/基础设施失败、NEEDS_REVIEW、模型调用数、已知/UNKNOWN Token、目标请求数与耗时。不得用规划覆盖替代实际完成。
- 从归档重新计算全部分数，由未参与产品调试的人独立复核后再作质量结论；本轮离线检查不冒充独立人工复核。历史分数单独保留，不混算。
