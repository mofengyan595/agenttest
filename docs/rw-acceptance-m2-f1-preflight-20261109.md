# M2/F1 真实模型验收预检与冻结一致性核查（2026-11-09，第 4 轮）

本轮范围：**只读**冻结记录与历史证据，新增审计文件；**未调用付费模型、未执行冻结的 M2/F1
位置、未发送任何业务写请求、未重新冻结、未修改冻结题目/rubric**。`WRITE_AUTHORITY` 保持
`NOT_GRANTED`，`CONSUMED_POSITIONS=("M1",)` 不变。

## 0. 结论摘要

| 检查项 | 结果 | 证据 |
| --- | --- | --- |
| 冻结记录一致性（题目/顺序/状态码/rubric/授权范围/预算） | **通过** | `runs/rw-acceptance-v4b-20261109/execution-manifest-*.json`，`artifact_digests` 六项 frozen=executing |
| 代码版本差异是否可审计 | **是**（frozen `d8eeae1` → 本轮提交；改动逐文件记录） | manifest `source.changed_files`、`frozen_head`/`executing_head` |
| M2/F1 位置是否未消耗 | **是**（仅 `M1` 已消耗） | `freeze.json.requirements.consumed_positions=["M1"]`、无 `result.json` |
| Focus/Design 实例绑定离线复核 | **1 类真实缺陷已修**（跨子句集合顺序 → 虚假依赖环），最小 prompt/schema + 早期诊断 | 第 3 节；`docs/evidence/rw-instance-replay-20261109/replay.json` |
| M2/F1 无成本预检 | **M2 READY / F1 READY**（9/9 项） | `preflight-<head>.json` checklist |
| 阻塞缺陷 | **0** | 第 5 节 |
| 遗留风险 | 4 条（模型填写质量等） | 第 6 节 |
| 预期调用/请求上限 | 每 case 2 次模型调用 / 12 请求 / 150 秒；合计 4 / 24 / ≤300 秒 | `freeze.json.budgets` + authority 声明 |

进入真实验收的结论：**技术前置条件已满足，但仍需一次新的显式付费授权**；本轮的 READY
只表示“若授权，现有代码与冻结记录一致、目标与初始状态可复现、证据完整”，不表示模型一定能通过。

## 1. 冻结一致性核查：机制与结果

### 1.1 旧机制的缺口

- `acceptance.freeze()` 会**直接覆盖**已有 `freeze.json`：任何一次重跑都能悄悄改写“原始冻结记录”。
- `acceptance.run()` 只检查 `MODEL_IDENTITY_DRIFT` 与 `POSITION_ALREADY_CONSUMED_NO_REPLAY`；
  若有人改了 `plan_v4.RUBRIC`、题目文本、预算或授权声明，运行仍会继续，失败后也无法证明
  “跑的是不是冻结的那一份”。
- 没有执行清单，事后无法把“某次运行”对应到具体 HEAD、源码摘要与差异文件列表。

### 1.2 新增判定（`scripts/rw_lifecycle/execution_gate.py`）

`compare(frozen, current)` 对冻结记录与“即将运行的代码视图”逐项比较，输出**封闭原因码**：

| 原因码 | 含义 | 是否阻塞 |
| --- | --- | --- |
| `FROZEN_DECLARATION_MISSING` | 冻结记录缺少必需字段（删字段不能绕过检查） | 阻塞 |
| `PLAN_RECORD_CHANGED` / `PLAN_FILE_CHANGED` | 计划名或 `plan_v4.py` 文件摘要变化 | 阻塞 |
| `CASES_CHANGED`（含逐题明细） | M2/F1 题目文本、期望方法/状态码、冲突/清理声明变化 | 阻塞 |
| `RUBRIC_CHANGED` | rubric 内容变化（按规范 JSON 摘要比较） | 阻塞 |
| `CONSUMED_POSITIONS_CHANGED` / `POSITION_LIST_CHANGED` | 已消耗列表或 dev/final 位置变化 | 阻塞 |
| `AUTHORITY_CHANGED` / `AUTHORITY_DECLARATION_CHANGED` | 授权声明摘要或内容变化 | 阻塞 |
| `CONTRACT_DOCUMENT_CHANGED` / `CONTRACT_SETUP_CHANGED` | 目标契约文档/夹具初始状态定义变化 | 阻塞 |
| `SCORER_CHANGED` | 评分器（`lifecycle.py`）变化 | 阻塞 |
| `BUDGETS_CHANGED` | 请求/时间/模型调用预算变化 | 阻塞 |
| `MODEL_IDENTITY_DRIFT`（附字段名） | 模型身份漂移；凭据内容从不参与比较 | 阻塞 |
| `TARGET_CHANGED` / `TARGET_NOT_ISOLATED` | 目标地址变化或非隔离/非 loopback | 阻塞 |
| `INITIAL_STATE_CHANGED` | 重置后的初始状态摘要与冻结值不同 | 阻塞 |
| `SOURCE_CHANGED` / `EXECUTING_TREE_DIRTY` | 代码升级/工作区未提交 | **仅告警并记录**；运行模式另见下 |

