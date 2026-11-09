# 系统性可靠性修复报告（2026-11-09，第二轮）

基线：master `2310f68`（上一轮专项交付点）。本轮修复提交：`ed45dc4`。
所有结论均来自本机实际运行；未新增付费模型调用，未重跑任何已消费位点（M2/F1 仍未执行）。

## 1. 根因与修复（file:line）

### P0-1 离线 CI 因读取不存在的 `.env` 失败

根因：`scripts/rw_lifecycle/acceptance.py` 把「配置校验」与「真实凭据」绑死。
`model_settings()` 无条件调用 `ModelSettings.from_env_file(ROOT/".env")`，`from_env_file()`
在文件缺失时抛 `FileNotFoundError`；仓库 `.env` 被 gitignore，因此 GitHub Actions 上
`tests/test_rw_lifecycle_next_round.py::test_approved_dry_run_uses_the_new_budget_and_only_reports_unconsumed_cases`
（子进程执行 `acceptance.py dry`）必然失败。本地因存在 `.env` 而通过，属于典型的
「本地绿、CI 红」。

修复（`acceptance.py:74-107`）：

- 新增 `model_env_path()`：优先 `AGENTTEST_MODEL_ENV`，否则 `ROOT/.env`，便于离线与 CI 复现。
- `model_settings(require_credentials=False)`：凭据文件存在时保持原有严格校验
  （超时必须等于批准的 45 s，否则 `MODEL_TIMEOUT_NOT_APPLIED`）；文件缺失且
  `require_credentials=True` 时以 `MODEL_CREDENTIALS_MISSING` 退出；否则返回显式离线占位
  身份（`model="offline-dry-run"`、`base_url="https://offline.invalid"`、无网络）。
- `model_identity()` 增加 `credentials: PRESENT | ABSENT_OFFLINE` 字段，freeze/dry 输出
  可审计地区分配置来源。
- `service()`（`:174`）在文件缺失时传 `model_env=None`，`configured_task_service` 本就按任务
  惰性解析设置，因此 dry 构建服务不需要凭据。
- `run()`（`:424-426`）在启动 fixture 前强制 `require_credentials=True`，并校验
  `MODEL_IDENTITY_DRIFT`，真实运行不会静默退化为离线身份。

回归证据（添加前该测试在无 `.env` 环境下失败）：

```
AGENTTEST_MODEL_ENV=<missing> python -m pytest tests/test_rw_lifecycle_next_round.py -q
6 passed
```

其中新增：`test_offline_dry_run_needs_no_credential_file`（无凭据 dry 通过、`ready: true`、
timeout 45）、`test_run_refuses_offline_without_a_model_identity`（`run` 在 fixture 前
以 `MODEL_CREDENTIALS_MISSING` 拒绝，未产生任何 HTTP）。

### P0-2 操作实例混淆导致假/自依赖与循环

根因：`plan_construction.construct_plan` 以 `operation_id` 匹配节点。当同一操作出现多个实例
（POST 重放、冲突重放、多身份读取）时，`earlier == later` 分支只能二选一：全连接产生自依赖，
或直接 `CONSTRUCTION_REQUIRED_ORDER_AMBIGUOUS` 误杀合法流程；真实循环与合法重复无法区分。

修复（`plan_construction.py:11-31`、`:119-135`）：

- 新增 `instance_groups(nodes, operation_id)`：以「请求身份」分组——参数（location/name/value/
  from_step/select 排序后）+ `role` + `policy_id`。完全相同的请求属于同一重放组，请求体变化
  开启下一组；组间按提案顺序串联，**任何节点都不会依赖自身**。
- 同操作有序要求改为组链：相邻组之间逐对 `depend(..., 'TEXT_ORDER')`；单组多实例按提案顺序
  串行；单实例直接跳过（不可能自依赖）。
- 不删除、不「修复」任何边；真实矛盾仍以 `CONSTRUCTION_DEPENDENCY_CYCLE` 报出，并附
  `construction.cycle` 与逐边 `reason`。

回归证据（新增两条失败反例，修复前分别报 AMBIGUOUS 与误判循环）：

```
pytest tests/test_plan_construction.py -q
16 passed
```

- `test_repeated_create_instances_chain_in_proposal_order_without_a_self_edge`：201 → 200 重放
  → 409 冲突三段式，`c1 < c2 < c3`，`dependency_edges >= 2`。
- `test_same_operation_by_two_identities_stays_two_distinct_instances`：owner 读取与匿名读取
  共享同一 `id` 绑定，仍是两个实例且顺序确定。
