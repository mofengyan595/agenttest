# 真实验收入口最终加固（第 6 轮）

日期：2026-11-10。基线：`a8d1600`（本轮开始时 `origin/master`，远端
Keyless `#42750826639`、Web `#42750826390` 均 success）。本轮只修复验收驱动器的三个安全与
可靠性问题，不改 Agent 架构、Focus/Design Prompt、rubric、评分器、冻结预算或执行器业务逻辑。

## 0. 边界

- 未调用任何真实付费模型；未向业务端点发送请求；未消费 M2/F1；真实
  `runs/rw-acceptance-v4b-20261109/` 下没有 `positions/`、`authorizations/` 或
  `execution.lock`。
- `freeze.json`、`authority.py` 的原始内容与哈希未改（`git diff` 为空）。
- 所有授权与消费测试使用临时目录、测试身份、MockTransport/桩服务。
- `WRITE_AUTHORITY` 保持 `NOT_GRANTED`。

## 1. 三个问题的根因

### P0-1：默认 `NOT_GRANTED` 不能阻止正式运行

`acceptance.py` 顶部把冻结声明复制成 `APPROVED = dict(WRITE_AUTHORITY,
operator_approval="GRANTED")`，`main()` 在*所有*模式下调用
`authority.startable(APPROVED)`。于是“是否可执行”只取决于进程内这份复制，而不是操作者的
独立决定：只要有人运行脚本，`NOT_GRANTED` 的默认值就被绕过。同时不存在任何绑定
case、执行提交、冻结摘要或预算的授权记录。

### P0-2：M2/F1 共用靶场却没有全局互斥

第 5 轮给每个位置独立的原子消费令牌，但两个 case 共用 `127.0.0.1:19097`。M2 与 F1 由两个
进程分别执行时，会同时启动 fixture、重置状态、争用端口；消费令牌只防止同一位置重跑，不能
防止两个不同位置同时占用同一靶场。

### P0-3：完成标记不与证据一致

`consumption.status()` 只要看到 `completed.json` 就返回 `COMPLETED`：JSON 损坏、字段缺失、
证据文件缺失或内容被改写都不会被发现；`complete()` 也没有记录证据摘要。截断或改写的记录
可能被当成 PASS 报告。

## 2. P0-1：独立操作者授权门禁

新增 [run_authorization.py](../scripts/rw_lifecycle/run_authorization.py)，运行目录下独立的
`authorizations/authorize-<case>-<head7>[-n].json` 审计记录。

### 记录内容（无凭据）

`schema`、`authorization_id`、`case`、`executing_head`（40 位）、`frozen_sha256`、
`operator`、`created_at`、`allow_model_calls`、`allow_loopback_writes`、
`budgets={max_requests,max_seconds,max_model_calls}`、`model_timeout`、`statement`、
`note`、`integrity`（对除自身外全部字段的 canonical SHA-256）。

### 独立性证明

1. **只能由操作者命令创建**：`acceptance.py authorize ...` 是独立子命令；`run()` 从不写
   `authorizations/`（测试断言被拒绝后目录仍不存在）。
2. **每个同意项都是必填显式参数**：`--operator`、`--allow-model-calls`、
   `--allow-loopback-writes`、`--max-requests`、`--max-model-calls`、`--max-seconds`、
   `--model-timeout`；缺预算报 `BUDGET_REQUIRED`，缺任一同意报对应错误。
3. **精确绑定**：case、执行 HEAD、`freeze.json` 字节摘要、冻结预算三元组、45 秒模型超时；
   `verify()` 逐项比较，任一不符即拒绝（`HEAD_MISMATCH`、`FROZEN_MISMATCH`、
   `BUDGET_MISMATCH`、`MODEL_TIMEOUT_MISMATCH`、`MODEL_CALLS_NOT_APPROVED`、
   `LOOPBACK_WRITES_NOT_APPROVED`）。
4. **不可覆盖、不跨 case 复用**：同一 `(case, head, frozen)` 再次授权返回
   `EXISTS_NO_OVERWRITE`，原文件字节不变；文件名与记录都绑定 case，M2 的记录不能用于 F1。
