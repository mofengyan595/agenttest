# M2/F1 冻结位置的原子消费与崩溃安全（第 5 轮，2026-11-10）

## 0. 结论与边界

本轮是验收基础设施的 P0 可靠性修复，不涉及规划逻辑、Prompt 调优或真实模型调用。

- 新增 `scripts/rw_lifecycle/consumption.py`：以目录 `positions/<case>/` 的 `mkdir` 作为唯一
  原子消费令牌，实现 `UNCONSUMED → CLAIMED → RUNNING → COMPLETED`（异常为
  `INTERRUPTED_OR_UNKNOWN`）的单向状态机。
- 重写 `acceptance.run()`：一次命令只执行一个位置；顺序固定为
  “冻结一致性校验 → 原子消费 → 标记 RUNNING → 任务/HTTP → 证据 → 完成标记 → 只读汇总”，
  消费登记早于任何模型调用或业务请求。
- 每个 case 独立持久化：`claim.json`、`running.json`、`evidence.json`、`completed.json`、
  `interrupted.json`（或 `write_failure.json`），全部独占创建且 `fsync`；不再以
  `result.json` 作为唯一或最终证据。
- 新增 `status` 只读模式与 `summary-<n>.json` 汇总视图；崩溃后可从记录判断已消费，
  不自动续跑。
- 兼容历史：`result.json`（含 `requests=0`、模型失败、损坏文件）与冻结计划中的
  `CONSUMED_POSITIONS`（M1）都视为已消费，绝不重放。
- 本轮**未**调用模型、**未**消费 M2/F1、**未**发送业务请求、**未**改写 `freeze.json`、
  rubric、预算或评分器；`WRITE_AUTHORITY` 保持 `NOT_GRANTED`。

## 1. 原漏洞复现

修复前的 `run()` 只写一次总报告，且防重放条件为：

```python
if any(row.get("requests") for row in done.get("results", [])):
    raise SystemExit("POSITION_ALREADY_CONSUMED_NO_REPLAY")
```

真实驱动把请求数写在 `row["metrics"]["requests"]`、`summary["requests"][case]` 和
`product["requests"]`；`row["requests"]` 从不存在。最小离线复现
（`tests/test_rw_acceptance_consumption.py::test_the_old_result_shape_never_reached_the_old_guard`）：

```python
done = {"results": [{"case": "M2", "metrics": {"requests": 4}}]}
assert any(row.get("requests") for row in done["results"]) is False   # 旧检查放行
assert consumption.legacy_consumed(folder, "M2") is True              # 新检查拒绝
```

由此产生的六个后果与本轮对应修复：

| 原问题 | 复现/证据 | 修复 |
| --- | --- | --- |
| 已执行 case 可再次运行 | 上面的最小复现；`row["requests"]` 恒为 `None` | 消费目录存在即拒绝 |
| Focus 失败、零 HTTP 也可重复调用模型 | `test_a_zero_request_focus_failure_still_consumes_the_position` | `requests=0` 仍写 `completed.json` |
| M2 之后无法独立判断 F1 | `test_m2_and_f1_are_independent_positions` | 每 case 一个目录 |
| 请求后、写报告前崩溃丢失状态 | `test_a_crash_after_a_real_post_keeps_the_position_consumed` | 先登记后执行 |
| 重跑覆盖 `result.json` | 新代码不再写 `result.json`；`summary-<n>.json` 独占 | 独占创建 |
| 两进程竞争同一位置 | `test_many_processes_racing_for_one_position_have_at_most_one_winner`（6 进程） | `mkdir` 原子性 |

## 2. 状态机与持久化布局

```
<run folder>/
  freeze.json                    冻结记录（只读，本轮未改）
  execution-manifest-<head>.json 每次预检/运行的全局清单（独占）
  positions/<case>/
    claim.json        CLAIMED     独占；目录本身即令牌，先于任何模型/HTTP
    running.json      RUNNING     独占；写入后才创建任务
    evidence.json     （证据）    独占；完整 run_case 证据
    completed.json    COMPLETED   独占；verdict/requests/model_calls/unknown
    interrupted.json  INTERRUPTED_OR_UNKNOWN   独占；异常原因，首次解释不被覆盖
    write_failure.json INTERRUPTED_OR_UNKNOWN  独占；claim/证据写盘失败
  summary-<n>.json               只读汇总视图（独占，可重新生成，不覆盖）
```

状态由文件推导，不存可变字段；`status()` 只读。只有 `UNCONSUMED` 可运行。派生优先级：