- `test_repeated_operation_order_pairs_instances_instead_of_chaining_every_node`：同参数重复读取
  被串行；与节点级链反向的矛盾仍报 `CONSTRUCTION_DEPENDENCY_CYCLE`。

### P1-5 执行预算与收尾预算混用

根因：`executor.run` 的 `asyncio.timeout(run_timeout)` 包住了断言执行、调查与
`_cleanup_owned`。断言超时会先耗尽预算，导致**已批准、未发送**的删除永远不执行；
`TimeoutError`/异常分支根本不调用清理，已创建资源被遗留在靶场。

修复：

- `executor.py:__init__`：`self.cleanup_timeout = max(5.0, min(30.0, run_timeout / 2))`，与执行预算
  分离且有界。**刻意不放进 `TargetConfig`**：该模型参与 `target_config_sha256` 与项目注册指纹，
  第一版加上字段后全量回归出现 152 个失败（`PROJECT_VERSION_MISMATCH`、记录哈希断言），
  因此改为执行器本地派生值，避免强制重注册所有已冻结项目（见 §4）。
- `executor.py:254-259`：执行预算结束后，清理在**独立**预算中运行。
- `executor.py:285-296`：超时/异常分支仍尝试一次有界清理。
- `executor.py:316-345`：`_bounded_cleanup`（独立预算、只记录不改写原失败）与
  `_cleanup_after_failure`（新建客户端、同样的安全门）。
- `executor.py:378` + `report.py:216`：`cleanup_error` 记录 `CLEANUP_TIMEOUT`、
  `CLEANUP_ERROR` 或 `CLEANUP_FAILED:<异常类名>`（只记类名，不记可能含目标数据的消息）。
- 取消（CancelledError）仍不清理；未知写结果仍被 `_cleanup_owned` 原有门禁挡住，不重放。

回归证据（`tests/test_resource_lifecycle.py`，新增 2 条）：

```
pytest tests/test_resource_lifecycle.py -q
15 passed
```

- `test_execution_budget_timeout_still_runs_the_approved_cleanup`：读步骤 2 s、`run_timeout=0.4`
  → 报告 `TEST_INFRA_ERROR/RUN_TIMEOUT`，请求序列 `POST, GET, DELETE`，资源 `CLEANED`。
- `test_cleanup_has_its_own_timeout_and_never_rewrites_the_failure`：删除慢于
  `cleanup_timeout` → 原 `ASSERTION_FAILED` 保留，`cleanup_error == CLEANUP_TIMEOUT`，资源未标记已清理。
- 既有 `test_unknown_create_never_guesses_deletes_or_replays`（含取消）与
  `test_incomplete_cleanup_keeps_evidence_and_is_not_replayed`（failure/timeout/cancel）全部继续通过。

### P2 长文本切分破坏结构

根因：`request_atoms` 每 300 字符硬切，可能切在 JSONPath、引号内的值或方括号列表中间，
导致下游无法解析 `select` 或把值切成两半，且切分后的 `start/end` 仍声称是原文片段。

修复（`request_requirements.py:199-237`、`:274-289`）：

- 新增 `bounded_span(text, start, limit=300)`：扫描到 300 字符处，若仍处于未闭合引号或括号中，
  在最多 40 字符的前瞻内补齐闭合；否则原样返回。
- `request_atoms` 改用 `bounded_span` 递增切分，保持 `start/end` 与原文偏移对齐、顺序单调。

回归证据（`tests/test_request_requirements.py`，新增 2 条）：

```
pytest tests/test_request_requirements.py -q
42 passed
```

- `test_bounded_span_finishes_a_quoted_value_instead_of_cutting_it`
- `test_long_request_text_keeps_paths_values_and_order_intact`：`$.data.items[0].long_field_name_with_more_chars`、
  `"a,b;c"`、`[first, second, third]` 均完整，引号成对，偏移单调，单段 ≤ 340 字符。

## 2. 验证状态（真实输出）

| 检查 | 命令 | 结果 |
|---|---|---|
| 定向回归 | `pytest tests/test_executor.py tests/test_plan_construction.py tests/test_rw_lifecycle_next_round.py tests/test_resource_lifecycle.py tests/test_request_requirements.py tests/test_planning_witness_recovery.py -q` | 100 passed |
| 规划相关 | `pytest tests/test_product_focus.py tests/test_focus_references.py tests/test_product_lengths.py tests/test_planning_witness_recovery.py tests/test_product_sources.py -q` | 71 passed |
| 静态检查 | `python -m ruff check .` | All checks passed |
| 全量离线 | `python scripts/check_full.py --jobs 4` | 2529 collected / **2514 passed / 0 failed / 0 errors / 15 skipped**，墙钟 765.5 s（HEAD `1df962b`），`exact_test_id_coverage: true`，`successful: true` |
| 示例回归 | `python -m pytest examples/regressions -q --basetemp=runs/pytest-regressions` | 10 passed（HEAD `1df962b`） |

