# 隔离读写生命周期测试准备（2026-11-08）

本文件只做**准备**，不启动真实写入验收。目标是一个完全本地、可重置、每次运行独立命名空间的
写夹具，复用现有执行器与审批架构（`TargetConfig` / `resource_creations` /
`resource_lifecycles` / `validate_lifecycle_authority` / `lifecycle_plan` /
`CreationJournal` / `LifecycleJournal`），不接触生产、不接触 CourseInsight、不接触任何真实业务服务。

## 1. 范围与禁止项

- 目标地址固定 `http://127.0.0.1:19097`，只允许 loopback；`startable()` 拒绝任何非 `127.0.0.1`。
- 环境标记 `isolated`，`allow_mutations=true`，`isolated_confirmed=true` 三者缺一即拒绝启动。
- 不新增写权限、不改产品运行时、不放宽任何既有安全闸门；本阶段只产出夹具、审批声明、评分器与
  确定性负控。
- 真实写入验收（拿到操作员批准后）才允许发送 POST/PATCH/PUT/DELETE。

## 2. 可写操作范围（唯一授权集合）

| 操作 | 方法 | 路径 | 身份 | 期望状态 | 变更 | 幂等键 |
| --- | --- | --- | --- | --- | --- | --- |
| `createItem` | POST | `/items` | writer（`author`/`editor`） | 201 | 是 | 必须 |
| `getItem` | GET | `/items/{itemId}` | 所有者或 `auditor` 只读 | 200 | 否 | — |
| `updateItem` | PATCH | `/items/{itemId}` | 仅所有者 | 200 | 是 | — |
| `replaceItem` | PUT | `/items/{itemId}` | 仅所有者 | 200 | 是 | — |
| `deleteItem` | DELETE | `/items/{itemId}` | 仅所有者 | 204 | 是 | — |
| `listItems` | GET | `/items` | 只读身份 | 200 | 否 | — |

身份只有四个：`author`、`editor`（可写），`auditor`（只读），`outsider`（越权对照）。
所有者之外一律 403，匿名 401；跨命名空间一律 404。资源 id 形如 `itm-0001`，由夹具生成。

## 3. 授权要求（未批准前不可启动）

`scripts/rw_lifecycle/authority.py` 的 `WRITE_AUTHORITY["operator_approval"]` 当前是
`NOT_GRANTED`，`startable()` 会抛 `WRITE_AUTHORITY_NOT_GRANTED`。正式启动前需要操作员逐条确认：

1. 批准上表 6 个操作作为唯一可写范围；
2. 确认目标只是 `127.0.0.1:19097` 隔离夹具，环境标记 `isolated`；
3. 确认每次运行的命名空间前缀与重置方式（`POST /__namespace`、`POST /__reset`，均需控制令牌）；
4. 确认清理策略：每次创建必须配对一次 DELETE，并以回读 404 作为清理证据；
5. 确认故障注入只用于补偿测试，且结果未知的写请求一律不重放；
6. 确认预算上限（12 次请求 / 120 秒 / 2 次模型调用）与停止条件。

本阶段**没有**、也不会自行扩大上述授权。

## 4. 每个用例的初始态、期望态、终态与清理

| 用例 | 初始 | 期望 | 终态 | 清理验证 |
| --- | --- | --- | --- | --- |
| 完整生命周期 | 空命名空间 | 201 → 200 → 200 → 200 → 204 → 404 | 资源已删除 | DELETE 204 + 回读 404 |
| 幂等重放 | 空命名空间 | 201，重放 200 且 id 相同 | 资源已删除 | 同上 |
| 越权对照 | 空命名空间 | 403、403，所有者仍可 204 | 资源已删除 | 同上 |
| 重复提交（未复用键） | 空命名空间 | 201、201 两条资源 | 有意留下 | 不计清理（负控） |
| 未清理 | 空命名空间 | 只有 201 | 留下资源 | `cleanup_unverified` |
| 提交后故障、无补偿 | 空命名空间 | 201，PATCH 500 但状态已变 | 留下资源 | `compensation_missing` |
| 提交后故障、有补偿 | 空命名空间 | 500 → DELETE 204 → 404 | 资源已删除 | 补偿 + 清理双证据 |
| 命名空间隔离 | 命名空间 A | 1 条资源 | — | A 内资源在 B 中 404 |
| 重置 | 已写入 1 条 | `POST /__reset` | items 0、audit 0 | 直接回读状态 |

状态一律用夹具自己计算的 `state_sha256`（`namespace + 未删除资源` 的规范化 SHA-256）表示，
不做任何猜测性重建。

## 5. 清理与失败恢复策略

- **先建后改**：所有读写地址必须绑定到同一次运行内新建的资源（`LIFECYCLE_OWNED_RESOURCE_REQUIRED`），
  禁止读写别人的 id。
- **创建必配清理**：`lifecycle_plan` 要求创建集合与删除集合完全相等，否则
  `LIFECYCLE_PREPARATION_AND_CLEANUP_REQUIRED`。
- **清理证据**：DELETE 返回 204 且随后 GET 返回 404 才算清理完成；否则计 `leftovers` 并阻止验收。
- **提交后故障**：注入 `after_commit` 时，客户端看到 500 但状态已改变；必须由补偿 DELETE 收尾，
  评分器用「故障行序号 < 删除行序号」证明补偿顺序。
- **未知结果不重放**：与产品 `CreationJournal`/`LifecycleJournal` 一致，写请求结果未知时保留资源与
  审计、标记待人工核实，绝不自动重试。
- **兜底重置**：`POST /__reset`（需令牌）清空资源、幂等表与审计，回到空状态；仅用于测试编排，
  不作为验收证据。

## 6. 裁决与证据