1. 冻结计划 `CONSUMED_POSITIONS` → `HISTORICAL_CONSUMED`
2. `completed.json` → `COMPLETED`
3. `interrupted.json` / `write_failure.json` → `INTERRUPTED_OR_UNKNOWN`
4. `running.json` → `RUNNING`（进程可能已死，仍算已消费）
5. 目录存在 → `CLAIMED`（即使 `claim.json` 缺失或损坏）
6. 只有历史执行证据 → `LEGACY_CONSUMED`
7. 无目录、无历史 → `UNCONSUMED`

可靠性措施：所有记录 `open("x")` 独占写 + `flush` + `fsync(file)`，目录项在 POSIX 上
`fsync(dir)`；任何记录损坏或写失败都不删除目录、不回退状态。`run()` 在 `claim` 与
`start` 之间、`start` 与任务之间、任务与 `complete` 之间崩溃，结果都是“已消费”。

## 3. 消费登记字段（`claim.json`）

`schema`、`case`、`attempt_id`（uuid4 hex）、`claimed_at`（本地时区）、`frozen_head`、
`frozen_digest`（整份 `freeze.json` 的 sha256）、`executing_head`、`manifest`（全局执行清单
文件名）、`budgets`、`identity_digest`（规范化模型身份的 sha256，**不含** key）、`initial`、
`state`。没有真实凭据；测试断言整份记录不含 `api_key`/`sk-`。

## 4. `run()` 新顺序

1. 读取 `freeze.json`，断言冻结时工作树干净（`FREEZE_REQUIRES_CLEAN_TREE`）。
2. `model_identity(require_credentials=True)`；身份漂移先于一切选择检查
   （`MODEL_IDENTITY_DRIFT`）。
3. 只允许一个位置：`ONE_POSITION_PER_COMMAND`（避免批量执行中证据混淆）。
4. 冻结计划已消费位置：`CONSUMED_POSITION_REFUSED:<case>`。
5. 消费状态必须为 `UNCONSUMED`，否则 `POSITION_ALREADY_CONSUMED_NO_REPLAY:<case>:<state>`。
6. 启动隔离夹具并重置冻结初始态；`guard()` 完成冻结一致性校验并写全局清单
   （`FROZEN_ARTIFACT_DRIFT`、`EXECUTION_REQUIRES_CLEAN_TREE`、`CODE_DRIFT_UNRECORDED`）。
7. `consumption.claim(...)`：原子取得该位置唯一一次机会；两个进程只有一个成功。
8. `consumption.start(...)`；随后才创建服务并提交任务（第一次模型调用发生在这里之后）。
9. 任务结束：先写 `evidence.json`，再写 `completed.json`；写盘失败 →
   `POSITION_EVIDENCE_WRITE_FAILED`，位置保持已消费。
10. 任务/HTTP 异常：写 `interrupted.json` 后原样抛出；不重试、不续跑。
11. 重新生成 `summary-<n>.json`（只读视图）；**不再写 `result.json`**。

## 5. 新旧数据格式兼容

`legacy_consumed()` 读取旧 `result.json`，任一信号即视为已执行：`verdicts`、`requests`
（含 0）、`model_calls`、`results[].verdict/requests/metrics.requests/product.requests/
audit/checks`。旧文件损坏（JSON 不可解析）时保守地把所有计划 case 视为已消费。
`CONSUMED_POSITIONS` 中的 M1 永远 `HISTORICAL_CONSUMED`。新记录优先，旧记录不会被改写；
`legacy_evidence()` 只读。

预检清单的 `position_unconsumed` 与 `no_replay_of_unknown_writes` 现在都由该视图判定，
历史 `result.json` 有请求数（即使 0）也会 `BLOCKED`，与运行时判断一致。

## 6. 离线反例与结果

`tests/test_rw_acceptance_consumption.py`（23 passed）：

- 独占与字段：`test_a_fresh_claim_is_exclusive_and_records_the_attempt`
- 独立：`test_m2_and_f1_are_independent_positions`、
  `test_completing_one_case_never_rewrites_the_other_case`
- 零 HTTP Focus 失败：`test_a_zero_request_focus_failure_still_consumes_the_position`
- 模型失败：`test_a_failed_model_call_leaves_an_interrupted_position`
- 崩溃（真实子进程）：`test_a_crash_after_the_claim_before_the_model_call_blocks_a_retry`、
  `test_a_crash_after_a_real_post_keeps_the_position_consumed`（子进程起真实 HTTP 服务并
  POST 201，随后 `os._exit(9)`）
- 不完整登记：`test_a_claim_directory_without_any_file_still_consumes`、
  `test_a_truncated_claim_record_still_counts_as_consumed`
- 最终证据写失败：`test_a_failed_final_evidence_write_keeps_the_position_consumed`
- 并发（6 个真实子进程）：`test_many_processes_racing_for_one_position_have_at_most_one_winner`
- 冻结漂移/脏树在登记前拒绝：`test_the_frozen_gate_refuses_before_a_claim`
  （`FROZEN_ARTIFACT_DRIFT:BUDGETS_CHANGED`、`EXECUTION_REQUIRES_CLEAN_TREE`，均无
  `positions/` 产生）
