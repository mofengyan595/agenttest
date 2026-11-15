# P3-B1 Focus 与 Durable Execution

最终运行时为 `c96052c61c22d220a7cba4721582e2b79804979f`，在
`54c385b636a8e1d11feac573c887b9484d717c00` 的持久化实现上闭合发布范围来源校验。
基线为 `7187803`。本轮使用隔离靶场验证工具边界恢复，记录每个位置的原始结果。

## Focus 复现与修复

- **C-r1** 首次原始输出把策略样例 `not-an-object` 当成用户精确输入，触发
  `FOCUS_INPUT_TEXT_CHANGED`。纠错输出使用了形如 `{"key":"meta","value":"string"}`
  的对象，违反 LiteralValue 的结构约束。批准的正负覆盖说明通过来源 ID 引用，选择
  对应批准检查时 `exact_value` 和适用的 `exact_length` 限制为 null。解码器独立验证，
  原文有具体值的其他子句依旧沿原授权和来源路径处理。
- **D-release** 的策略覆盖说明没有命中原归一化条件，模型在发布主请求和说明各放了
  一个创建动作。新规则识别完整发布请求中的闭合覆盖说明，由服务端沿原 `source_id`
  生成无动作的约束，保留原句和已批准的负向范围。运行时继续接收历史 Focus 记录，
  归一化后的回归覆盖了保留两个明确创建实例的情况。
- 首次新发布验收仍在 Focus 遭遇 `FOCUS_REQUIREMENT_WITNESS_MISSING`，纠错又触发
  `FOCUS_NEGATIVE_LENGTH_INVALID`。该位置保留为 0/31。修复后，从同一份原始输出
  去掉服务端已拥有的说明槽位可以离线解码，含未授权精确取值的纠错输出仍被拒绝。

## 原生恢复与可信执行

