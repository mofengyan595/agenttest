# Planning IR 生产接入核查与最小修复

日期：2026-11-10（工作区实现；未进行真实模型调用，未消耗 M2/F1 位置）

## 1. 结论

修复前，生产入口确实**没有**进入新的确定性 IR 编译器。归档 M2 的 Focus 结果已经生成了
`planning_ir`（规范化生效，4 个请求实例），但 `summary["planning_requirements"]` 被无条件
设为该轮全部 8 个 Focus 子句。IR 激活条件里有一条 `not summary.get("planning_requirements")`，
因此该条件恒为假，`group_ir` 保持 `None`，`construct_plan()` 实际选择
`UNORDERED_APPROVED_NODES_V1`（旧路径）。

修复后，生产入口在真实 M2 语义下实际执行
`NORMALIZED_IR_COMPILER_V1`，并把实际执行证据写入产品报告。离线 E2E 的 4 个请求按
`POST /items → GET /items/item_1 → DELETE /items/item_1 → GET /items/item_1` 执行并通过。

## 2. 修复前的生产证据

用 `runs/_e2e_product.py` 重放归档 M2 的 Focus/Design 输出（MockTransport，不调用模型）：

| 项目 | 修复前 |
|---|---|
| Focus required 子句 | 8 |
| `summary["planning_requirements"]` | 8（非空） |
| `planning_ir` | 存在 |
| `normalization_applied` | `true` |
| IR 实例 | 4（`ai_1..ai_4`） |
| `group_ir` | `null` |
| 实际 construction encoding | `UNORDERED_APPROVED_NODES_V1` |
| 实际执行请求 | 4 个，顺序正确（旧构造器恰好也能完成本样例） |

关键点：**旧路径能跑通本样例，不等于新编译器被接入**。修复前的“E2E PASS”只证明了旧路径可用，
不能证明生产入口调用了新编译器。

## 3. 最小修复

### 3.1 激活条件

删除“只要存在普通 `planning_requirements` 就禁用 IR”的条件。普通 requirement 是 Focus 对
原文义务的记录，不是权限阻塞，也不是未解决歧义；它不应阻止程序把已规范化的请求实例交给
编译器。

新的激活条件只使用真实的阻塞事实：

1. `planning_ir` 存在；
2. `normalization_applied` 为真；
3. 无 IR `ambiguities`；
4. 无待处理权限（`permission_requirements` 为空）；
5. 无待确认问题（`questions` 为空）；
6. 无额外 requested checks；
7. 至少有一个实例；
8. 本 Design 组覆盖全部实例操作；
9. 没有被规范化隐藏、但仍带 action 的 `needs_review` 或多 action `required` 子句。

条件名称和每个条件的实际布尔值写入
`summary["planning_engine_groups"][*]["conditions"]` 与
`summary["design_records"][*]["planning_engine_conditions"]`。没有符合条件的组仍走旧路径，
并记录首个具体原因，例如 `PENDING_PERMISSION`、`IR_AMBIGUITIES`、
`UNRESOLVED_REQUEST_CLAUSES`、`PARTIAL_GROUP_COVERAGE`。

### 3.2 实际执行证据

不能只凭 `summary["planning_ir"]` 存在就声称使用了新编译器。报告字段来自
`raw_model.construction_records` 的真实 `encoding`：

- `planning_engine`：`NORMALIZED_IR_COMPILER_V1` / `LEGACY` / `MIXED` / `NOT_EXECUTED`；
- `planning_engine_reason`：实际选择或回退原因；
- `planning_engine_choice_reason`：计划阶段的首个阻塞原因；
- `planning_engine_conditions`：每个激活条件的实际值；
- `planning_instance_count`：规范化后的请求实例数；
- `planning_instance_mapping_complete`：模型步骤是否全部绑定到实例；
- `planning_dependency_edges`：编译器实际生成的依赖边数；
- `planning_engine_groups`：按 Design 组保存上述证据。

### 3.3 无 source binding 的确定性实例匹配

M2 的两个 GET 在 IR 中分别对应已批准的 `200` 与 `404` 策略。生产 E2E 的 Design 输出故意不带
`requirement_source_id`，编译器仍能唯一匹配：

