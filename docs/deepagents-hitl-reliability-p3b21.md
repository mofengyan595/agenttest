# P3-B2.1 HITL 可靠性与发布验收

开始时 `master` 为 `d7206b9e42734e305565a902aa55a0b3aa638253`，运行时基线
为 `e099ddf`。工作区有既有未跟踪的 `.venv-lock/`。仍使用 Deep Agents、
LangGraph 的 SQLite Checkpointer、LangChain 和原确定性执行器。HITL 与持久化默认关闭。

## 遗留问题与复现

| 问题 | 原始证据和原因 | 最小复现 |
| --- | --- | --- |
| 未知 DELETE | P3-B2 复核候选只支持负向 POST。删除有原计划和派发证据，读取缺少经验证的原创建回执绑定。 | 创建回执落盘，DELETE 派发后、响应落盘前退出。 |
| 真实失联 POST | `.eduwork/p3b2-live/unknown/phase-02.json` 记录靶场库存 1、派发 1、原响应未知。该计划没有可独立查证的批准身份。 | 服务端实际创建，可信响应保存前退出，随后读取复核列表。 |
| 审批崩溃窗口 | TaskStore 已提交 `TOOL` 和审批后，LangGraph 仍需保存原生中断。此前两条入口都会继续等待这条缺失的中断。 | 在 `publish("TOOL")` 提交后、`interrupt()` 前强杀。 |
| 网关 500 | `.eduwork/p3b2-live/release/phase-06.json` 的主 Agent 调用 11 返回 500，错误为 `MODEL_SERVICE_ERROR`，Token 未报告。该次调用前已有 70 个业务 HTTP，覆盖 25/31。500 当时不在瞬时故障集合，任务进入终态。 | 已执行业务工具后，再给主 Agent 返回三次明确的 HTTP 500。 |
| Windows 启动器 | `.eduwork/p3b2-verified/` 中两个报告发布测试的队列等候超时，捕获 stdout 出现了嵌套 pytest 输出。临时分组脚本缺少进程入口保护，Windows spawn 重跑顶层测试。 | 用有顶层 `pytest.main()` 的脚本启动含 multiprocessing spawn 的测试。标准入口隔离复跑 4/4。 |

## 恢复约束

- **未知 DELETE。** 仅使用原预检方案紧邻删除的批准 GET。原创建响应必须有
  已提交的 201 证据，读取与删除需使用同一个来源步骤、选择器、路径和实际资源摘要。
  原部署、原授权和请求上限重新校验。执行器再次校验全计划，只发送该 GET。
  404 记录资源不可读，200 记录残留；原 DELETE 始终是 `UNKNOWN_OUTCOME`。
  观测报告保留 `NEEDS_REVIEW`，原删除状态断言及清理凭据仍待证明。
- **未知 POST。** 隔离项目可以额外批准关联键观测。预检把服务端生成的独立键
  绑定到创建请求与前后 GET。前置可信回执须确认零匹配，后置单次 GET 观察唯一匹配后
  记录创建效果。原 201、资源身份和清理分别需要证据。缺少可清理身份时保留待复核。
  独立正向观测样本使用显式批准的查询；完整发布仍使用原 31 项配置。