运行模式（`acceptance.guard(..., enforce_code_change=True)`）额外要求：

- 工作区必须干净（`EXECUTION_REQUIRES_CLEAN_TREE`）：运行的字节必须能由某个 HEAD 唯一指认；
- 代码差异必须被显式命名（`CODE_DRIFT_UNRECORDED:<head7>`）；命名后写入**不可覆盖**的执行清单。

`freeze()` 现在在 `freeze.json` 已存在时直接 `FREEZE_RECORD_EXISTS_NO_OVERWRITE`；
`preflight` 报告同样独占（`PREFLIGHT_RECORD_EXISTS_NO_OVERWRITE`）；执行清单用
`open("x")` 独占创建，同名冲突时追加 `-2`、`-3`，**从不覆盖**。

### 1.3 冻结产物对照（M2/F1 v4b）

| 产物 | 冻结值（`freeze.json`） | 本轮运行视图 | 结果 |
| --- | --- | --- | --- |
| 源代码 | `src_sha256=11cc34951dedff26…`，head `d8eeae121…` | 本轮提交（见 manifest） | 差异已记录，非阻塞 |
| 题目集合摘要 | `f2ba78ab754ca6b3…` | 相同 | 一致 |
| `plan_v4.py` | `4c1d775c75632abf…` | 相同 | 一致 |
| rubric | `670d8ee465a0c7e9…` | 相同 | 一致 |
| 授权声明 | `3eb8ebc31685cabe…`（6 个操作、`NOT_GRANTED`→运行期临时批准） | 相同 | 一致 |
| 目标契约 | doc `00960ecf41c5c271…`，setup `1a3cacfd0a6fc3e6…` | 相同 | 一致 |
| 评分器 | `d7d7d2d7d061f292…` | 相同 | 一致 |
| 模型身份 | `ecnu-max` / `chatecnu` / `https://chat.ecnu.edu.cn/open/api/v1` / 45s / 4096 / 20000 | 相同（`.env` 存在，凭据只记录 `PRESENT`） | 一致 |
| 目标与初始态 | `http://127.0.0.1:19097`，`isolated`，初始 `10718ffae8dc6926…` | 相同 | 一致 |
| 预算 | 12 请求 / 150 秒 / 2 次模型调用 | 相同 | 一致 |
| 位置 | final `["M2","F1"]`，consumed `["M1"]` | 相同 | 一致 |

冻结题目要求的 HTTP 序列（本轮未执行，仅核对）：

| case | 序列（同一 `itemId`） | 期望状态 |
| --- | --- | --- |
| M2 | POST `/items` → GET `/items/{id}` → DELETE `/items/{id}` → GET `/items/{id}` | 201 → 200 → 204 → 404 |
| F1 | POST → GET → PATCH → PUT → DELETE → GET | 201 → 200 → 200 → 200 → 204 → 404 |

## 2. 代码版本差异与执行清单

执行清单（每个预检/运行各一份，独占）：

```
runs/rw-acceptance-v4b-20261109/execution-manifest-<head7>.json
```

字段：`frozen_head`、`executing_head`、`accept_code_change`、`allowed`、`blocking`、
`warnings`、`source`（两侧 `src_sha256`/`status`/`changed_files`）、`artifact_digests`（六项
frozen/executing）、`model`（drift 明细）、`target`（地址/隔离/初始态）、
`acceptance_conditions`（rubric 检查、预算、位置、consumed、清理/命名空间/幂等规则、初始态）。

本轮相对冻结提交 `d8eeae1` 的代码差异（全部在本轮验证，均不改变冻结题目/rubric/预算）：