- `readItem` + 200 策略 → `ai_2`；
- `readItem` + 404 策略 → `ai_4`；
- CREATE/DELETE 各只有一个实例，按操作唯一匹配。

匹配只使用已批准语义：`operation_id`、`role`、`policy_id`、策略/IR 的状态断言、已解析的
生产者引用、字面量参数槽位。**不使用数组位置**。若两个候选在所有证据上相同，返回
`PLANNING_INSTANCE_AMBIGUOUS` / `NEEDS_REVIEW`，不猜测；也不修改模型给出的业务参数。

### 3.4 字面量保留

核查中发现一个会误伤合法请求的编译器缺陷：IR 为“已批准但未固定值”的参数保存
`value=null`（例如 `limit` 允许 1..3、`order` 允许 asc/desc），旧编译代码把这个 `null`
覆盖到模型给出的合法值，导致 `OPENAPI_VALUE_TYPE`。修复为：只有非 `null` 的程序解析值才
覆盖模型参数；`null` 表示“由模型的合法值决定”，交由既有 DSL/OpenAPI 校验，而不是清空。
这不是修改模型业务参数，也没有放宽校验。

## 4. 生产入口 E2E 结果

测试：`tests/test_planning_ir.py::test_archived_m2_semantics_run_through_the_product_entry`

- Focus：归档 M2 的 8 个 required 子句；
- `planning_requirements` 长度：8（证明它不再阻塞 IR）；
- 规范化：8 → 4 个请求实例；
- Design：4 个步骤，**不带** `requirement_source_id`；
- 实际构造 encoding：`NORMALIZED_IR_COMPILER_V1`；
- 实例映射：`ai_1→c`、`ai_2→r1`、`ai_3→d`、`ai_4→r2`，完整；
- 依赖边：5；
- 请求：`POST /items`、`GET /items/item_1`、`DELETE /items/item_1`、
  `GET /items/item_1`；
- 两个 GET 是不同实例，最后一个 GET 使用 404 期望策略；
- 结果：`PASS`；没有额外 CREATE/GET/DELETE；所有路径 ID 来自 CREATE 的 `item_1`。

E2E 断言的是 `summary["planning_engine"] == "NORMALIZED_IR_COMPILER_V1"`，不是仅断言 IR 存在。

## 5. 安全路径测试

- `test_unbound_repeated_gets_match_on_approved_status`：两个无 source 的 GET 由 200/404
  策略唯一映射，证明确定性匹配可工作。
- `test_unbound_identical_gets_remain_review`：两个完全相同的 GET、没有可区分证据时，
  构造器返回 `PLANNING_INSTANCE_AMBIGUOUS`，不按数组顺序猜测。
- 全量离线回归包含既有负向用例、权限待审、资源歧义、真实依赖环和构造失败用量测试；
  负向步骤仍在 `construct_design` 中显式关闭 `planning_ir`，走旧构造路径。

## 6. 验证

- `ruff check .`：通过。
- 定向：Planning IR、Focus references、negative values、mixed planning、composed planning、
  product lengths、development A/B、eval v2 runner 均通过。
- 完整离线回归（4 进程，冻结运行源码）：
  **2654 collected / 2639 passed / 0 failed / 0 errors / 15 skipped**，
  wall 884.438 秒。
- 本轮没有调用真实模型，没有重跑已消耗的 M2/F1，没有修改归档证据、验收结果或冻结评分。

## 7. 三个问题的直接回答

1. **原来的生产路径是否真的绕过了新编译器？**
   是。M2 语义下 IR 已生成且规范化生效，但 `planning_requirements` 非空使激活条件恒假，
   实际 construction encoding 是 `UNORDERED_APPROVED_NODES_V1`。
2. **修复后有没有测试证明生产入口真的调用了新编译器？**
   有。生产入口 E2E 使用归档 M2 Focus 结果和 MockTransport，断开 source binding，断言
   实际 `planning_engine == "NORMALIZED_IR_COMPILER_V1"`、4 个实例全部映射、5 条依赖边、
   4 个请求的正确顺序与 404 回读，并验证没有额外请求。
3. **是否可以进行新一轮独立的真实模型验收？**
   代码与离线证据已准备好，但本轮只完成离线核查与最小修复；是否开始新的独立真实模型
   验收需用户明确授权。未授权前不调用真实模型，也不重跑已消耗位置。