- **审批对账。** 恢复入口仅在原任务与检查点一致、原审批待处理且账本没有该工具的
  派发记录时，使用保存的计划重建同一个原生中断。决定入口仍要求存在原生中断。
  原预检和预算重新校验，图收到决定后才可执行。两份存储依靠可重复的对账恢复。
  [LangGraph 中断](https://docs.langchain.com/oss/python/langgraph/interrupts)
- **网关错误。** 明确的 429、500、502、503、504 共用同一有界策略，最多两次重试。
  主 Agent 达到重试上限时保存原图位置和原任务账本。明确拒绝各计一次调用，缺失
  Token 留空，传输中断等未确认用量仍走原有核对门槛。目标业务 HTTP 从独立执行证据
  进入断言，格式或计划错误仍按各自类别处理。
- **再次强杀。** 核实决定和一次性申领存入 SQLite。进程退出后保留核实账本的证据引用，
  无可信结果的 GET 不再自动派发。恢复查询包含原未知派发和额外核实的保守使用量。

## 测试入口

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_step_recovery.py tests/test_product_durability.py tests/test_negative_post_observation.py tests/test_human_review.py
.\.venv\Scripts\python.exe scripts/run_frozen_regression.py --out .eduwork/p3b21-frozen --exclude-local-environment
```

启动器用标准 `python -m pytest` 作为唯一完整测试进程，保存全部收集 ID 和执行阶段
结果，拒绝漏项、重复收集或重复执行。只在当前测试进程排除既有未跟踪的虚拟环境。
开始和结束均记录整个参与校验的工作区摘要。

## 真实故障注入

六个独立位置保留在 `.eduwork/p3b21-live/`。使用 ecnu-max 生成 Focus、
方案与工具调用。真实验收的 Python 源码摘要为
`bc67190f142a204a8a212bf497c202c50b3bd2191051a9b91de34fbb87174c5a`。
强杀位置在客户端取得临时响应之后、SQLite 保存可信响应之前。后续由新工作进程承接，
使用同一部署的原任务和已批准读取。

| 位置 | 实际业务 HTTP | 模型调用 | 已知 Token | 核实与终态 |
| --- | --- | ---: | ---: | --- |
| 未知 DELETE，资源消失 | POST、DELETE、GET，各 1 次 | 5 | 24,374 | GET 404，`ABSENCE_OBSERVED`，库存 0，原 DELETE 响应未知 |
| 未知 DELETE，假 204 | POST、DELETE、GET，各 1 次 | 5 | 24,331 | GET 200，`RESOURCE_STILL_PRESENT`，库存 1，原 DELETE 响应未知 |
| 失联 POST，显式关联键 | GET、POST、GET，各 1 次 | 5 | 26,394 | 唯一匹配，`CREATION_OBSERVED`，库存 1，原 POST 响应未知 |

前两例的核实绑定原 DELETE 的实际路径摘要与原 POST 201 的同一身份。第三例
批准查询的返回仅含匹配数，因此没有可用于删除的实例身份，资源保持待清理。
三例均保持 `REVIEW_REQUIRED`，复核追加 1 次 GET，原未知的写入各仅派发一次。

完整发布的网关注入发生在已经提交 2 个用例、8 次业务 HTTP 后。主 Agent 收到
三次显式 HTTP 500，保存 `AGENT` 检查点。跨进程恢复到下一个审批点时，两个用例
的原始记录逐字段相同，业务 HTTP 仍为 8，模型计数沿用 9 次并继续累积。
注入的 500 不冒充模型服务实测故障；上一里程碑的真实 500 单独保留。

## 完整发布三轮

三轮保留同一份需求与原策略分母。工具调用计主图历史中的 `TOOL_CALL`，
HTTP 计靶场实际业务事件，排除健康与契约请求。

| 位置 | 覆盖 | HTTP | 模型调用 | 已知 Token | 工具调用 | 批准 | 负向 | 有清理凭据 | 库存 | 严格完成 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `release-gateway` | 31/31 | 94 | 28 | 220,586 | 17 | 8 | 24/24 | 15/15 | 0 | 是 |
| `release-r2` | 31/31 | 95 | 25 | 200,273 | 17 | 9 | 24/24 | 15/15 | 0 | 是 |
| `release-r3` | 0/31 | 0 | 2 | 28,673 | 0 | 0 | 0/24 | 0/0 | 0 | 否 |

两轮完成中 24 项负向各执行一次，10 个负向 POST 各有同键无副作用证明。
`readAvailability` 实际返回 503，`checks_passed=false`，`complete=true`。
第二轮多一次独立可用性读取，负向义务未重复。第三轮 Focus 首次遗漏原子句
（`FOCUS_SOURCE_CLAUSE_MISSING`），纠错仍未取得通过校验的范围。两次原始输出及
`MODEL_INVALID_RESPONSE` 留在 `release-r3/model-outputs.jsonl`。任务停在
`NEEDS_REVIEW`，未进入工具审批或 HTTP 执行。

交叉核对发现第二轮多出的可用性读取走 `supervise_read`，进入预算和用例报告，
当时没有进入 `http_execution`，形成预算及靶场 95、步骤账本 94 的差异。
最终改动在探针发送前保存计划指纹及原工具位置，使用稳定 run ID 进入原步骤账本，
并提交原执行器内置只读探针的证据阶段。无密钥故障注入在探针响应落盘后中断，
重进只从原回执生成证据，预算仍为 1，靶场 GET 仍为 1。三轮原始结果按原源码保留，
最终验收源码摘要为 `9124a0a989c990425b70032e8b60d5a6bb7fee7145fb552b7599ee92bb549ac1`。

### 最终源码追加验收

`release-final` 是发现账本差异后的独立验收，沿用相同 31 项批准范围并再次在 8 次
业务 HTTP 后注入三次网关 500。最终预算、HTTP 账本 `EVIDENCE_COMMITTED`、
靶场事件均为 86 次。30 次模型调用、227,222 已知 Token、21 次工具调用及 7 个批准
均有记录。22 个负向策略各测一次，13 个创建资源均有清理凭据，库存为 0。
真实 503 仍为 `ASSERTION_FAILED`，未观察到未知写入重放或错误清理凭据。

该轮覆盖 **29/31**，严格完成判据未满足。两次 `negative_batch_7` 方案分别遭
`JSON_SYNTAX` 与 `CONSTRUCTION_REFERENCE_INVALID` 拒绝，最终到达
`NO_PROGRESS`。仅 `web_rule_7_negative_8` 和 `web_rule_7_negative_9` 未测，
其余逐项状态与上表一致。原始模型输出与完整报告保存于 `release-final/`。

下表列出前两轮各项实际状态。第三轮所有列出的义务均为未执行。

| 原批准策略 | 网关注入轮 | 独立第二轮 |
| --- | --- | --- |
| `web_rule_1` | 503，断言失败 | 503，断言失败 |
| `web_rule_3` | 201，PASS | 201，PASS |
| `web_rule_3_negative_1` | 422，PASS | 422，PASS |
| `web_rule_3_negative_2` | 422，PASS | 422，PASS |
| `web_rule_3_negative_3` | 422，PASS | 422，PASS |
| `web_rule_3_negative_4` | 422，PASS | 422，PASS |
| `web_rule_3_negative_5` | 422，PASS | 422，PASS |
| `web_rule_3_negative_6` | 422，PASS | 422，PASS |
| `web_rule_3_negative_7` | 422，PASS | 422，PASS |
| `web_rule_3_negative_8` | 422，PASS | 422，PASS |
| `web_rule_3_negative_9` | 422，PASS | 422，PASS |
| `web_rule_3_negative_10` | 422，PASS | 422，PASS |
| `web_rule_4` | 204，PASS | 204，PASS |
| `web_rule_5` | 200，PASS | 200，PASS |
| `web_rule_5_expectation_1` | 404，PASS | 404，PASS |
| `web_rule_6` | 200，PASS | 200，PASS |
| `web_rule_6_negative_1` | 422，PASS | 422，PASS |
| `web_rule_6_negative_2` | 422，PASS | 422，PASS |
| `web_rule_6_negative_3` | 422，PASS | 422，PASS |
| `web_rule_6_negative_4` | 422，PASS | 422，PASS |
| `web_rule_7` | 200，PASS | 200，PASS |
| `web_rule_7_negative_1` | 422，PASS | 422，PASS |
| `web_rule_7_negative_2` | 422，PASS | 422，PASS |
| `web_rule_7_negative_3` | 422，PASS | 422，PASS |
| `web_rule_7_negative_4` | 422，PASS | 422，PASS |
| `web_rule_7_negative_5` | 422，PASS | 422，PASS |
| `web_rule_7_negative_6` | 422，PASS | 422，PASS |
| `web_rule_7_negative_7` | 422，PASS | 422，PASS |
| `web_rule_7_negative_8` | 422，PASS | 422，PASS |
| `web_rule_7_negative_9` | 422，PASS | 422，PASS |
| `web_rule_7_negative_10` | 422，PASS | 422，PASS |

## 无密钥验证与限制

- P3-B2 回归专项 35 项通过；最终跨进程审批与核实、网关故障及安全专项 9 项通过。
  正向关联键样本独立通过。兼容专项 201 通过、8 跳过，209 个收集 ID 均有结果。
  前端类型检查和 80 项 Vitest 通过，Ruff 与 `git diff --check` 通过。
- 跨部署、错误资源摘要、预算不足均阻断读取；原生中断未落盘时按同一审批重建。
  核实再次退出后的证据保留为未确认，清理凭据与断言仍依赖原始 HTTP。
- 当前恢复观测适用于无需登录的明确授权路径。第三轮保留了 Focus 变异导致的
  安全停止，因此三轮真实发布成功比例为 2/3。

## 门槛结论

**NO-GO，暂缓 P3-B3。** 网关续跑、写入核实和同计划审批崩溃恢复均已获得证据。
账本修复后的最终源码发布仍有 2 项未完成。下一步基于冻结的
`negative_batch_7` 请求与输出最小复现并核对构造引用，再在独立隔离项目中
复验原 31 项范围。

## 接管复核（2026-11-16）

原执行者的会话在最后一轮真实验收结束、本报告与 `docs/progress.md` 写完后终止，
留下未提交的运行时改动、本报告和冻结回归脚本。接管时 `HEAD` 与 `origin/master`
同为 `d7206b9`，工作区有 19 个修改文件和 3 个未跟踪文件，没有暂存内容。

- 第一次冻结回归（`.eduwork/p3b21-frozen/`）在第 2% 处中断，没有 `result.json`；
  其 `before.json` 与 145 字节日志原样保留，不计入结果。
- 接管时第二次冻结回归（`.eduwork/p3b21-frozen-final/`）已在最终源码上运行，
  因此没有重复启动；等待其结束后直接采用结果：`2867 passed, 15 skipped,
  1 xfailed`（2792.50 秒），收集 2883 个 ID，漏项、重复、多余、重复收集均为 0，
  开始与结束的工作区摘要同为 `ca419b362de038a5…`，`same_checkout=true`，退出码 0。
- 冻结回归运行期间未改动任何跟踪文件。回归结束后只追加本节的文档记录，
  运行时、测试与脚本保持回归时的字节。
- 当前源码指纹 `9124a0a989c9…` 与 `release-final/manifest.json` 记录的最终验收
  源码一致；三个写入核实位置的指纹 `bc67190f…` 更早，两者差别只是核实探针的
  账本提交改动。
- Ruff、`git diff --check` 与密钥扫描通过；未提交内容全部属于本里程碑，没有
  环境文件、临时文件或无关改动。

逐项复核已有产物，未发现需要修复的执行器、Policy 或 Validator 缺陷：

| 产物 | 负向测量 | 重复 | 创建/清理 | 写入请求 | 重放 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `release-gateway` | 24/24 | 0 | 15/15 | 25 | 0 |
| `release-r2` | 24/24 | 0 | 15/15 | 25 | 0 |
| `release-final` | 22/24 | 0 | 13/13 | 23 | 0 |
| `release-r4`（本轮追加） | 20/24 | 0 | 11/11 | 21 | 0 |

- `release-r2` 的一个候选用例在发送前被 `OPENAPI_UNKNOWN_BODY_FIELD` 拒绝，属预期拦截。
- 三个写入位置的复核记录分别是 `ABSENCE_OBSERVED`（GET 404）、
  `RESOURCE_STILL_PRESENT`（GET 200，原 DELETE 仍为 `UNKNOWN_OUTCOME`，
  假 204 的断言降级为 `NEEDS_REVIEW`）和 `CREATION_OBSERVED`（唯一匹配 1），
  均保持 `REVIEW_REQUIRED`，各追加 1 次 GET。
- `negative_batch_7` 的两次失败原始输出已逐字核对：第一次是模型输出在
  `"name",` 处缺少冒号导致的 `JSON_SYNTAX`，第二次是模型把两条 PUT 负向策略
  当成只读约束、`before` 为空却引用 `create_seed`，被
  `CONSTRUCTION_REFERENCE_INVALID` 拒绝。同样两项在前两轮各执行一次，
  因此这是模型规划变异，不是构造或校验缺陷。

### 追加的最终源码独立发布

`release-r4` 在最终源码上另开独立隔离靶场，沿用同一 31 项批准范围，并在已提交
8 次业务 HTTP 后注入三次 HTTP 500。三次拒绝都指向同一检查点，停止后由新工作
进程续跑，保守用量仍为 8，模型调用从 8 累加到 38，没有重置预算。

| 覆盖 | 业务 HTTP | 模型调用 | 已知 Token | 用例 | 批准 | 严格完成 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 27/31 | 79 | 38 | 287,225 | 23 | 7 | 否 |

`readAvailability` 实际返回 503，`checks_passed=false`；11 个创建资源全部取得清理
凭据，库存 0，没有未知写入重放。`web_rule_7_negative_1`、`_2`、`_3`、`_10` 未测：
主 Agent 在义务仍存在时给出了结束答复，运行时按确定性判据记为未完成，而不是
按模型结论宣布通过。

### 复核后的门槛

最终源码在两次独立运行中为 29/31 与 27/31，没有任何一次 31/31；账本修复前的
两轮 31/31 不能算作最终源码样本。因此 4/5 稳定性目标未达到，NO-GO 不变。

剩余问题收敛为一条：`replaceParcel` 负向批次的监督停止策略仍允许主 Agent 在
批准义务未测完时结束（`ANSWER` 或 `NO_PROGRESS`）。运行时如实报告缺口，安全约束
全部保持，但覆盖无法保证。下一里程碑应先在该批次的监督层收紧结束条件，再用
新窗口独立运行 5 次，其中至少 4 次达到 31/31 并保留清理证据。

### 远端 CI

实现提交 `d992dfa849d26032e7970ac4638fe8389e96d5c2` 的 7 项检查全部通过：

| 工作流 | 运行 | 结论 |
| --- | --- | --- |
| Keyless Python checks | [#186](https://github.com/mofengyan595/agenttest/actions/runs/43818140832) | success（Ubuntu/Windows × base/agent 四组离线、Ruff、冒烟，加 Deep Agents 运行时） |
| Web Console checks | [#165](https://github.com/mofengyan595/agenttest/actions/runs/43818140825) | success（Ubuntu/Windows，类型检查与 Vitest） |