- 历史兼容：`test_a_legacy_result_file_consumes_every_case_it_mentions`、
  `test_a_corrupt_legacy_file_blocks_every_planned_case`、`test_m1_stays_historical_consumed`
- 只读汇总：`test_a_summary_is_regenerated_without_any_result_json`、
  `test_status_mode_is_read_only_and_needs_no_freeze`、
  `test_the_status_command_reads_records_without_starting_anything`
- `run()` 顺序：`test_run_claims_before_the_task_and_persists_per_case_evidence`（断言任务
  开始时 `claim.json` 与 `running.json` 已存在、`completed.json` 尚不存在）、
  `test_run_refuses_two_positions_in_one_command`、
  `test_run_refuses_a_legacy_consumed_position_before_the_fixture`、
  `test_run_refuses_a_historical_position`、`test_the_old_result_shape_never_reached_the_old_guard`
  （最小复现：旧检查读 `row["requests"]` 恒为 None，新检查拒绝）

## 7. 冻结预算文案不一致（显式披露）

冻结声明 `APPROVAL_CHECKLIST` 文案写“每 case 不超过 120 秒”，冻结预算
`freeze.json.budgets.max_seconds` 为 **150**。本轮不改冻结声明、rubric、预算或评分器
（改动会改变冻结哈希），实际执行上限以 `budgets` 为准：

| 项目 | 冻结值 |
| --- | --- |
| 每 case 请求 | 12 |
| 每 case 秒 | 150（不是文案中的 120） |
| 每 case 模型调用 | 2 |
| 模型超时 | 45 |
| 输出/输入 tokens | 4096 / 20000 |

**未来正式运行前，操作者必须明确确认“每 case 150 秒”这一实际预算。**
预检 `READY` 只表示冻结一致性与基础设施就绪，不构成新的付费授权或写入授权。
如需预算确认机制，应作为冻结记录之外的独立审计记录（例如运行目录下的
`budget-confirmation-<head>.json`），不得改动 `freeze.json`。

## 8. 本轮未执行

- 未调用真实模型；未执行 M2/F1；未创建任何真实位置的消费目录。
- 未修改 `freeze.json`/rubric/题目/预算/评分器；未覆盖历史证据。
- 未进行生产写入或真实业务请求。

## 9. 验证

- `ruff check .`：通过。
- `pytest tests/test_rw_acceptance_consumption.py -q`：**23 passed**。
- `pytest tests/test_rw_acceptance_preflight.py tests/test_rw_lifecycle_next_round.py
  tests/test_rw_lifecycle_preparation.py -q`：**32 passed**。
- `pytest examples/regressions -q`：**10 passed**。
- 全量离线：`python scripts/check_full.py --jobs 4` = **2603 collected / 2588 passed /
  0 failed / 15 skipped**（990 s，`source_unchanged=true`、`exact_test_id_coverage=true`）。
- 干净提交 `ee45a33` 上的预检：
  [preflight-ee45a33.json](evidence/rw-acceptance-v4b-preflight-20261109/preflight-ee45a33.json)
  （M2/F1 均 9/9 READY，`position_unconsumed` 证据为 `journal=UNCONSUMED`、`consumed=["M1"]`，
  `blocking=[]`，仅 `SOURCE_CHANGED` 告警）与
  [execution-manifest-ee45a33.json](evidence/rw-acceptance-v4b-preflight-20261109/execution-manifest-ee45a33.json)
  （frozen `d8eeae1` → `ee45a33`，38 个文件：20 新增 / 18 修改，工作区干净，
  预算 12/150/2）。

## 10. 首次执行 M2 的精确安全步骤

1. 在干净提交上运行预检，确认 `M2 READY`（`preflight-<head7>.json` 九项、`blocking=[]`）。
2. 操作者显式确认：每 case **150 秒**、12 请求、2 次模型调用；`WRITE_AUTHORITY` 由操作者
   临时授予，运行结束必须回到 `NOT_GRANTED`。
3. 执行：
   ```bash
   python scripts/rw_lifecycle/acceptance.py run runs/rw-acceptance-v4b-20261109 \
       --plan plan_v4 --cases M2 --accept-code-change <head7>
   ```
4. 成功或失败都检查 `positions/M2/{claim,running,evidence,completed|interrupted}.json`；
   任何异常都**不得**重跑 M2，只能读取证据并人工调查。
5. M2 结束后再单独运行 F1（另一个位置、另一次 `--cases F1`）；两者互不影响。
6. 运行 `status` 生成只读汇总；核对 `WRITE_AUTHORITY=NOT_GRANTED`。
