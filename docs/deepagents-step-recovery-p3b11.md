# P3-B1.1 可信 HTTP 步骤恢复

基线 `a5f6e3b0f706617349833e07d1a817f97d8c2582`，上一里程碑运行时
`c96052c61c22d220a7cba4721582e2b79804979f`。本轮运行时 `e797b23398a584c3a1c1cb2e06b971f86cd43339`。
验收使用一次性本地靶场与真实 ecnu-max，定向和全量回归使用无密钥环境。

## P3-B1 的恢复边界

P3-B1 通过原生 Checkpoint 恢复主图，在完整工具和负向批次返回后继续。历史
`.eduwork/p3b1-live/` 的生命周期在全部 3 个业务请求完成后暂停，恢复新增 0 个；
完整发布在首个负向批次结束后暂停，由 11/31 续至 31/31。坏检查点、取消、
竞争和目标替换有拒绝路径。旧 `ACTIVE` 中断只保留证据并停在复核，
无法从工具内部已有的 HTTP 回执续作。原生图的 pending writes 不能证明远端写入结果。

## 存储与提交顺序

| 存储 | 职责 |
| --- | --- |
| LangGraph SQLite Checkpointer | Deep Agents 图状态、消息、工具调用、节点进度。使用任务 ID 作 thread ID，同步保存 |
| TaskStore | owner、任务阶段、恢复申领、应用状态与原生 checkpoint ID。运行前后校验目标部署、项目批准版本、原需求和快照摘要 |
| TaskStore `http_execution` | 单次 HTTP 尝试的派发意图、响应、阶段和已观测活动时间 |
| Evidence 和报告 | 保留原 run ID、开始时间、可信尝试、断言与覆盖状态，报告发布沿用原有路径 |
| 资源生命周期和写效果 | 原 Executor 使用创建回执、批准删除、同身份缺失回读和负向关联键前后观测生成凭据 |

