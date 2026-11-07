# C02 / C03 规划缺陷修复（2026-11-07）

开发起点：`77effe8db742419c7256509bafbd417562d40f71`，原分支 `fix/mixed-planning-p0`，工作区干净，origin 指向 `https://github.com/mofengyan595/agenttest.git`。读取了 `CODEX_TASK.md`、项目规则和 [P0 真实复验](mixed-planning-p0-ab-20261107.md)。本轮用户明确授权两项修复及通过后独立分支的本地提交；不推送、不合并、不修改 CourseInsight、不做真实业务写入、不运行付费模型。

验证期间检测到另一项工作并发修改 Windows 报告发布性能相关的 `report_files.py`、`test_report_publication.py` 及 README、progress、validation-performance 文档。用户明确回复“已暂停另一项工作，保留其改动并继续”。这些改动保留并冻结，不属于 C02/C03 提交；最终全量在包含这些保留修改的工作区运行，不能等同于只含本提交的干净 checkout 验证。其文件摘要单独记录，本轮未覆盖或提交它们。

## 根因与改动

### C02：权限问题和执行动作使用了同一类见证

权限问题校验要求其原文分项必须包含同一操作、同一角色的动作，故“只确认 reviewer 权限”的合法问题在 `FOCUS_PERMISSION_QUESTION_WITHOUT_ACTION` 处被整轮拒绝。若绕过这一层，required 分项 `actions=[]` 又触发 `FOCUS_REQUIREMENT_WITNESS_MISSING`。生成 Schema 同样强制 required 至少一个动作，反而诱导生成不应执行的 reviewer 请求。

同时，“同一个版本接口”的字面 `same_resource` 被当成实体资源引用要求见证；无参数的接口没有可引用的实体资源，导致独立匿名读取也被拒绝。既有 B12 的匿名动作判别尚不包含“匿名访问”。

本轮在 `request_requirements.py` 定义单独的权限询问见证：核对精确分项、当前来源、操作的待审角色候选及明确权限询问。源于该分项的询问可以没有 HTTP 动作，不会被标为执行完成；选择接口可以只为承载该问题，来源记录为 `PENDING_PERMISSION`。生成 Schema、解码及任务入口使用一致约束。无问题或无身份来源的空动作仍拒绝；显式匿名动作不能借此漏掉。

同接口指代只对 GET/HEAD、同操作、当前或较早的真实权限问题放行；匹配到的实体/资源指代仍要求资源见证。`product_focus.py` 沿用 B12 的待审记录、执行视图及最终身份门禁，补全匿名访问语法。原始 requirement disposition/actions 保留；reviewer 的问题与匿名执行分开记账，reviewer 不被替换为匿名。

同一分项因 reviewer 问题整体标为 needs_review，但含有明确匿名动作时，执行视图也按身份拆开：只将这些已明确请求的匿名动作标为 required，reviewer 问题仍待审。存在同源输入问题或另一个业务问题时，不通过这一规则提升执行范围；未知断言与来源的既有阻塞门禁继续生效。

Python 全量结束后，仅同步 `web/src/api/tasks.ts` 的类型声明，允许新的 `PENDING_PERMISSION` 来源值；没有修改界面或 JavaScript 行为。独立运行现有 TypeScript `--noEmit` 检查并核对该运行前后 HEAD/正式文件摘要相同。Python 运行代码与已通过全量的源码逐字节一致；前端类型声明的验证时间与摘要单独记录。

### C03：纯数据用途由固定动作词表决定

原提取器要求 JSONPath 之后匹配“读取、查询、调用、作为”等词，因此“随后用该响应中的 ... 打开相应专科”没有进入数据来源对账，却被响应检查提取器保存为 `operator=None`，从而暂停下游。

`requirement_ledger.py` 改为先从有界的“响应来源表达式 + JSONPath + 后续用途”提取候选，区分明确谓词及同次出现的检查，移除下游动作词表。候选本身不授予任何权限。进入执行范围还须核对：

1. 相邻原文分项中唯一的上游生产者和下游消费者；
2. 两端精确身份及批准的 same/different/anonymous 等关系；
3. 精确 JSONPath、源策略、消费策略、入参位置和名称；
4. 策略来自现有可信注册表，两端断言均已审阅且批准来源非空，生产者不是负例；
5. 匹配必须唯一，多输入槽或多政策歧义继续待审；
6. 最终计划必须以正确 source step/policy/role 绑定该参数，实际主执行报告才推进 EXECUTED。

保留既有执行器和批准断言机制。错误身份、未批准选择器、错误 source policy 或实际绑定仍被拒绝；不会自动改选择器、补固定 ID 或改预期。完整任务测试用两种上游返回值（7、19），确认下游路径随真实响应变化。

## 反例与最终验证

新增 `tests/test_mixed_planning_c02_c03.py` 的 37 项正反例。修复前正常环境的首次反例为 **10 failed / 2 passed**，原始 XML 留存，不重写为通过记录。覆盖无 reviewer 动作的权限问题、同接口匿名独立读取，以及 C03 三种下游动作表述。后续补入正常 Wire/Schema、空动作错误来源、同分项不同身份、原始数组选择器、唯一来源政策和真实绑定等正反例。