| 文件 | 变更 | 对验收的影响 |
| --- | --- | --- |
| `src/agenttest/executor.py` | 新增 `_refuse_bodyless_sources`：成功码 ⊆ {204,304} 的来源不得提供参数 | 纵深防御；DSL 层已先拒绝（`POLICY_DEPENDENCY_MISMATCH`） |
| `src/agenttest/product_design.py` | `INSTANCE_BINDING_PROMPT`、`design_prompt`、`strip_instance_bindings` | 仅当同一操作重复时追加段落；历史 wire 字节不变 |
| `src/agenttest/product_schema.py` | 两个绑定槽增加简短描述 | 帮助模型理解槽位；不影响必填性（服务端门控） |
| `src/agenttest/product_focus.py` | Focus prompt 增加“跨子句必须按实例排序、否则虚假环”段落 | 直接针对历史失败形态 |
| `src/agenttest/request_requirements.py` | `RequestOrder` 四槽描述；新增 `order_ambiguity` 与 `FOCUS_REQUIREMENT_ORDER_INSTANCE_MISSING` | 把虚假环前移为可操作的“缺实例”诊断，零 HTTP |
| `scripts/rw_lifecycle/execution_gate.py` | 新增一致性闸门/清单/清单检查 | 本轮核心机制 |
| `scripts/rw_lifecycle/acceptance.py` | `guard`、`preflight` 模式、`freeze` 禁覆盖、git 解码修复 | 运行前拦截；只新增审计文件 |
| `scripts/rw_lifecycle/instance_replay.py` | 离线重放归档回复 | 预检证据，不参与运行时 |

`git diff --name-status frozen..head` 由 `changed_files()` 读取并写入清单（上限 200 条），
因此“代码升级”不是口头声明，而是逐文件可复核的记录。

## 3. Focus/Design 实例绑定离线复核

### 3.1 绑定在链路上的位置

- 实例身份 = `(requirement_source_id, requirement_action_index)`；
- 两个槽只在 `product_design._needs_instance_bindings(context)`（同一操作重复出现）时进入
  Design wire；否则 `strip_instance_bindings` 移除，历史请求字节保持一致；
- Focus 侧 `RequiredOrder` 的 `earlier_source_id/later_source_id/earlier_index/later_index`
  用于把顺序边钉到具体子句实例；本轮为四槽补了中文描述。

### 3.2 五条必须理解的关系

| # | 关系 | 代码事实 | 离线证据 |
| --- | --- | --- | --- |
| a | 删除前后两次 GET 是**两个实例** | 实例按 `(source_id, index)`；同一操作重复才要求绑定 | 历史 T1V3 把两次读拆成 `request_3/4`（前）与 `request_8/9`（后），但未填索引 |
| b | 所有请求共用 CREATE 的 id | 回读参数 `from_step="c"`（`MODEL_REFERENCE`），与顺序边（`TEXT_ORDER`）分开记账 | M2/F1 冻结题面与 E2E 断言一致；DELETE 204 不作为来源 |
| c | DELETE 204 不能当 id 来源 | DSL 先拒绝（来源策略无选择器），executor 再拒绝（成功码 ⊆ {204,304}） | `test_executor_bodyless_source.py`、`test_instance_delivery_e2e.py` 新反例 |
| d | 实例顺序 ≠ 数据引用 | `edge_reasons` 区分 `TEXT_ORDER` 与 `MODEL_REFERENCE` | 构造统计中两类边分别出现，互不替代 |
| e | 合法请求不得被虚假环拒绝 | 新增 `order_ambiguity`：集合顺序自相矛盾时，先报“缺实例”而不是“依赖环” | 归档 T1V3 重放：9 子句、18 条顺序边、**7 节点环**；新诊断定位到 `request_2` |

### 3.3 历史真实模型证据（全部零目标请求）

| 位置 | 代码 | 结果 | 失败码 | 调用/令牌 |
| --- | --- | --- | --- | --- |
| v4a M1 | 旧代码 | NEEDS_REVIEW（已消耗） | `FOCUS_REQUIREMENT_WITNESS_MISSING` | 1 次 / 11750 |
| v3 T1V3 | `96f574b` | NEEDS_REVIEW | 设计后校验；`requirement_map` 9 子句、无实例索引 | 2 次 / 12889+11356 |
| v3 T2V3 | `96f574b` | NEEDS_REVIEW | `FOCUS_REQUIREMENT_WITNESS_MISSING`（`request_2.actions`） | 1 次 / 13570 |
| v3 T3 | `96f574b` | NEEDS_REVIEW | 同类，`request_4.before` | — |

