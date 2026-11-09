# 实例级依赖建模修复（2026-11-09）

本轮起点 `b4b329a`（本地 HEAD == `origin/master`，工作树干净），只在 master 上提交与普通 push，
未使用 force、未新建分支；`WRITE_AUTHORITY` 保持 `NOT_GRANTED`，未产生任何付费模型调用，
未执行 v4 的 M2/F1，未改动任何冻结证据或 rubric。

目标：让 AgentTest 能区分同一个 API 的不同调用实例，并按用户原文规定的先后执行，
而不是依赖模型恰好输出了某种数组顺序。

## 1. 最小复现（修复前先失败）

复现脚本 `runs/evidence_eight_scenarios.py` 用同一份"已批准节点 + 需求映射"分别调用
`b4b329a` 的 `construct_plan`（`git show` 载入旧模块）与当前实现，证据落盘
`runs/20261109-instance-order/{pre_fix,current}.json` 与 `eight-scenarios.json`。

修复前的三个缺陷（均可离线复现，不依赖模型输出运气）：

| 缺陷 | 触发输入 | `b4b329a` 实测 | 期望 |
| --- | --- | --- | --- |
| A 不同 operation 全连接 | 创建 → 读 → 删除 → 再读（读在删除两侧） | `CONSTRUCTION_DEPENDENCY_CYCLE`，环 `d → read_after → d` | 四个实例各自成链 |
| B 同参数非相邻重复被签名合并 | 建 A → 建 B → 再建 A | 执行序 `a1, a3, b2`，`b2 ← {a1, a3}` | `a1, b2, a3` |
| C 提案数组位置被当成顺序 | 冲突创建写在最前 | 执行序 `conflict, first, replay` | `first, replay, conflict` |

旧实现的根因就是本轮要修的三行事实：`earlier = [n for n in nodes.values()
if n["operation_id"] == order["earlier"]]` 把同一 API 的所有节点都当成同一个"操作实例"；
`instance_groups()` 只按请求签名分组，参数相同的非相邻调用被并成一组；
构造顺序只从需求映射的 operation_id 出发，于是只能靠提案数组顺序兜底。

## 2. 实例身份设计

- **实例 = `(source_id, action 出现下标)`**：`source_id` 是 `request_atoms()` 给原文片段的稳定编号，
  下标是该片段 `actions` 数组中的第几次出现。`operation_id` 只表示 API 类型，不再表示实例。
- **谁声明**：Focus/Design 线上新增两个可选字段 `requirement_source_id`、`requirement_action_index`
  （`GenericDraftStep`），只有"同一操作在一次请求里出现多次"时才进入线上 schema
  （`product_design._needs_instance_bindings`）；此时两字段是**必填的 nullable 槽**
  （`llm_planner._require_requirement_bindings`），否则从 schema 移除，线上字节与历史完全一致。
- **需求顺序携带实例**：`RequiredOrder` 增加 `earlier_source_id/earlier_index/later_source_id/later_index`；
  Focus 派生的文本顺序边直接写实例（`textual_order_edge`）。未绑定实例时字段不序列化，
  历史报告字节不变。
- **解析规则（`request_requirements.order_instances`）**：显式 id 优先；两侧同一操作时，
  只接受"已声明重复的单一实例"（重放组）或用显式 id 分开的两次出现；
  两侧不同操作时返回两侧的**全部实例集合**（集合约束，`earlier` 的每一次都先于 `later` 的每一次），
  不猜数组位置、不猜片段顺序。无法解析即 `FOCUS_REQUIREMENT_ORDER_INSTANCE_INVALID`
  （NEEDS_REVIEW，零 HTTP）。
- **构造（`plan_construction.construct_plan`）**：`bound_instances()` 把实例映射到节点；
  同一实例的多个节点只有在**逐字节同请求**时才允许，并串成 `REPLAY_GROUP` 内部序列；
  其余情况按实例集合建 `TEXT_ORDER` 边。真实矛盾仍报 `CONSTRUCTION_DEPENDENCY_CYCLE`
  并保留环路径与逐边 `reason`；不删边、不换序、不补边。
- **Design 门（`validate_plan_requirements`）**：核对每个绑定指向真实 clause/action；
  同一实例的多步必须是同请求；两侧不同操作时允许"全实例"覆盖（不会误判），
  同一操作用例必须真正绑定（`DESIGN_REQUIRED_ORDER_INSTANCE_MISSING`），
  跨执行上下文时按用例序列核对（`DESIGN_REQUIRED_ORDER_MISSING`），不看数组下标。