5. **不能靠默认值/环境变量绕过**：`--yes` 不存在；`AGENTTEST_APPROVED`、
   `AGENTTEST_OPERATOR_APPROVAL`、`AGENTTEST_YES`、`WRITE_AUTHORITY`、`CI` 全部设为
   `GRANTED` 仍得到 `RUN_AUTHORIZATION_MISSING:M2`。
6. **校验时机**：`run()` 在身份漂移、位置选择与消费状态检查之后，**在锁、fixture、重置、
   guard 和 claim 之前**校验授权；失败时零模型调用、零业务 HTTP、零消费（测试断言 fixture
   与任务步骤列表为空、`positions/` 不存在）。
7. **内部视图只在验证后设为 GRANTED**：`run()` 用
   `approved_view = dict(WRITE_AUTHORITY, operator_approval="GRANTED")` 调
   `authority.startable()` 仅发生在 `run_authorization.verify()` 成功之后；`main()` 中原来
   对所有模式生效的 `authority.startable(APPROVED)` 已删除。冻结声明本身仍是 GRANTED，它是
   冻结哈希的一部分，不是运行许可——两者现在被明确分开。
8. **记录不可被 run 修复或升级**：`verify()` 只读；损坏记录报 `TAMPERED`，不会自动重建。

## 3. P0-2：跨进程执行互斥锁

新增 [run_lock.py](../scripts/rw_lifecycle/run_lock.py)：

- `execution.lock` 上用系统级非阻塞锁：Windows `msvcrt.locking(LK_NBLCK)`，POSIX
  `fcntl.flock(LOCK_EX|LOCK_NB)`；不是临时文件存在性判断。
- `run()` 在**启动 fixture 之前**获取锁，覆盖重置、模型调用、HTTP、证据落盘、清理；解锁在
  `finally`。预检也使用同一把锁，因此预检不会在真实执行期间重置靶场。
- 第二个进程立即得到 `EXECUTION_LOCKED:<path>`，不等待、不创建自己 case 的消费目录
  （测试断言 `positions/` 不存在）。
- 进程崩溃时操作系统自动释放锁；代码**不会删除锁文件来“恢复”**。锁释放不代表位置可重跑：
  消费目录仍是唯一令牌（测试在崩溃后用新进程取得锁，但 M2 仍是 `RUNNING` 并拒绝）。
- M2/F1 仍是两个独立状态，锁只覆盖共享靶场，不合并消费状态。

## 4. P0-3：完成状态与证据一致性

`consumption.complete()` 现在：

1. 以 `newline="\n"` 写出**确切字节**的 `evidence.json`，计算其 SHA-256 并写入完成标记
   （`evidence_sha256`、`evidence_bytes`）。
2. 从证据本身派生 `requests`、`model_calls`、`unknown` 写入标记，避免参数与证据不一致。
3. 证据写失败 → `write_failure.json` + `PositionWriteFailed`；标记写失败同样保留消费。

`consumption.status()` 对 `completed.json` 校验：合法 JSON/对象、`schema`、`case`、
非空 `verdict`、`evidence=="evidence.json"`、`evidence_sha256` 为 64 位、`evidence.json`
存在且可解析、字节摘要一致、`case`/`verdict` 一致、`requests` 与 `metrics.requests` 一致、
`model_calls` 与 `product.model_calls` 一致、`unknown` 与 `product` 中的 UNKNOWN 一致。

- 任一不符 → `INTERRUPTED_OR_UNKNOWN`，附 `integrity` 原因列表；**绝不返回 PASS**。
- 旧格式无摘要 → `INTERRUPTED_OR_UNKNOWN`（`evidence-digest-missing`），不静默假定通过。
- 位置仍为已消费，任何重跑请求继续拒绝。
- 该改动只增强报告可信度，不改评分标准。

## 5. 本轮测试

新增 [test_rw_acceptance_authorization.py](../tests/test_rw_acceptance_authorization.py)
**15 passed**：无授权拒绝且零消费、M2 授权不能用于 F1、HEAD 不符、120 秒被拒、未同意模型
调用、未同意隔离写入、操作者/预算必填、记录不可覆盖、授权先于锁、真实子进程竞争锁、
锁忙时不消费不启动、崩溃不重放、环境变量不能授予、记录无凭据且授权不需要模型、无授权时
`status` 正常。

