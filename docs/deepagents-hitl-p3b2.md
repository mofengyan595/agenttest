# P3-B2 人工审批与可信恢复

基线为 `8aeeb60d30178364e3017bd4f46592dbbef01b75`，其运行时代码为 `e797b23`。
开始时工作区只有未跟踪的既有 `.venv-lock/`。依赖锁和本地安装均为 Deep Agents
0.7.35、LangChain 1.4.10、LangGraph 1.2.20、SQLite checkpointer 3.1.3。

## 如何启用

```powershell
$env:AGENTTEST_HITL = "1"
$env:AGENTTEST_DURABLE_EXECUTION = "1"
$env:AGENTTEST_DURABLE_TARGET_EPOCH = "<当前隔离部署标识>"
# 可选。按空白分隔的合约 operationId；省略时复核任何包含业务写入的批次。
$env:AGENTTEST_HITL_OPERATIONS = "createParcel"
```

任务服务中的 Deep Agents 产品路径默认关闭人工审批。启用后，运行需要原生 SQLite
Checkpointer。审批触发点设在工具内整组方案完成预检之后、发起第一个 HTTP 之前。
锁定版本的 `interrupt_on` 在模型提出调用后即可暂停，但此时 `run_test_group`
只有组 ID 和设计备注，具体的 Policy、绑定和请求仍未生成。因此在原工具中使用
LangGraph `interrupt()`，读取 SQLite 检查点中对应的中断 ID，再通过
`Command(resume={interrupt_id: review_id})` 恢复原线程。限定动作来自服务端决策记录。
[Deep Agents HITL](https://docs.langchain.com/oss/python/deepagents/human-in-the-loop) ·
[LangGraph Interrupt](https://docs.langchain.com/oss/python/langgraph/interrupts) ·
[LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)

## 状态、审计与执行

- 与原 `TOOL` 恢复快照同事务保存审批记录。记录带任务、线程、图检查点、调用、
  分组、目标部署和原授权摘要、Policy 和 IR 摘要、已预检请求及资源绑定摘要、
  模板路径、证据引用、时间、版本和决定历史。工具尚未执行时，运行标识使用任务 ID；
  未知写复核使用原 HTTP 用例的实际 run ID。计划引用和业务请求参数用摘要呈现。
- `GET /api/tasks/{id}/reviews` 返回清单及恢复条件，`GET …/reviews/{review_id}`
  查询详情，`POST …/reviews/{review_id}/decision` 接收 `decision`、
  `version` 和可选 `note`。查询复用任务所有者，决定还须通过原有 CSRF 校验。
  当前操作员身份使用任务会话的所有者标识。备注只写入审批审计。
- `WAITING_APPROVAL → APPROVED` 保存决定后使用原 `/resume`。恢复重建原设计，
  重跑 Validator 并比较方案摘要，随后由原预算与 Executor 执行。原始确认回执和
  派发意图保护中断后再次进入的节点。SQLite 事务约束提交和取消竞争。
  `WAITING_APPROVAL → REJECTED` 安全终止任务并保留待测义务。取消使用原 `/cancel`。
  如果进程在审批记录落盘后、图中断落盘前退出，决定入口会因缺少原生中断而阻塞，
  任务仍可取消。
- 写入账本中存在 `DISPATCHED` 且无可信响应时，生成 `REVIEW_REQUIRED`。
  可归档至 `NEEDS_REVIEW`。原未知派发继续计入保守用量。符合条件的负向 POST
  可以批准一次 `request_verification`。条件包括原预检产生了同关联键的后置 GET，
  该 GET 无需登录、轮询或重试，前置读取有可回放的零计数响应且剩余 HTTP 预算充足。
  独立观测经原 Executor 记录报告和派发账本。原写入结果与清理状态保留待复核。
- 当前覆盖负向 POST 的已批准关联键核实。成功创建但缺少关联键的 POST、以及
  依赖原生命周期身份的未知 DELETE，保持待复核并支持归档。

## 真实 ecnu-max 验收

各位置使用独立靶场与证据目录 `.eduwork/p3b2-live/`。跨进程恢复沿用位置自己的
部署标识。源码哈希 `46e2bb4ffe738b564fc00347a2209cec7d283d6c5a3cb6d7bbdd3ca62fa768aa`。
该哈希覆盖 `src/` 和 `scripts/` 的 Python 源码。下表的 HTTP 取自靶场事件，
排除靶场控制请求。

| 位置 | HTTP | 模型调用 | 已知 Token | 覆盖和结果 |
| --- | ---: | ---: | ---: | --- |
| 批准同资源生命周期 | 0 → 3 | 5 → 7 | 24,599 → 38,111 | 3/3，严格完成 |
| 拒绝同资源生命周期 | 0 → 0 | 5 → 5 | 24,234 → 24,234 | 0/3，待复核 |
| POST 响应落盘前强制退出 | 0 → 1 | 5 → 5 | 24,212 → 24,212 | 0/3，写入未知，库存 1 |
| 完整发布，按预检批次批准 | 0 → 70 | 5 → 19 | 28,389 → 142,151 | 25/31，模型服务 HTTP 500，未严格完成 |

正常生命周期为 POST 201、同一创建 ID 的 DELETE 204 与 GET 404，各一次。
审批前的 `approve/phase-00.json` 记录 `run_test_group` 调用
`call_5d56d8c1747b4721b853c194`、检查点 `1f1e1674-b3cc-6666-8016-6ac2b2e37e11`
和按序预检的三个操作。新进程的 `approve/phase-01.json` 保存同一审批决定与用例证据。
拒绝后工具没有执行，模型没有重新规划被拒操作。未知位置在收到 POST 201 后、
SQLite 保存可信响应之前退出，从靶场库存观察到资源已创建。工作进程只见原
`DISPATCHED`，恢复入口继续阻塞，操作员可归档。该位置缺少可靠的已批准同身份读取。

发布验收按 `createParcel` 匹配预检批次，因此实际批准了六次。18/24 项负向策略各
执行一次。其余六项均属于 `replaceParcel`。9 个创建资源均有清理凭据，另外 10 个
负向 POST 有无副作用凭据，目标库存为 0。可用性接口的真实 503 保留为
`ASSERTION_FAILED`。最后一次模型请求返回 500，未回报 Token。
P3-B1.1 的独立发布位置为 31/31、94 次 HTTP、24 次模型调用和 214,731 Token。
两轮覆盖和停止位置不同，保留各自原始结果。

真实运行结束后复核了凭据检测和只读核实预算。最终源码哈希为
`3a10c4fd6048d74876a859b0c212690986aef255a458c75b0d842fc000d7c56b`。
最终源码恢复审批记录落盘前逐字段检查凭据，并约束核实为无需登录的项目及原任务
剩余时限。真实模型证据对应上文运行时哈希，后续门槛改动使用离线验证。

## 离线验证

- 阶段性定向测试 75 通过，含原生 SQLite 检查点、批准后强制退出再恢复、拒绝、
  权限、重复决定、竞争决定、未知写只读核实、原 HTTP 恢复和假 204。
- 最终源码针对未知写只读核实和跨进程审批复跑 3 项，全部通过。
- 屏蔽可选 Agent 依赖的本地测试 8 通过。前端类型检查通过，Vitest 80 通过。
- Ruff 和 `git diff --check` 通过。
- 本地 `pip check` 报告现有环境缺少 Deep Agents 声明的 `langchain-anthropic`
  和 `langchain-google-genai`。本轮复用了现有 ecnu-max 路径，依赖锁无变动。
- 前一轮回归期间更新报告，触发了历史组合规划测试的工作区冻结检查。
  重跑时冻结整个参与校验的工作区。原有 `.venv-lock/` 只在测试进程的 Git
  配置中排除，目录、仓库和全局配置均保留原样。
- 冻结全量覆盖 2,873 项。原始结果为 2,855 通过、15 跳过、1 个既有 xfail、
  2 失败，前后工作区摘要一致。两个失败均为报告发布测试的进程队列超时，
  临时分组启动器在 Windows 子进程中重复进入 pytest。标准命令隔离复跑同组
  4 项全部通过。原始失败日志保留在 `.eduwork/p3b2-verified/`。
- 原始模型、检查点和 HTTP 证据保留在各验收目录。发布失败位置保留
  `release/phase-06.json`，未知结果同时保留 `unknown/interrupted.json` 和
  `unknown/phase-02.json`。

## 当前门槛

**NO-GO**。进入 P3-B3 前，补齐真实失联写入后已批准读取的验收样本、未知 DELETE
的同身份核实，以及保持 31 项原范围的完整发布验收。继续执行和清理需要原授权和可信证据。
