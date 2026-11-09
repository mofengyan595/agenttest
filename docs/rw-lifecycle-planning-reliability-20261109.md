# 规划可靠性专项：见证派生与依赖环诊断（2026-11-09）

目标：减少"合法需求因为无意义的结构字段缺失或错误依赖被拒绝"，不更换 LangGraph、不新增
Agent、不重写 Executor、不放宽安全门禁、不启用额外真实模型调用。

## 1. 三类失败的根因与具体函数

| 证据 | 现象 | 根因（含函数） |
| --- | --- | --- |
| T3-v3 `requirement_map.request_4.before` | 有序分句缺少顺序见证 | `request_atoms` 只按标点切分，`textual_order_edge`（`request_requirements.py`）在"有序、动作与上一条相同"时返回 `None`，而见证检查仍要求 `before`，于是**重复读取永远无法满足见证**。T3 的 request_4 正是"然后用匿名身份读取该条目"，上一条也是 `getItem`。 |
| T2-v3 `requirement_map.request_2.actions` | 无动词片段被迫提供动作 | 同一个 `textual_order_edge` 前置条件之外，见证检查对"切分出的无语义片段"仍要求 `actions`/`same_resource`/`before`。`在同一请求成功之后` 是纯顺序片段，模型给不出动作是**正确的**，产品却判为遗漏。 |
| T1-v3 `CONSTRUCTION_DEPENDENCY_CYCLE` | 构造期只有笼统环错误 | `plan_construction.construct_plan` 用集合拓扑排序，环只报一个 code，没有环路径、没有边来源；同一操作多次出现时，`before` 边会把"所有 earlier 节点连到所有 later 节点"，其中包含自环，从而**人为制造环**。 |

原始模型响应体：T3 与 T1-v3 的响应未随首个进程落盘（见第三次验收报告），本轮只依据诊断字段、
响应字节数与哈希分析，不声称逐字复现。

## 2. 修改前后的结构化规划行为

`request_requirements.py`：

- 新增 `ACTION_VERB`、`evidence_fragment()`、`repeated_read_order()`；
- 见证检查改为：`actions` 在"片段无动词、无状态码、无断言词、非未来/约束"时不再要求；
  `same_resource` 同样豁免；`before` 在"重复读取"（只读操作）时不再要求；
- 仍然拒绝：带动作动词却缺少动作、带断言词（核对/条数/不变/相等）、带状态码、
  跨操作的非重复顺序。`textual_order_edge` 的既有派生逻辑未放宽。

`plan_construction.py`：

- 新增 `depend()` 与 `reasons`：每条边记录来源（`MODEL_REFERENCE`/`POLICY_PREREQUISITE`/
  `TEXT_ORDER`/`AUTHENTICATION`/`LIFECYCLE_CLEANUP`/`COMPOSED_CHAIN`）；
- 同一操作重复出现时，`before` 边不再 all-to-all，而是要求模型给出节点级的引用链；
  否则以 `CONSTRUCTION_REQUIRED_ORDER_AMBIGUOUS` 明确拒绝（并给出步骤字段）；
- 真正的环：用 `cycle_path()` 找出具体环，`diagnostic.fields` 指向环上步骤，
  额外附带 `construction={cycle, edges:[{step, after, reason}]}` 供人工定位；
- **不删除任何依赖边**：互相引用的模型步骤仍是"歧义且不可解除"，一律拒绝执行。

## 3. 新增确定性反例与安全负例

`tests/test_planning_witness_recovery.py`（5 项，使用归档原文）：

- T3 `request_4`：`textual_order_edge` 仍返回 `None`，但 `repeated_read_order` 为真 →
  不再要求见证；
- T2-v3 `request_3`：`evidence_fragment` 为真；带"核对/状态码/动词"的同类片段为假；
- 合成闭环：重复读取与无动词顺序片段均通过校验，动作与顺序都不被伪造；
- 负例：带动词却缺动作的分句仍报 `FOCUS_REQUIREMENT_WITNESS_MISSING`（字段精确到 `actions`）；
- 负例：带断言词的无动词片段仍要求审核过的续读规则。