最初受限环境出现临时目录权限错误；改目录后证明标准库 `asyncio.run(asyncio.sleep(0.01))` 也无法完成，因此中断该运行，切换正常本机环境。测试夹具继续禁止 DNS 和网络连接；没有用提权放开目标访问。中间一轮为 61 passed / 1 failed，失败来自新夹具缺少 credential_status，修正夹具后 79 passed。首次完整相关批次为 333 passed、安全 116 passed、固定 48 passed；随后增加同分项“同一个接口”的边界并重新完整验证。

| 最终批次 | 实际结果 | Pytest 时间 |
| --- | --- | --- |
| 相关回归 | 340 passed / 0 skipped / 0 failed | 255.69 秒 |
| 安全回归 | 116 passed / 0 skipped / 0 failed | 38.12 秒 |
| 固定回归及导出审计 | 48 passed / 0 skipped / 0 failed | 24.45 秒 |
| 全量回归 | 2368 passed / 15 skipped / 0 failed | 3530.54 秒 |

相关、安全、固定批次使用项目 `.venv/Scripts/python.exe -m pytest -q`；全量使用已有 `scripts/check_full.py --jobs 4`，默认收集全套测试，逐 ID 核对收集与实际终态、遗漏、重复、退出码及源码摘要。全量表中为各进程 Pytest 时间合计，实际墙钟见验证清单。相关批次指定需求、来源、聚焦、设计、计划构造及任务测试，安全批次指定 SSRF/执行器/负例/取消/敏感字段测试，固定批次包含 `examples/regressions` 及导出/回放审计。完整参数和原始结果见 [验证清单](evidence/mixed-planning-c02-c03-20261107/verification.json)及同目录的四份 `*-verification.json`。`ruff check .` 和 `git diff --check` 已通过。

全量跳过原因：Windows symlink privilege unavailable（2 项）；could not import 'schemathesis': No module named 'schemathesis'（1 项）；POSIX link semantics（1 项）；POSIX directory replacement semantics（1 项）；POSIX FIFO semantics（1 项）；POSIX nofollow/dir_fd semantics（1 项）；Symlink creation unavailable: 1314（7 项）；OS does not grant symlink creation（1 项）。

第一次全量运行约至 45% 时，额外只读复查发现同一分项 needs_review 会连明确匿名动作一起暂停；该次运行主动结束并保留部分日志，不计作全量通过。新增反例先得到 **2 failed / 2 passed**，修复后集中回归 **85 passed**，再加入两个不允许扩大执行范围的保护反例，重新运行全部最终批次。

重跑相关批次得到 340 passed，但严格全仓摘要检查发现三份文档在运行中变化，因此该次冻结校验失败，未继续安全/固定/全量。用户暂停并发工作后，再从新冻结开始运行上述最终批次。

最终全量采用仓库已有的 `scripts/check_full.py --jobs 4`，保留全部默认收集 ID，按文件分为四个隔离进程，不调整测试内容或失败重试。开始前已结束刚启动的串行全量及其子进程；该串行片段不计入完成结果。并行全量的全部终态 ID、批次退出码和源码 ZIP 摘要独立核对后，才计作全量验收。

每一最终批次在开始和结束分别冻结 Git HEAD 及全部正式文件摘要（包括未跟踪的新测试），结果见证据目录的 `*-verification.json`；最终运行期间没有修改源码、HEAD、分支或正式文档。测试与报告输出仅写入忽略的 `runs/`。

提交前对拟交付新增文本检查敏感路径、常见 Token/私钥格式及本机配置中的敏感值匹配，发现数为 0；没有提交 `.env`、凭据、缓存或私有运行目录。另一项工作的文件保留，progress 仅纳入本轮追加段落。

## 保留与限制

- B12：没有独立匿名动作时不发匿名兜底；错误角色计划仍拒绝。B10：不可用接口的固定输入保留待审，独立读取和既有次数、顺序回归继续通过。相关历史测试未改。
- 历史证据目录的 54 个已跟踪文件（包括 ZIP 归档和原始成绩）逐文件 SHA-256 前后核对；历史报告未修改。完整性结果见 `verification.json`。
- 数据来源仍是有界语言与数据流验证，不是任意自然语言理解。非相邻/多生产者、歧义参数、未支持来源表达、被引号包裹的 JSONPath 等可能待审或拒绝；不据此自动扩展授权。
- 数据和未知断言同分项兼用但无法确定谓词时，继续待审。源码范围没有扩展次数提取器，不声称任意复合句都能独立抽出完整次数。
- 模型违反 Schema、错误身份或实际绑定时，仍可能使同组计划一起拒绝；没有新增自动修复、重规划、写入重放或容错执行。
- 本轮只有确定性模型响应和 MockTransport。真实模型规划质量、成本及 12 条新题的成绩尚未验证，独立人工复核也未完成。

下一准确里程碑：按 [12 条全新需求 A/B 方案](mixed-planning-c02-c03-ab-plan-20261107.md) 完成新隔离只读夹具、评测器正反例和执行前封印，确认付费执行后再运行 48 个首次位置。不能把本轮离线回归当作真实 A/B 提升证据。