## 3. 全量检查记录

第一次全量（提交 `f55cc2e` 之前，`TargetConfig` 曾新增 `cleanup_timeout` 字段）结果：
2529 collected / 2362 passed / **152 failed** / 15 skipped，墙钟 698.5 s。失败集中在
`test_regression`(29)、`test_evidence_evaluation`(24)、`test_project`(14)、
`test_petclinic_chain`(11)、`test_pocketbase_acceptance`(11) 等 24 个文件，根因统一为
`PROJECT_VERSION_MISMATCH` 与记录哈希断言——新增字段改变了 `target_config_sha256`。
该字段已撤回（改为执行器派生值），随后逐文件复跑 **213 passed / 0 failed**（90 + 122 +
65 中含跳过 1），再做第二次全量：

```
# f55cc2e（派生预算，报告仍序列化 cleanup_error）
python scripts/check_full.py --jobs 4
{"successful": true, "exact_test_id_coverage": true, "tests": 2529, "passed": 2514,
 "failures": 0, "errors": 0, "skipped": 15, "wall_seconds": 733.81}

# 1df962b（最终 HEAD：cleanup_error 仅在收尾异常时序列化）
python scripts/check_full.py --jobs 4
{"successful": true, "exact_test_id_coverage": true, "tests": 2529, "passed": 2514,
 "failures": 0, "errors": 0, "skipped": 15, "wall_seconds": 765.52}
```

15 个跳过为环境相关（LangGraph/可选依赖等）既有跳过，与本轮改动无关。

## 4. 兼容性影响

- `model_identity()` 新增 `credentials` 键：现有 freeze/dry 消费方按字典读取，未知键不破坏
  解析；历史 `freeze.json`/`dry.json` 未被修改。
- `TargetConfig` **未**新增字段：`cleanup_timeout` 是执行器派生值，因此项目注册指纹、
  历史 `target_config_sha256` 与已冻结项目全部保持不变（首版加字段导致 152 个回归失败，
  已撤回，见 §3）。
- `RunReport.cleanup_error` 为可选字段，仅在异常/超时时出现；正常报告序列化不变。
- 计划构造：同操作多实例现在会**成功**构造并串行，而不是一律报 AMBIGUOUS。这是行为放宽，
  但只放宽到「模型自己提案的相同请求之间加顺序」，不发明新的业务步骤；真实矛盾仍然失败。
- 切分：`request_atoms` 的段界可能比之前晚至多 40 字符，`start/end` 仍指向原文；
  依赖固定 300 字符切分的测试已按新契约更新。

## 5. 未解决风险与下一任务

1. **Focus/Design 规划稳定性**仍未系统性收敛：本轮只修了「证据片段/重复读取」两类证人误判
   （提交 `6f1119b`）与实例级顺序（本轮），`FOCUS_REQUIREMENT_GAP_WITHOUT_EVIDENCE`、
   `MODEL_SCHEMA_TYPE_OR_CONSTRAINT` 的误报仍缺少覆盖真实失败样本的回归。
2. **v4 位点 M2/F1 未执行**：冻结已完成（`runs/rw-acceptance-v4b-20261109/`，timeout 45、
   12 请求、150 s、2 次模型调用），但按约定不新增付费调用，需操作者明确批准后才能跑。
3. **残留资源记录**：本轮保证「已批准删除在有界预算内仍会尝试」，但没有跨会话的残留资源
   清单/对账任务；长时间中断后仍靠 `resource_lifecycle` 状态人工核查。
4. **授权边界**：已审计 `task_api.py`（任务/报告均按 `owner` 过滤，报告还需 run_id 属于该任务
   自己的 summary；会话 cookie + CSRF）与 `task_store.py`（`SELECT ... WHERE id=? AND owner=?`），
   未发现本地可越权路径。项目目录是「单操作者全局」语义，多用户部署需要额外的项目级归属，
   属于部署约束而非本轮缺陷。
5. **失败报告信息量**：`cleanup_error`、`construction.cycle`/逐边 reason、诊断 `fields` 已补齐；
   「阶段 + 源码子句 + 操作/步骤 + 是否已发 HTTP + 副作用 + 下一步」的统一失败摘要仍未落地。