归档重放（`scripts/rw_lifecycle/instance_replay.py`，离线）：

- T1V3：9 子句、`explicit_indices=false`、18 条顺序边、环含 7 个节点；
  新诊断 `ambiguity={clause: request_2, earlier: 2, later: 4}`；
- T2V3：10 子句、5 条顺序边、3 节点环；新诊断 `ambiguity={clause: request_9}`；
- T3 归档无 Focus 正文（失败在更早阶段），不计入重放。

结论：历史失败不是“执行器不稳”，而是**规划输出把重复操作当成一个集合**；旧代码要等到构造
阶段才以依赖环或歧义拒绝，新的 Focus 诊断在**消耗 Design 调用之前**给出可操作原因。

### 3.4 本轮最小修复

1. Focus prompt 段落：同一操作跨多个子句出现时，每个出现是不同实例；跨子句的 `before`
   必须用四个槽指明实例；未被文本排序的实例不得互相排序；数组位置不决定身份，数据引用才决定。
2. `RequestOrder` 四槽描述（“该 before 边前/后一个实例所在的子句”“子句内第几次调用”）。
3. `order_ambiguity()`：对每个 `required` 子句的顺序边求实例集合，建“目标←来源”图并做拓扑消解；
   若成环且存在**未指明两侧实例、且至少一侧跨多个子句**的边，返回该子句，由
   `validate_requirements` 抛 `FOCUS_REQUIREMENT_ORDER_INSTANCE_MISSING`
   （`fields=["requirement_map.<clause>.before"]`，`construction` 带两侧实例）。
   已显式指明实例的真矛盾不受影响，仍在构造期报 `CONSTRUCTION_DEPENDENCY_CYCLE`。

为什么这不是“改断言/降低标准”：诊断**前移**且更具体，合法且有实例的请求照常通过
（`test_an_addressed_pair_over_repeated_reads_is_not_ambiguous`、单次操作用例），
冻结题目与 rubric 未动一行。

### 3.5 Design 侧

- `INSTANCE_BINDING_PROMPT` 只在重复操作时追加：一个子句一次请求、不同子句不同实例、
  实例身份不由数组位置决定、“DELETE 返回 204 无正文，永远不能提供 id”、书写顺序与数据引用
  是两种依赖；
- 槽位 nullable + `required` 不含它们（服务端按上下文门控），避免对非重复请求扩大 wire；
- 新增测试：`tests/test_product_instance_prompt.py`（4 条，含 Focus prompt/schema 描述）、
  `tests/test_focus_order_ambiguity.py`（3 条，含归档 T1V3 重放）。

## 4. M2/F1 预检清单（无成本）

`python scripts/rw_lifecycle/acceptance.py preflight runs/rw-acceptance-v4b-20261109 --plan plan_v4`

| 清单项（每个 case） | M2 | F1 | 证据 |
| --- | --- | --- | --- |
| 位置未消耗 | READY | READY | consumed=`["M1"]`，无 `result.json` |
| 代码差异可审计 | READY | READY | manifest 固化 frozen/executing head 与逐文件差异 |
| 模型身份一致 | READY | READY | 六字段一致，凭据仅记录存在性 |
| 目标获批且隔离 | READY | READY | `127.0.0.1:19097`、`isolated`、loopback |
| 写操作在授权范围 | READY | READY | 6 个操作与声明逐项一致 |
| 预算与冻结一致 | READY | READY | 12 / 150 / 2，超时 45 |
| 初始状态一致 | READY | READY | 重置后 `10718ffa…` 与冻结值相同 |
| 无未知写重放 | READY | READY | 无历史请求；`run()` 见请求即拒绝 |
| 证据完整 | READY | READY | focus/findings/audit/state/product 均落盘 |

预检报告：`runs/rw-acceptance-v4b-20261109/preflight-<head7>.json`（独占创建）；
报告只启动本地夹具并做一次重置，**不发送业务请求、不调用模型**。

## 5. 阻塞缺陷

**无。** 九项检查全部 READY，冻结产物无漂移，M2/F1 均未消耗且初始态可复现。

## 6. 遗留风险（不阻塞预检，但影响真实验收的解释力）