[test_rw_acceptance_consumption.py](../tests/test_rw_acceptance_consumption.py) 增加到
**30 passed**：原有 23 项（真实子进程竞争、真实 POST 201 后 `os._exit(9)`、截断 claim、
空 claim 目录、证据写失败、旧格式兼容等）加 7 项完整性：完成标记损坏、证据缺失、证据被改写、
旧格式无摘要、只有证据没有标记仍为 RUNNING、汇总暴露完整性、完成标记不可重写。

## 6. 全量验证

- `python -m ruff check .`：通过（0 错误）。
- `python -m pytest tests/test_rw_acceptance_consumption.py -q`：30 passed。
- `python -m pytest tests/test_rw_acceptance_authorization.py -q`：15 passed。
- `python -m pytest tests/test_rw_acceptance_preflight.py -q`：10 passed。
- `python -m pytest examples/regressions -q`：10 passed。
- `python scripts/check_full.py --jobs 4`：**2625 collected / 2610 passed / 0 failed /
  15 skipped**（805 s，`source_unchanged=true`、`exact_test_id_coverage=true`）。

## 7. 真实执行前的剩余阻塞项

1. **操作者必须针对最终 HEAD 单独授权**：授权绑定执行提交；本轮结束后仍有文档提交，所以
   授权必须在最后一次推送后的确切 HEAD 上创建，不能用旧提交的授权。
2. **操作者确认每 case 150 秒**（旧检查表文案 120 秒，冻结预算 150 秒）；授权命令必须显式
   写 `--max-seconds 150`，写 120 会被拒绝。
3. **操作者授予本次写入与模型调用许可**（`--allow-model-calls`、
   `--allow-loopback-writes`），并保持 `WRITE_AUTHORITY=NOT_GRANTED` 的默认值不被脚本修改。
4. 真实模型服务的可用性只能由那一次真实调用验证；预检 `READY` 不是付费或写入授权。

基础设施本身没有剩余阻塞项。

## 8. 首次真实 M2 的精确步骤

前置：工作树干净、`HEAD == origin/master`、CI green。

1. 预检（只读，不消费）：
   `python scripts/rw_lifecycle/acceptance.py preflight runs/rw-acceptance-v4b-20261109 --plan plan_v4`
   期望 M2/F1 九项 READY，`blocking=[]`，`position_unconsumed` 证据为
   `journal=UNCONSUMED`。
2. 操作者创建授权（示例，HEAD 必须替换为当前完整提交）：
   ```
   python scripts/rw_lifecycle/acceptance.py authorize runs/rw-acceptance-v4b-20261109 \
     --plan plan_v4 --cases M2 --operator "操作者姓名" \
     --allow-model-calls --allow-loopback-writes \
     --max-requests 12 --max-model-calls 2 --max-seconds 150 --model-timeout 45 \
     --head <当前完整 HEAD>
   ```
   输出包含记录文件名、case、HEAD、冻结摘要与预算；文件不可覆盖。
3. 唯一一次 M2 执行：
   ```
   python scripts/rw_lifecycle/acceptance.py run runs/rw-acceptance-v4b-20261109 \
     --plan plan_v4 --cases M2 --accept-code-change <HEAD 前 7 位>
   ```
   无论结果如何都不重跑 M2；异常时位置保持 `INTERRUPTED_OR_UNKNOWN`。
4. 复核：`python scripts/rw_lifecycle/acceptance.py status runs/rw-acceptance-v4b-20261109 --plan plan_v4`，
   检查 `positions/M2/` 下 claim/running/evidence/completed 或 interrupted/write_failure。
5. F1 必须在操作者另行授权后，用同样步骤单独执行。
6. 结束后确认 `WRITE_AUTHORITY=NOT_GRANTED`，不再新增授权。

## 9. 改动文件

- 新增 `scripts/rw_lifecycle/run_authorization.py`、`scripts/rw_lifecycle/run_lock.py`。
- 修改 `scripts/rw_lifecycle/acceptance.py`（授权子命令、授权校验、锁、移除全局 startable）。
- 修改 `scripts/rw_lifecycle/consumption.py`（证据摘要与完整性校验）。
- 新增/修改测试与本文档；`docs/progress.md` 追加本轮记录。