## 3. 变更文件

- `src/agenttest/request_requirements.py`：`RequiredOrder` 实例字段与校验；`order_instances`
  返回实例集合；`textual_order_edge` 产出实例边；删除 `repeated_read_order`/`read_order_witness`；
  `validate_requirement_links` 的实例自环守卫；`validate_plan_requirements` 的绑定审计与跨用例顺序。
- `src/agenttest/plan_construction.py`：`bound_instances`、`node_for`（集合语义）、
  `REPLAY_GROUP` 串行化、按实例建边、缺失实例的精确诊断。
- `src/agenttest/llm_planner.py`：`GenericDraftStep` 增加可选绑定字段；`_require/_drop_requirement_bindings`。
- `src/agenttest/product_design.py`：`_needs_instance_bindings`，按需提供/要求绑定槽。
- `src/agenttest/product_schema.py`：绑定槽为紧凑 nullable 类型（不再在每条 policy 分支重复 clause 枚举）。
- `scripts/rw_lifecycle/acceptance.py`：模型身份多维比对（见第 7 节扩展 A）。
- 测试：`tests/test_plan_instance_dependencies.py`（新增 10 条）、`tests/test_plan_construction.py`、
  `tests/test_request_requirements.py`、`tests/test_focus_approved_expectations.py`、
  `tests/test_planning_witness_recovery.py`、`tests/test_mixed_planning_p0.py`、
  `tests/test_mixed_planning_round3.py`（后两条只调整"数组位置不再作为拒收依据"的断言）、
  `tests/test_rw_lifecycle_next_round.py`。

## 4. 新旧依赖图对照

同一输入下 `b4b329a` 与当前实现的执行序/依赖边（`runs/20261109-instance-order/eight-scenarios.json`）：

| 场景 | 修复前 | 修复后 |
| --- | --- | --- |
| 最小 CRUD | 假环 `d ↔ read_after` | `c → read_before → d → read_after`；`d` 只依赖 `c, read_before` |
| 完整 CRUD | 假环 | `c → r1 → p → u → d → r2` |
| 非相邻重复 | `a1, a3, b2`（`b2 ← {a1,a3}`） | `a1, b2, a3`（每个实例一条边） |
| 幂等重放/冲突 | `conflict, first, replay` | `first, replay, conflict`（`replay` 与 `first` 同请求 → 重放组） |
| 多身份 | `c, other_read, owner_read, d` | `c, owner_read, other_read, d` |
| 连续读取 | `g3, g1, g2` | `g1, g2, g3` |
| 真矛盾 | 环（`d ↔ read_after`，TEXT_ORDER） | 同环，路径与逐边原因保留，零 HTTP |
| 无法解析 | 直接执行 `r1, r2`（按数组） | `CONSTRUCTION_REQUIRED_ORDER_AMBIGUOUS` + `requirement_map.request_2.before`，零 HTTP |

## 5. 八条必测反例

`tests/test_plan_instance_dependencies.py` 覆盖：最小 CRUD（200/404 读回）、完整 CRUD、
非相邻重复建（乱序提案）、幂等重放与冲突（201/200/409）、多身份（创建 → owner 读 → other 读 → 删除）、
连续 GET×3、真矛盾成环（零 HTTP）、无法解析映射（零 HTTP、指定字段）。断言不是只看状态码，
而是逐条断言 `node_dependencies` 的边集合与执行序；扫描 30 个乱序排列证明"数组顺序不产生边"。

## 6. 回归与 CI

- `python -m ruff check .`：通过（0 错误）。
- `pytest tests/test_plan_instance_dependencies.py -q`：10 passed。
- `pytest tests/test_plan_construction.py tests/test_request_requirements.py
  tests/test_focus_approved_expectations.py tests/test_planning_witness_recovery.py -q`：77 passed。
- `pytest examples/regressions -q`：10 passed。
- `pytest tests/test_mixed_planning_round3.py tests/test_mixed_planning_p0.py -q`：55 passed。
- `pytest tests/test_composed_preflight.py tests/test_composed_review.py tests/test_composed_workflow.py
  tests/test_composed_authentication.py tests/test_composed_constraints.py -q`：70 passed。