`scripts/rw_lifecycle/lifecycle.py` 只读夹具自己的审计行（方法、身份、状态码、路径、幂等重放标记、
故障标记、请求前后状态哈希）与用例声明，输出：

- 指标：`creates`、`replays`、`updates`、`deletes`、`denials`、`identity_violations`、
  `unapproved_operations`、`namespace_violations`、`cleanup_verified`、`leftovers`、
  `compensation_verified`；
- 结论：`PASS` / `FAIL` / `NEEDS_REVIEW`，以及逐条 `findings`（原因 + 证据）。

可检测的缺陷类：未授权操作、越权写入、期望拒绝缺失或多余、创建计数不符（重复提交）、
状态与期望不符、终态不符、清理未验证、资源残留、补偿缺失、命名空间越界、故障未注入。

## 7. 确定性负控（真实 loopback HTTP，0 次模型调用）

`python scripts\rw_lifecycle\preflight.py runs\rw-lifecycle-preflight.json`：
先跑授权闸门（未批准声明必须拒绝启动，四种不安全声明逐条拒绝），再在 `127.0.0.1:19097`
上跑 10 个用例，每个用例断言期望结论。

| 用例 | 期望 | 实际 |
| --- | --- | --- |
| `positive_full_lifecycle` | PASS | PASS（201/200/200/200/204/404） |
| `idempotent_replay` | PASS | PASS（201/200/204/404） |
| `wrong_identity_and_resource_scope` | PASS | PASS（403/403/204/404） |
| `duplicate_submission_without_reused_key` | FAIL | FAIL（`create_count_mismatch`） |
| `missing_cleanup_leaves_resource` | FAIL | FAIL（`cleanup_unverified`） |
| `post_commit_fault_without_compensation` | FAIL | FAIL（`compensation_missing`、`cleanup_unverified`） |
| `post_commit_fault_with_compensation` | PASS | PASS（500/204/404） |
| `namespace_a_creates_one_item` | PASS | PASS |
| `namespace_isolation` | PASS | PASS（跨命名空间 404） |
| `reset_restores_empty_state` | PASS | PASS（items 1 → 0，audit 0） |

7 个 PASS / 3 个 FAIL 的混合证明评分器既接受正确生命周期、也能在同一目标上逐类报错。

## 8. 复用既有审批架构的证明

`tests/test_rw_lifecycle_preparation.py`（12 项）证明本夹具走的是产品真实闸门，而不是自制旁路：

- `build_generic_project` 接受该声明：`allow_mutations=True`、`environment="isolated"`、
  `resource_creations={"createItem"}`、`resource_lifecycles=[createItem → deleteItem]`；
- `validate_lifecycle_authority` 通过；关闭 `allow_mutations` 后报 `*_AUTHORITY_INVALID`；
- `lifecycle_plan` 接受「创建 → 读 → 改 → 替换 → 删除」的完整计划，并返回创建/清理配对；
- 6 个不安全计划逐条被拒：读取未创建的资源、绑定到错误选择器、身份不匹配、
  删除带请求体、变更数据含隐藏引用、删除用错操作。

## 9. 准备阶段发现（不修改产品，只如实记录）

1. ~~**必填头参数暂不支持**~~（2026-11-08 已修复，见
   `docs/product-fix-partial-review-and-headers-20261108.md`）：`Idempotency-Key` 现在可以声明为必填
   头参数，契约解析、审批作用域、输入校验、计划绑定与执行器发送全部支持，且大小写不敏感、值在证据中
   脱敏。夹具仍同时接受 header / body `idempotencyKey` / query `idempotency_key` 三种写法，
   优先使用真实服务常用的 header 传输。
2. **无产品侧「回读验证」**：删除后的 404 回读由夹具与评分器保证；产品的 `LifecycleJournal` 记录
   清理结果，但不回读校验。若要把清理回读变成产品能力，需要另立里程碑。
3. **审计在夹具侧**：状态哈希、幂等重放、故障标记都由夹具产出，产品只提供执行与日志；因此本阶段
   的验收证据是「夹具审计 + 产品执行记录」两份独立材料，不合并成单一真源。

## 10. 是否可以正式启动写入验收

**准备就绪，但不自行启动。** 夹具、审批声明、评分器、确定性负控和既有闸门复用均已通过；
必填头参数支持已补齐，幂等键可以直接走真实服务常用的 `Idempotency-Key` 头。
正式启动仍需要第 3 节 6 条批准。在批准之前，`startable()` 会一直拒绝，不会发送任何写请求。
真实写入产生的资源、清理与补偿结果，以及任何 UNKNOWN，必须逐条保留，不得重放。

**2026-11-09 首次验收已执行**（六项批准已获操作员授权）：三条首次位置全部进入真实
`ecnu-max` 的 `web_contract_v2` 模型设计路径，但都在 focus 阶段被产品校验拒绝，目标 HTTP
请求为 0、资源残留 0、UNKNOWN 0；判定 T1 FAIL（模型规划）、T2 NEEDS_REVIEW（夹具表达力）、
T3 FAIL（模型规划）。详见 `docs/rw-lifecycle-acceptance-20261109.md` 与
`docs/evidence/rw-lifecycle-20261109/`。三条位置已消费，不得重放；后续改进需使用新的位置。

**2026-11-09 离线修复与下一轮准备**：三条诊断的根因已修复（批准的预期结果策略、受控多批准
取值、原文顺序见证派生、受控写操作负向权限声明），评分器增加冲突与批准拒绝白名单；下一轮
需求集见 scripts/rw_lifecycle/plan_v2.py（开发样本与最终样本分离）。本阶段未授予写入，
WRITE_AUTHORITY 保持 NOT_GRANTED；重新执行首次验收前需按第 3 节再次批准。
详见 docs/rw-lifecycle-focus-fixes-20261109.md。