沿用 Deep Agents 0.7.35、LangGraph 1.2.20、checkpoint 4.3.1、
官方 SQLite saver 3.1.3、LangChain 1.4.10、langchain-core 1.7.7、aiosqlite 0.22.1、sqlite-vec 0.1.9。
内层 Workflow 以独立的 LangGraph 调用重建原 Executor，隔离外层图的任务上下文和检查点。
原 TaskService、Web/CLI 和持久化默认关闭配置沿用 P3-B1。
[LangGraph 持久化](https://docs.langchain.com/oss/python/langgraph/persistence) ·
[LangGraph 中断与恢复](https://docs.langchain.com/oss/python/langgraph/interrupts)

以 `AGENTTEST_DURABLE_EXECUTION=1` 和当前 `AGENTTEST_DURABLE_TARGET_EPOCH` 启用。
`GET /api/tasks/{task_id}/recovery` 返回原任务的恢复条件，工具中断时包含保守的
HTTP 计数和不确定派发数量。`POST /api/tasks/{task_id}/resume` 沿用任务 owner 和
原批准范围申领工作，`POST /api/tasks/{task_id}/cancel` 取消当前或等待恢复的任务。
应用检查点沿用 `agenttest.durable.v1`，新增的阶段对旧运行时不可识别时会拒绝恢复。

1. 业务工具保存 `TOOL` 标记、经验证的设计及证据映射、预算、执行计划摘要，
   与原生 checkpoint ID 和 tool call 绑定。重启重新运行原 Validator，
   由稳定的工具、组、用例和步骤身份关联原尝试。策略、目标部署、路径和规范化请求的
   摘要必须吻合。相同 HTTP 形状可以属于两个独立批准实例。同一检查点由一个业务工具
   持有恢复标记，多个并行提案中的其他业务工具可以在下一轮单独提交。
2. Executor 以两个 SQLite 事务依次提交 `PREPARED`、`DISPATCHED`，使用
   `BEGIN IMMEDIATE` 和 `synchronous=FULL`。当前发送路径只有在
   `DISPATCHED` 提交后才可能访问目标。恢复要求同一已批准尝试且台账仍是
   `PREPARED` 才允许继续该步骤。只要看见 `DISPATCHED` 且没有可信回执，
   即使服务端实测收到零次也保留为不确定派发。
3. 可信响应连同尝试号、HTTP 状态、内容摘要、解析投影和可恢复标志提交为
   `RESPONSE_RECORDED`。过滤会改变原投影或出现凭据时不保存可恢复原文，后续停止复核。
   已知回执重新进入原断言及资源状态机，报告投影完成对应 `EVIDENCE_COMMITTED`。
   业务失败、清理待证实和清理已证明仍由原判据区分。重建已有尝试扣除相同的 HTTP 配额，
   只有新步骤进入网络；模型设计快照复用原调用和 Token 结算。
4. 工具结果及完整范围、预算和报告视图标记为 `TOOL_DONE`，先于主图保存工具消息。
   回归计划归档按原 run ID 幂等写入，相同内容可复用，身份冲突拒绝。
   发布跨 SQLite、报告文件和主图，通过回执重建及校验对账。
   旧的已完成用例、创建者关联、清理结果和剩余义务保留在原任务报告中。
   清理续跑只执行已批准原计划里的步骤，目标身份继续来自原创建回执。

任务目录的 OS 文件锁与 TaskStore 事务申领约束同一部署内的 Worker。
账本写入检查任务仍在 RUNNING；取消和恢复竞争由任务状态决定，终态拒绝重新申领。
恢复计入已落盘的活动时长，等待重启的时间排除；退出前最后一笔事务到进程终止
之间的活动时间无法从持久化记录精确还原。不确定派发按已用 HTTP 计数并另列数量。

## 故障注入

测试靶场独立。子进程以 `os._exit(89)` 退出，再由全新 Python 进程执行同一任务。
用服务端事件、SQLite 尝试及最终库存交叉核对。正式路径默认无故障注入。

| 强制退出边界 | 恢复结果 |
| --- | --- |
| 派发意图提交前 | 原地 0 HTTP，完成原计划的 3 个请求 |
| `PREPARED` 提交后、进入派发前 | 0 HTTP，原请求只发送一次 |
| `DISPATCHED` 提交后、发送前 | 0 HTTP，预算保守计入 1 次不确定派发，拒绝重发 |
| 服务端创建、客户端未收到响应 | 靶场观测 1 个 POST 和 1 个残留，恢复新增 0 次 HTTP，保留待复核 |
| 客户端取得响应、落盘前 | 靶场观测 1 个 POST 和 1 个残留，恢复新增 0 次 HTTP，保留待复核 |
| POST 回执落盘、断言前 | 复用 201 和创建身份，只补 DELETE 204、GET 404，库存归零 |
| POST 断言已提交 | 只补 DELETE、GET，库存归零 |
| DELETE 回执落盘 | 只补同身份 GET 404；假 204 用例得到 200，报告断言失败、库存 1 |
| 清理回读完成、工具未返回 | 恢复新增 0 次 HTTP，保留原报告、清理和回归计划 |
| 连续两次强杀 | 先在 POST 回执后、再在 DELETE 回执后退出，最终全程仅 3 次业务 HTTP |

负向 POST 测试独立重建原计划，用同一已批准关联键恢复
`GET 200 → POST 422 → GET 200`，仅续跑后一次 GET。失配关联键或失败的零计数断言
保留 `CREATION_UNPROVEN`。同一请求 422 并不能单独证明无副作用。

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_step_recovery.py tests/test_product_durability.py tests/test_negative_post_observation.py
.\.venv\Scripts\python.exe scripts/run_p3b1_resume.py --out .eduwork/p3b11-repro --kind delete --port 30888 --break-after-method POST --break-after-status 201
```

## 真实 ecnu-max

各位置沿用同一已批准原文。不同位置使用独立靶场，恢复核对同一个部署实例。业务 HTTP
来自靶场事件，排除健康探针。覆盖指用例报告已提交的请求义务。模型用量为实际网关回执。
SQLite 大小为主文件读数；中断时部分页面可能仍在 WAL。恢复时间包括继续执行的模型、
工具与业务 HTTP，排除靶场准备。

| 位置 | 累计 HTTP | 模型调用 | 已知 Token | 请求覆盖 | 库存 | 恢复耗时 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| POST 201 后中断 `post-final` | 1 → 3 | 5 → 7 | 24,546 → 38,108 | 0/3 → 3/3 | 1 → 0 | 12.93 秒 |
| DELETE 204 后中断 `delete-fixed` | 2 → 3 | 6 → 8 | 37,449 → 50,411 | 0/3 → 3/3 | 0 → 0 | 8.81 秒 |
| 假 204 后中断 `fake-delete-final` | 2 → 4 | 5 → 8 | 24,353 → 45,977 | 0/3 → 3/3 | 1 → 1 | 16.08 秒 |
| 发布 422 后中断 `release-awake` | 10 → 94 | 8 → 24 | 58,231 → 214,731 | 7/31 → 31/31 | 0 → 0 | 169.28 秒 |

- 原生命周期用例将同一个创建 ID 绑定到 DELETE 与缺失 GET。已落盘的尝试回执逐字段保留。
  正常路径各 1 次 POST 201、DELETE 204、GET 404，原报告 ID 和清理凭据可读。
- 假 204 实际为 201、204、200。新增的另一条 GET 是原批准的只读复核，也返回 200。
  仍为 `ASSERTION_FAILED`，`CREATED_CLEANUP_FAILED=1`、`CREATED_AND_CLEANED=0`，
  严格完成和 `checks_passed` 均为 false。
- 完整发布从负向 POST 422 回执接续同关联键的后置 GET。中断前完成的 2 份用例结果
  逐字段相同，原 422 回执及同批先行 GET 保留。最终 24 项负向策略各一次，
  10 次集合负向 POST 有同键无副作用凭据，15 个新资源均有已批准的清理凭据。
  真实 503 继续是 `ASSERTION_FAILED`。`complete=true`，`checks_passed=false`。
- 实际业务请求经原 Policy、Validator 和 Executor，以上成功续跑和对抗样本的授权报告均为真。
  已确认写重放、未知写自动重放、负向义务重复和错误清理完成证明均未观测到。
  所列样本的 ecnu-max 请求均返回 HTTP 200，429 行为由无密钥故障注入测试验证。

以上四个位置的已完成工具数分别为 3 → 6、2 → 5、2 → 6、5 → 13。恢复后 SQLite
主文件依次为 278,528、278,528、327,680、679,936 字节。新脚本记录的三个中断位置
主文件均为 4,096 字节，其他已提交页面可位于 WAL。发布实际增加 84 次业务 HTTP、
16 次模型调用和 156,500 Token。

原数据在 `.eduwork/p3b11-live/`，每个位置保存 manifest、前后阶段、原始模型输出、
TaskStore、报告及靶场事件。正常 A、B 的首次成功使用变更中的源码
`3fbfe78e378e030d9e5791f5c31c4250e11a08202f5af29fa8c63aae7b2a91c1`。
最终 POST 201 复验、完整发布及假 204 使用最终实现源码
`b168061933142f034230798dbfebf19a43058d5a4eac3d4fecc7d7a679ecf5f5`。

### 保留的中途停止和失败

- `post`、`fake-delete-fixed` 在 Focus 停止。分别 2 次模型调用、26,211 和
  26,285 Token，业务 HTTP 都为 0。`release-confirmed` 因 Focus 原文保真拒绝，
  1 次模型调用、14,717 Token，业务 HTTP 为 0。三者没有进入恢复边界。
- `post-r2` 在 POST 201 后退出，同一任务重启时内层图继承了外层检查点。
  执行节点跳过初始化，旧运行时标记 `FINISHED`，实际严格未完成，业务事件只有
  1 个 POST，库存 1，已证实的 HTTP 也没有正确纳入任务汇总。该样本保留；
  隔离图上下文的修复经完整产品入口强制退出测试及后续实测验证。
- `release-fixed` 达 7/31，在 422 回执后因观察关联键被重新随机生成，
  `STEP_APPROVED_GROUP_CHANGED` 停止。实际 HTTP 10，恢复新增 0，
  8 次模型调用、52,074 Token，库存 0。确认问题后，用工具调用和用例索引稳定生成
  原观察键，独立用例仍使用不同的键。
- `release-final` 已续过原 422 路径，恢复过程跨越系统休眠，Windows 记录
  07:35:09 唤醒。恢复阶段记录 26,136.72 秒，超过 900 秒任务预算，最后一条
  已登记的 GET 没有可信回执。靶场实际 70 次业务 HTTP，保守记账 71；
  已测负向政策无重复，17 个负向政策 ID 见于用例记录，其中 16 个带完整投影，
  5 条负向关联观察已证明无副作用，12 个已确认创建完成清理。库存为 0。
  后续完整重验保留同一范围和故障，重新使用独立靶场。

## 离线和远端回归

- 步骤恢复、Executor、资源生命周期与 P3-B1 恢复组合早期 48 通过；规划、Web、CLI、
  MCP 兼容组合 124 通过、3 跳过。当前关联键重建和工具返回前归档复用各 2 通过。
  取消、竞争恢复、版本/部署标识变化、损坏检查点和 429 均保留原专项。
- 早期冻结缺最后一批终态，另一次跨越休眠出现三个失败，三条单独重验均通过。
  后续六进程复验保留一条既有取消用例的 20 秒同步超时。在本地 Git 快照中，
  未跟踪的备用虚拟环境包含 1,043 个文件。仅对验证进程排除此目录后，
  同一取消用例通过。最终四进程冻结沿用该排除项，保留原仓库、虚拟环境和用例断言。
  完整收集与结果在 `runs/p3b11-full-source/result.json`。
- 最终全量 2,867 个测试 ID 全部有终态，2,851 通过、15 跳过、1 个既有 xfail，0 失败、0 错误，4 批退出码均为 0，墙钟耗时 2,322.39 秒。Ruff 与 Git 差异检查通过。源码和 HEAD 在本轮全量期间保持冻结。
- 按运行时 `e797b23398a584c3a1c1cb2e06b971f86cd43339` 核查推送触发的 7 个作业。
  Linux 与 Windows 的 Python base、Python agent，Linux Deep Agents 专项及两套 Web
  作业全部在终态通过。
  [Python](https://github.com/mofengyan595/agenttest/actions/runs/43787214447) ·
  [Web](https://github.com/mofengyan595/agenttest/actions/runs/43787214331)

## 门禁与剩余边界

**GO，限受控 P3-B2。** 本轮样本中未观测到未授权业务请求、已确认写入重放、未知写入
自动重放、错误清理完成证明、重复负向义务计数或跨任务、跨部署错误恢复。真实模型完成
了 POST 后、DELETE 后以及完整发布的步骤级续跑。未知写入由隔离强制退出测试验证为
安全停止。全量与运行时 CI 达到工程门槛，结论适用于本轮批准计划和隔离部署。

没有外部系统的幂等协议时，`DISPATCHED` 到可信响应落盘之间的副作用依然未知。
系统保留预算和复核状态。未知读当前也保守停止。没有原计划批准及可信身份时，
不会跨场景借用旧资源执行新的清理。上一用例的创建、报告和未完成义务仍保留。
本轮使用本地 SQLite 和同任务目录锁，部署标识由运行方随目标实例轮换。
跨用例的既有证明规则可接纳后续已批准的只读缺失回读，要求同部署、同资源身份、
正确时序及无中间写入。这一路径的同身份与篡改情形由既有回归覆盖。
已启用步骤账本的用例沿用确定性的批准回读，选用模型决定补充探针的路径在这些用例内关闭。
应用和图的 checkpoint 无法对账、可信响应因敏感字段不能原样重建、
旧技术版本读到未知阶段时，均需要复核。