- `pytest tests/test_rw_lifecycle_next_round.py -q`：7 passed。
- `python scripts/check_full.py --jobs 4`：**2541 collected / 2526 passed / 0 failed / 15 skipped**，
  746 s（本地离线，无网络、无模型）。上一轮同脚本曾失败 51 条，本轮逐一修到 0。
- GitHub Actions：见第 10 节（push 后以远端结论为准；本地无法代替远端 CI）。

## 7. 范围外两处补强

**A. 模型身份漂移**：`scripts/rw_lifecycle/acceptance.py` 原只比 `model`。现在
`IDENTITY_FIELDS = (model, protocol, base_url, timeout, max_output_tokens, max_input_tokens)`，
`normalize_identity()` 归一化 base_url（scheme/host 小写、去尾斜杠）、数值预算，凭据只做存在性检查、
从不输出；`run()` 发现漂移直接 `SystemExit("MODEL_IDENTITY_DRIFT:<字段>")`，离线 dry-run 不读 `.env`。
反例：`tests/test_rw_lifecycle_next_round.py::test_same_model_name_at_a_different_address_is_identity_drift`
（同名不同地址 → 拒绝；大小写/尾斜杠不算漂移；API key 不出现在任何输出）。

**B. 长结构化切分**：`bounded_span()` 原来只在 40 字符前瞻内找闭合引号/括号，超长即被切断。
现在扫描到闭合处（上限 4000 字符），仍无法闭合时保留剩余原文整体，绝不截断或改写；
片段偏移始终等于原文切片。反例：`tests/test_request_requirements.py::
test_a_structure_longer_than_the_lookahead_stays_in_one_span`（>400 字符引号值 / JSONPath / 嵌套 JSON 各保持完整，
偏移精确；未闭合结构整体保留）。修复前同一输入在 300 字符处切断（`literal_intact=false`）。

## 8. 历史格式与冻结证据影响

- `DraftStep`（`PlannerDraft`/`BatchPlannerDraft`/`ComposedPlannerDraft`/`evaluation_freeze` 的
  `draft_schema_sha256`）**未改**，因此这些线上的 schema 指纹、冻结记录与历史档案字节不变。
- 绑定字段只在"同一操作重复出现"的请求里进入 Product Design 线；其余请求的线上 schema 与
  历史逐字节一致，历史回复照常校验。
- Python 模型上两个字段仍是可选：历史 Design/报告可继续加载；新报告仅在真正绑定时多出这两个键，
  属向后兼容扩展。
- 冻结的 v4 证据（`runs/rw-acceptance-v4b-20261109/`）未读取为输入、未改写、未重算。

## 9. 仍未解决的风险

1. 同一操作的两次出现若既无显式 id、也无"已声明重复"，现在会停在
   `FOCUS_REQUIREMENT_ORDER_INSTANCE_INVALID`/`CONSTRUCTION_REQUIRED_ORDER_AMBIGUOUS`；
   这是有意的 NEEDS_REVIEW，但需要真实模型学会填写绑定字段（本地只有 mock 证据）。
2. 两侧不同操作时采用"全实例"覆盖（集合约束），比逐实例更严格；若用户本意是
   "只让其中一次读发生"，需要显式 id 才能表达。
3. 绑定槽不再带 clause 枚举（若带，输入预算超限）：非法 clause id 由本地校验拒绝，
   代价是模型可能多花一次重试。
4. 跨用例顺序仍要求用例序列满足；用例内部顺序完全由构造决定，报告读者需理解这一点。
5. `CONSTRUCTION_REQUIRED_ORDER_AMBIGUOUS` 在同操作、未绑定、且节点同请求时触发；
   与"重放组"语义相邻，文档与诊断文案仍需在下一轮统一。

## 10. M2/F1 技术准备与远端 CI

- M2（最小闭环）与 F1（完整闭环）所需的实例级依赖建模、重放组序列化、删除后读回、
  多身份读、真矛盾成环、无法解析即 REVIEW 均已在离线反例中固定；`b4b329a` 上出现的
  假环与数组序错误不再复现。
- 本轮**未执行** M2/F1，也未新增任何真实模型调用；`runs/rw-acceptance-v4b-20261109/` 保持冻结，
  `CONSUMED_POSITIONS=("M1",)` 不变。执行它们仍需要一次新的显式付费授权。
- 远端 CI 状态以 push 后的 GitHub Actions 为准，命令与结论记录在 `docs/progress.md` 同一节。