本地复核了已安装 API。Deep Agents 0.7.35 的 `create_deep_agent(checkpointer=...)`
传递给现有图。LangChain 1.4.10 继续承担模型调用、中间件与工具。LangGraph 1.2.20、
checkpoint 4.3.1、官方 SQLite saver 3.1.3、aiosqlite 0.22.1 和 sqlite-vec 0.1.9
锁定在产品依赖。图用任务 ID 作为 `thread_id`，以 `durability="sync"` 落盘。
受控暂停用原生 `interrupt_after=["tools"]`，同一图用 `astream(None, ...)` 续跑。
[LangGraph 持久化](https://docs.langchain.com/oss/python/langgraph/persistence) ·
[SQLite Checkpointer 3.1.3](https://pypi.org/project/langgraph-checkpoint-sqlite/3.1.3/)

| 存储 | 内容 |
| --- | --- |
| `tasks.checkpoints.sqlite3` | Deep Agents 消息与工具状态、框架计数、节点进度和原生检查点 ID |
| TaskStore `product_durability` | `agenttest.durable.v1`、任务行与目标版本组合摘要、阶段、检查点 ID、状态和证据摘要、重试最早时间 |
| 产品恢复状态 | 已校验 Planning IR、批准检查、剩余义务与工具记录、预算状态、累计 Token 和调用数、活动时间 |
| 原有报告与模型账本 | 已确认的 HTTP 尝试和断言、资源清理凭据、模型派发与用量结算。恢复继续使用原任务和报告 ID |

保存检查点时检查配置密钥和目标凭据，反序列化采用 LangGraph 的受限类型。任务完成后用
原生接口清理该线程的图记录。保留 TaskStore、报告和模型账本供审计。SQLite 文件大小为
磁盘占用，删除线程后保留的空页仍计入。中断和取消记录保留供人工核对。

恢复先比较项目审批、目标部署标识、任务状态与快照摘要，再比较原生检查点 ID。
TaskStore 状态变更原子认领，原进程锁阻止另一个进程同时打开同一任务目录。重新排队
仍走 `TaskService → ProductTasks → Deep Agents`，业务请求继续经过原有的
Policy、Validator、Executor 和预算守卫。报告显示重复请求证据，已测量的负向义务
由现有范围账本排除。业务批次中途退出时保留请求意图与已落盘报告，拒绝从上一个
图检查点重发。若已见未知写结果，恢复入口返回待复核原因。

## 启用与接口

启动现有任务服务前明确启用。隔离目标重新部署时更新部署标识。

```powershell
$env:AGENTTEST_DURABLE_EXECUTION = "1"
$env:AGENTTEST_DURABLE_TARGET_EPOCH = "<当前隔离部署标识>"
```

- `GET /api/tasks/{task_id}` 读取任务状态，`GET /api/tasks/{task_id}/recovery`
  查询恢复条件和检查点，`POST /api/tasks/{task_id}/resume` 对符合条件的原任务排队。
- `POST /api/tasks/{task_id}/cancel` 取消当前或等待恢复的任务。原时间线和报告接口
  继续提供前后证据。沿用原 owner 和写接口访问控制。
- 靶场脚本用 `AGENTTEST_DURABLE_PAUSE_AFTER_TOOLS=1` 控制安全边界。日常部署默认
  关闭持久化，基础安装可以继续使用确定性运行模式。
- Focus 和主 Agent 遇到符合重试条件的网关拒绝时，每次调用最多重试两次。遵守
  `Retry-After`，等待超过当轮窗口时保存可恢复停止点。Focus 在进入图前的拒绝
  由 TaskStore 保存，图内拒绝由两处存储联动恢复。未知模型用量仍须先核对。

## 真实 ecnu-max

按位置串行执行。每次恢复使用新的 Python 进程，同一位置使用原隔离靶场进程，
每次启动通过健康回执核对靶场实例。耗时从 worker 调用提交或恢复算到该阶段终态，
包含当段模型和 HTTP，排除进程启动和靶场准备。

| 位置和边界 | 业务 HTTP 累计 | 模型调用累计 | 已知 Token 累计 | SQLite 字节数 | 阶段耗时 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 生命周期，清理后暂停 | 3 | 5 | 24,597 | 204,800 | 7.66 秒 |
| 生命周期，恢复后完成 | 3 | 7 | 37,595 | 311,296 | 30.90 秒 |
| 首次发布，Focus 拒绝 | 0 | 2 | 29,148 | 0 | 10.16 秒 |
| 最终发布，首批负向完成后暂停 | 20 | 7 | 44,386 | 352,256 | 18.58 秒 |
| 最终发布，恢复后完成 | 94 | 21 | 177,894 | 1,101,824 | 202.12 秒 |

- 生命周期在 `54c385b` 实测同一资源的 POST 201、DELETE 204、GET 404 各一次。
  清理后恢复增加 2 次模型调用、12,998 Token 和 0 次业务 HTTP。运行报告逐字段相同，
  恢复后确实调用 `read_test_report` 和 `get_test_coverage`。
- 完整发布在 `c96052c` 实测。正向完成并且首批 4 项负向已测时覆盖 11/31。
  恢复增加 74 次业务 HTTP、14 次模型调用和 133,508 Token，覆盖最终达到 31/31。
  6/6 份原报告逐字段一致，7 个负向批次共 24 项策略各一次。15 个新资源均有已批准
  清理凭据，10 项集合负向 POST 有无副作用证明。中断和最终靶场库存均为 0。
  服务可用性仍真实返回 503，保留 `ASSERTION_FAILED`，任务严格完成而
  `checks_passed=false`。
- 两个成功位置的已确认写重放、负向义务重复、未授权业务请求和未知写结果各为 0。
  共三个位置的模型账本记录 30 次实际 HTTP 200，未出现真实 429 或未知用量。
  429 的退避和续跑证明来自确定性故障注入。

原始证据在 `.eduwork/p3b1-live/{delete,release,release-c96052c}/`。各目录保留
`manifest.json`、每阶段报告、模型原始输出和靶场事件。

以下命令可复现专项和新发布位置。发布脚本读取现有模型配置，并新建独立靶场。

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_product_durability.py tests/test_product_focus.py tests/test_task_completion.py
.\.venv\Scripts\python.exe scripts/run_p3b1_resume.py --out .eduwork/p3b1-repro-release --kind release --port 30660
.\.venv\Scripts\python.exe scripts/check_full.py --jobs 6 --output runs/p3b1-repro-full
```

## 离线测试与交付

- 两条初始 Focus 回归由失败变为通过。原始运行时定向 102 通过、1 个既有 xfail，
  Executor、生命周期、Web、CLI、MCP 和 Planning IR 兼容组合 107 通过、3 跳过。
  最终 Focus 定向 54 通过、1 个既有 xfail。跨进程检查点、拒绝损坏状态、429、
  取消与并发竞争等补充专项 8 通过。
- 冻结 `c96052c` 运行 `scripts/check_full.py --jobs 6`。2,852 个测试 ID 均有
  终态，2,836 通过、15 跳过、1 个既有 xfail，0 失败、0 错误。源码快照一致，
  Ruff 和 Git 差异检查通过。完整结果在 `runs/p3b1-full-c96052c/result.json`。
  六批总墙钟时间为 1,106.92 秒。此前 `54c385b` 的本地全量因新 Focus 问题停止，
  归档留存。
- 远端首个运行时 `54c385b` 的 7 个 CI 作业通过。
  [Python](https://github.com/mofengyan595/agenttest/actions/runs/43688669849) ·
  [Web](https://github.com/mofengyan595/agenttest/actions/runs/43688669819)
- 最终运行时 `c96052c` 已推送，按该 SHA 核查 5 个 Python 作业和 2 个 Web 作业，
  全部通过。
  [Python](https://github.com/mofengyan595/agenttest/actions/runs/43707648973) ·
  [Web](https://github.com/mofengyan595/agenttest/actions/runs/43707648963)

## 保留边界和下一里程碑

应用支持已确认的工具批次边界和明确的网关拒绝边界。任意强杀后若只有
`ACTIVE` 记录，将保留未确认状态供复核。已收到 POST 回执、同一工具还未到
DELETE 或缺失回读时的步级续跑仍缺实现和真实跨进程验收。目标部署标识依赖部署方
随实例轮换，靶场验收额外核验了当前实例。设计工具内部的模型故障沿用既有设计失败路径。

**暂不进入 P3-B2。** 下一门禁是在保留 Executor 派发意图的前提下验证工具内部中断，
覆盖已取得可信写响应和写结果未知两种退出，核对剩余清理义务。