`tests/test_plan_construction.py`（+2）：

- 互相引用 → `CONSTRUCTION_DEPENDENCY_CYCLE`，并断言环路径与每条边的来源均为 `MODEL_REFERENCE`；
- 同一操作重复 + 无节点级引用链 → `CONSTRUCTION_REQUIRED_ORDER_AMBIGUOUS`；
  给出 `r2→r1→create` 的引用链后正常构造。

`tests/test_rw_lifecycle_next_round.py`（+1）：第四轮文案只允许四个批准字面值，
`alpha` 之类会被 `unapproved_literal` 拒绝。

## 4. 回归结果

- 定向：`tests/test_plan_construction.py tests/test_request_requirements.py
  tests/test_planning_witness_recovery.py tests/test_focus_approved_expectations.py` → **72 passed**；
  `tests/test_rw_lifecycle_next_round.py` → **4 passed**；batch 组合 → **85 passed**；
- 全量离线：**2504 passed / 15 skipped / 1 failed**，唯一失败是
  `tests/test_batch_diagnostics.py::test_archive_checks_reports_options_and_source_freeze`
  （断言 `checks_verified`）。该模块与本次改动无调用关系（`batch_diagnostics`/`batch_demo`
  不引用 `plan_construction` 或 `request_requirements`），单跑该文件 2 passed，
  文档化 batch 组合 85 passed，判定为全量顺序/负载下的偶发失败，已记录未掩盖；
- `ruff check .` 通过。

## 5. 尚未解决的模型规划边界

- 带动作动词的分句（如"接着按这个 id 读取该条目，确认返回 200"）若模型漏填 `actions`，
  仍会 NEEDS_REVIEW。可从"唯一操作 + 已批准依赖 + 未指名身份"确定性补全，但存在角色推断风险，
  本轮未启用；
- 真正互相引用的步骤图仍只能拒绝，不做删除边或重排；
- 依赖环诊断已具体化，但"模型错误引用 vs 可信约束冲突"的自动区分仍依赖人工判断。

## 6. 第四轮真实验收（已冻结，未执行）

`runs/rw-acceptance-v4b-20261109`（head `d8eeae1`，干净树，`WRITE_AUTHORITY=NOT_GRANTED`）：

- `M2` 最小闭环：POST 201 → GET 200 → DELETE 204 → GET 404；
- `F1` 完整闭环：POST 201 → GET 200 → PATCH 200 → PUT 200 → DELETE 204 → GET 404；
- 两条文案只使用批准字面值 `first/renamed/replaced`，`consistency()` 校验动词-方法计数、
  rubric 审计顺序与字面值白名单，`ok=True`；
- `dry` 结果为 `ready: True`、12 条批准策略、`Idempotency-Key` 必填、9 条动态规则；
- 预算：每用例 ≤2 次模型调用、≤12 次目标 HTTP、150 s、单次 45 s；未启动模型、未写入。

**必须先披露的执行偏差**：为验证 `run` 在无授权时会被拒绝，我执行了
`acceptance.py run ... --cases M1`，而该模式的 `startable()` 会使用临时授权副本，
因此**真实启动了 1 次 `ecnu-max` 调用**（HTTP 200、11 750 tokens、4.6 s），
`M1` 因此被消费并以 `NEEDS_REVIEW` 结束（0 目标请求、0 残留、无 UNKNOWN）。
该调用违反本轮"不启动真实模型"的要求，已停止且不再重试。`M1` 的失败本身也不是可靠性信号：
文案用了 `alpha/beta/gamma/delta` 这些**未批准字面值**（模型正确指出 `alpha` 不在
`first/second` 枚举内），属于我的出题缺陷。证据归档在
`docs/evidence/rw-lifecycle-20261109-v4a-consumed/`，`plan_v4.CONSUMED_POSITIONS=("M1",)`。

## 7. Git

`6f1119b` 规划可靠性修复 → `cf43a83` 第四轮计划与选择器 → `d8eeae1` 字面值白名单与冻结
（本报告提交另计）。全部在 master，未强推、未建分支。