1. **模型填写质量未验证**：prompt/schema 已把实例规则写得可执行，但真实模型是否稳定填写
   `requirement_source_id`/`requirement_action_index` 仍只能由一次真实调用回答；失败会消耗位置。
2. **位置消耗不可逆**：M2/F1 各只有一次机会；失败后不得重放、不得 re-freeze、不得改 prompt 重试。
3. **跨用例顺序仍由用例序列约束**：报告读者需理解“用例内顺序由构造决定，用例间由调度决定”。
4. **授权声明文案与冻结预算有轻微不一致**：`APPROVAL_CHECKLIST` 写“120 秒”，冻结预算为 150 秒；
   数值以 `budgets` 为准，本轮未改文案（改文案会改变 authority 摘要→被视为冻结漂移）。

## 7. 预期调用/请求预算上限

| 项目 | 每 case | 合计（M2+F1） |
| --- | --- | --- |
| 模型调用 | ≤2（Focus 1 + Design 1；失败不自动重试） | ≤4 |
| 目标 HTTP 请求 | ≤12（M2 需 4，F1 需 6） | ≤24 |
| 墙钟时间 | ≤150 秒 | ≤300 秒 |
| 单次输入/输出上限 | 20000 / 4096 tokens | 输入 ≤80000，输出 ≤16384 |
| 历史实测参考 | v4a M1 11750；v3 T2V3 13570 tokens（单次） | — |

## 8. 未来真实验收步骤（本轮**不执行**）

1. **授权**：操作者明确批准“对冻结 v4b 的 M2/F1 各执行一次”，并确认消耗后不重放。
2. **提交**：代码与文档在 master 上提交，工作区干净；记录 `head`。
3. **预检复跑**：`acceptance.py preflight ... --plan plan_v4`，确认 manifest 的
   `executing_head` 等于即将运行的 HEAD，九项仍为 READY。
4. **单 case 运行**：`acceptance.py run runs/rw-acceptance-v4b-20261109 --plan plan_v4 --cases M2`
   （必须带 `--accept-code-change=<head7>`，因为相对冻结提交存在已审计的代码升级）；
   待人工确认证据后再决定是否执行 F1，**不做 best-of 采样**。
5. **证据**：`result.json` 分字段保存 planning（Focus/Design 响应、诊断、令牌）、validation、
   HTTP 序列与状态码、断言、清理（`CLEANED/CLEANUP_PENDING/OUTCOME_UNKNOWN`）；
   失败同样保存，不覆盖、不删改。
6. **停止条件**：任一冻结产物漂移、身份漂移、位置已消耗、未知写结果、预算触顶即停止；
   失败后不得 re-freeze、不得改 prompt/代码重试同一位置。
7. **收尾**：更新 `docs/progress.md` 与验收报告，核对 `WRITE_AUTHORITY` 已恢复 `NOT_GRANTED`，
   推送并在远端 CI 校验确切 HEAD。

## 9. 本轮验证

- `ruff check .`：通过（0 错误）。
- 新增测试：`tests/test_rw_acceptance_preflight.py`（10）、`tests/test_focus_order_ambiguity.py`（3）、
  `tests/test_product_instance_prompt.py`（4）；直接受影响的
  `tests/test_request_requirements.py`+`test_plan_instance_dependencies.py`+`test_product_design.py`
  +`test_instance_delivery_e2e.py`+`test_executor_bodyless_source.py` 合计 **105 passed**；
  `tests/test_rw_lifecycle_next_round.py`+`test_rw_lifecycle_preparation.py` **22 passed**；
  `tests/test_mixed_planning_round3.py` **21 passed**。
- 本轮机制自身的一次误报与修复：`order_ambiguity` 首版只把“有出边的节点”放进拓扑集合，
  于是无前驱的实例永远不被消解，三个合法场景（`test_mixed_planning_round3`）被误判为环。
  修复为“集合包含全部 `(clause, index)` 实例”，历史 T1V3 形状仍判为环并给出缺实例诊断；
  20 个混合规划用例与 3 个歧义用例全部通过。
- 全量离线：`python scripts/check_full.py --jobs 4` = **2580 collected / 2565 passed / 0 failed /
  15 skipped**（906 s，`source_unchanged=true`、`exact_test_id_coverage=true`）。
- 本轮**未执行**：真实模型调用、M2/F1 位置、业务写请求、re-freeze、生产访问。
