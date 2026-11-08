# 第三轮缺陷修复与离线验收（2026-11-08）

**第三轮真实 A/B 结果保持冻结，未重跑、未重调已用位置。** 本轮只针对第三轮暴露的 5 类系统性缺陷做最小通用修复，并用确定性反例在离线环境验证。
第三轮结论仍为 **不接受**：错误拒绝 14→14、静默漏项 71/186→81/186、正确局部待审 7/20→9/20、任务范围越界 1→0、可完整执行 0/4（两版）。
本文件只说明缺陷根因、修复后的离线行为、回归结果与技术边界；它不改变第三轮的任何分数或证据。

## 1. 冻结基线与方法

- 第三轮冻结：baseline `77effe8db742419c7256509bafbd417562d40f71`，candidate `f5e3e359cfc743a10d6ab20ad9fb1c271ee968f5`，48 位置、76 次真实调用、471,600 Token、UNKNOWN/基础设施失败均 0；证据见 [evidence/mixed-planning-round3-ab-20261107](evidence/mixed-planning-round3-ab-20261107/freeze.json) 与 [第三轮报告](mixed-planning-round3-ab-20261107.md)。
- 修复起点为 master `8111a37358f1cc161249016035ff0c25c68ea0ca`；本轮不新增分支、不 reset、不 push --force、不修改 CourseInsight、不做生产访问、不写业务数据。
- 反例来源全部是第三轮**原始原文分句**（逐字复制，未改写），只把夹具移入离线 loopback 项目：索引/条目/回执/健康/构建/账号/标签/分页 8 个只读接口、4 个已配置身份、审批检查与 `permission_question_roles` 均由可信配置给出。
- 付费 A/B 未重跑；本轮所有运行都是本地 pytest（无网络、无模型、无外部目标）。

## 2. 失败类根因（模块/函数）

### 2.1 P0-1 数据来源与响应断言混为一谈（D01/D02/D03/D04）

- `requirement_ledger._DATA_PREFIX` 的引导词表不含“将它响应里的”（D01）与“以该响应中的”（G04），`explicit_data_references` 因此完全不识别该次取值；随后响应检查分支把同一个 `$.links[0].id` 当成未批准的断言，`unresolved_response_operations` 以 `RESPONSE_CHECK_NOT_APPROVED` 阻塞 readEntry，D01 之后的健康读取又因顺序依赖一起停摆。
- `explicit_response_checks` 只在“分句尾部没有检查动词”时跳过数据来源出现位置；`核对回执 $.code 等于 "R73" 后匿名读取健康状态`（D02）尾串不能被谓词完整匹配，得到 `operator=None`；该分句有两个动作（readReceipt/owner、readHealth/null），`unresolved_response_operations` 判为 `RESPONSE_SCOPE_AMBIGUOUS`，两个动作一起阻塞——真正的“读取回执并断言”和独立的“读取健康”都没执行。
- 同一个选择器可以同时被登记为“来源”和“断言”，一个分句里任意一个动作上的未批准断言会阻塞该分句的全部动作。
- 相关函数：`explicit_data_references`、`explicit_response_checks`、`unresolved_response_operations`、`validate_response_witnesses`（均在 `src/agenttest/requirement_ledger.py`）。

### 2.2 P0-2 无证据待审项拖停合法独立工作（I03/G02/G04）

- 焦点模型把“确认权限/规则”的分句映射为 `needs_review` + `actions=[]`，但没有给出确认问题；`validate_requirement_links` 判定 `FOCUS_REQUIREMENT_GAP_WITHOUT_EVIDENCE`，整轮零请求。
- 即使待审项本身正确，服务端也没有“用可信配置 + 原文”补出待审问题的能力，于是同一轮的独立合法读取（匿名构建、分页、标签等）被一起拒绝。
- 相关函数：`validate_requirement_links`、`product_focus.select_focus`、`TestFocusModel._decode_output`。

### 2.3 P0-3 权限问题与跨用例顺序（I01/I04）

- `permission_inquiry` 只认“权限/403/404”等词，不认“拒绝规则”，I01 第 2 轮因此报 `FOCUS_PERMISSION_QUESTION_WITHOUT_ACTION`。
- 一个分句同时点名两个接口时，模型只声明其中一个问题，另一个被静默丢弃（I04 第 2 轮缺 `readAccount`）；待审身份还可能被同轮“再访问它”映射回可执行动作。
- I01 第 1 轮是 `CONSTRUCTION_REQUIRED_ORDER_AMBIGUOUS`：模型把两次匿名构建放在用例 1、账号读取放在用例 2，计划构造按“单用例内顺序”检查跨用例顺序而拒绝整案。
- 相关函数：`permission_inquiry`、`permission_review_scope`、`read_order_witness`/`validate_plan_requirements`、`construct_plan`。

### 2.4 P1 中文说明被当成结构约束（D02/D03/D04 第 2 轮）

- `FocusSelection.interpretation` 与 `TestDesign.interpretation` 带 CJK `pattern`；`prepare_request` 把 `pattern` 从生成 schema 里弹出，解码仍按 `pattern` 校验——schema 与解码不一致，纯英文说明被判 `MODEL_SCHEMA_TYPE_OR_CONSTRAINT`/`string_pattern_mismatch`。
- 这是**表述**问题，与角色、权限、动态来源、范围、断言审批、输入来源无关；把它当结构失败会制造非安全类错误拒绝。

## 3. 修复内容与新增反例

### 3.1 数据来源与断言的分离（P0-1）

- 数据引导词表补齐“将它/它…响应里的”“以该响应中的”“用这份响应中的”“依据该响应中的”等；同一个选择器出现位置**只要**是数据来源，就不再登记为断言。
- 新增 `_continued_predicate`：一个完整字面量谓词后面允许跟独立后续动作（“核对…等于 R73 **后**匿名读取健康状态”），条目携带 `continues`。
- 被取值的断言若指向“该字段/它”这类无选择器目标，登记为 `RESPONSE_ANAPHOR_UNRESOLVED` 并保持待审——不再借用来源选择器，也不会被静默丢弃。
- 断言归属：`unresolved_response_operations` 只在“该分句只有一个动作或断言带后续独立动作”时把已批准断言归属到唯一批准动作（`approved_action`），只阻塞真正无法归属的断言；`validate_response_witnesses` 按该动作校验见证。
- 单测/集成反例：`tests/test_mixed_planning_round3.py` 中 `test_response_anaphora_is_a_data_source_not_an_assertion`、`test_response_predicate_survives_a_following_independent_action`、`test_content_assertion_after_a_data_source_stays_its_own_check`、`test_assertion_on_the_transferred_value_has_no_borrowed_selector`、`test_requested_data_source_flow_executes_its_binding`、`test_approved_predicate_and_independent_clause_action_both_execute`、`test_unapproved_content_assertion_still_holds_its_read`。

### 3.2 待审问题见证与独立工作（P0-2）

- 新增 `permission_inquiry_operations`：只用 `grouped_index` 的运营标签（GET/HEAD/OPTIONS）与原文分句求最长共有短语；两个接口共用同一短语则判为不可判定，短短语被更长标签覆盖时按更长者解释。
- 新增 `named_role` 与 `derive_permission_questions`：在 `TestFocusModel._decode_output` 与 `select_focus` 幂等挂接，只用可信 `permission_question_roles`、运营标签、原文分句与具名身份补出缺失的待审问题；每个问题保留自己的操作、身份、原文引文、`status_choices=[403,404]` 和待审内容，总数不超过 8。
- `validate_requirement_links` 的 gap 见证、`permission_question_witness` 的“操作/身份/原文”三重约束未被放宽：无标签、无名身份、非显式询问的分句仍拒绝（`FOCUS_REQUIREMENT_GAP_WITHOUT_EVIDENCE` 保留）。
- 集成反例：`test_permission_inquiry_without_a_model_question_keeps_its_own_witness`、`test_pending_permission_does_not_block_independent_legal_reads`、`test_declared_and_derived_permission_questions_are_both_kept`，以及负例 `test_pending_clause_without_a_witness_is_still_refused`。

### 3.3 权限询问与跨用例顺序（P0-3）

- `permission_inquiry` 增加“拒绝规则/拒绝状态/是否拒绝/是否允许…”表达。
- `permission_review_scope` 记录待审问答对 `(operation_id, role)`，把该身份的动作从执行范围移除并登记 `PERMISSION_EXPECTATION_NOT_APPROVED`，因此待审身份不会被匿名或其他身份顶替（B12 的任务范围保护不变）。
- `validate_plan_requirements` 的顺序校验改为对**展开后的步骤序列**（用例按列表顺序执行）检查；`construct_plan` 在“更早的操作已在本设计中（`design_operations`）”时不再报 `CONSTRUCTION_REQUIRED_ORDER_AMBIGUOUS`，跨用例顺序因此可执行而不是被拒。
- 反例：`test_permission_inquiry_wording_covers_refusal_rules`、`test_review_scope_removes_a_pending_identity_action_even_in_another_clause`、`test_required_order_across_independent_cases_still_executes_in_order`。

### 3.4 表述与结构的分离（P1）

- 去掉两处 `interpretation` 的 CJK `pattern`，`prepare_request` 不再弹出 `pattern`：生成 schema 与解码一致，纯英文/中英混排说明被接受并原样保留。
- 新增 `presentation_language`（`src/agenttest/diagnostics.py`）：在 `summary['focus_presentation']` 与设计记录的 `presentation` 中记录 `CHINESE|NON_CHINESE|EMPTY`，只做观察，不改变任何校验、分数或安全门。
- 角色、权限、动态来源、范围、断言审批、输入来源六类门禁均未放宽（`tests/test_dsl_safety.py`、`tests/test_reliable_safety.py`、`tests/test_product_scope.py` 全绿）。
- 反例：`test_presentation_language_is_reported_without_rewriting`、`test_non_chinese_focus_interpretation_is_accepted_and_preserved`、`test_non_chinese_design_prose_does_not_become_a_schema_failure`。

### 3.5 修复前后证据

- 修复前：把 master `8111a37` 的源码树用 `git archive` 导出到 `runs/prefix-head`，只复制新反例文件运行 → **19 失败 / 2 通过**（通过的两项是 D04 的参数化用例与安全负例，说明修复没有放松原有门禁）。原始输出与 JUnit 见 [pre-fix-failures.txt](evidence/mixed-planning-round3-fixes-20261108/pre-fix-failures.txt)、[pre-fix-junit.xml](evidence/mixed-planning-round3-fixes-20261108/pre-fix-junit.xml)。
- 修复后：同一批反例 **21 通过**；`tests/test_round3_ab_evaluation.py`（评分器/夹具小规模校验）31 通过。

## 4. 修改文件

| 文件 | 变更 |
| --- | --- |
| `src/agenttest/requirement_ledger.py` | 数据引导词、`_continued_predicate`、数据/断言互斥、`RESPONSE_ANAPHOR_UNRESOLVED`、断言归属与见证 |
| `src/agenttest/request_requirements.py` | `permission_inquiry` 表达、`common_phrase`/`named_role`/`permission_inquiry_operations`、展开顺序校验 |
| `src/agenttest/product_focus.py` | `derive_permission_questions`、`permission_review_scope` 待审问答对、`interpretation` 去 CJK 约束 |
| `src/agenttest/product_design.py` | `interpretation`/`title` 去 CJK 约束，schema 与解码一致 |
| `src/agenttest/product_tasks.py` | 记录 `focus_presentation` 与设计 `presentation` |
| `src/agenttest/plan_construction.py` | 跨用例顺序：`design_operations` 传入构造上下文 |
| `src/agenttest/diagnostics.py` | `presentation_language` 观察器 |
| `tests/test_mixed_planning_p0.py` | 旧“双重用途”断言更新为“来源不是断言、无选择器断言仍待审” |
| `tests/test_mixed_planning_round3.py` | 新增 21 项确定性反例（4 类缺陷 + 安全负例） |
| `tests/test_round3_ab_evaluation.py` | 冻结评分器小规模校验；仓库脚本路径注入，兼容 `pytest.main` 收集 |
| `pyproject.toml` | 冻结证据脚本 `docs/evidence/**` 的 E402 豁免（证据文件不改） |

## 5. 测试结果

| 运行 | 结果 |
| --- | --- |
| 新增反例（修复后） | 21 passed |
| 冻结评分器/夹具校验 | 31 passed |
| 相关模块（需求抽取、台账、P0/C02-C03、gap、焦点、计划构造、引用） | 203 passed |
| 安全与产品（DSL 安全、可靠安全、任务范围、产品任务、产品设计） | 108 passed |
| `ruff check .` | All checks passed |
| 完整离线回归（4 进程，冻结源码 `3490637140a8c2293592bc9680ac4d4aa4877767fc2c3eb93793ae4dcc0b0ce3`） | **2435 collected / 2420 passed / 0 failed / 0 errors / 15 skipped**，精确覆盖 `exact_test_id_coverage=true`，wall 780.7 s，4 个进程退出码全 0 |

环境说明：Windows 11 + Python 3.11 `.venv`；本机无 Docker、无外部数据库，涉及外部服务的用例按既有标记跳过（数量见完整回归输出）；未执行任何真实模型调用或业务写入。

## 6. 剩余技术边界

1. **未重跑第三轮**：本轮所有结论都是离线的；第三轮分数保持原样，修复效果尚未在真实模型上验证。
2. **中文分句词典仍是启发式**：新增的表达（“将它响应里的/以该响应中的/依据该响应中的”等）来自第三轮原文；其他语言或其他动词框架仍可能落到“待审”而不是被执行——这是保守方向，但会体现为漏项。
3. **无选择器断言一律待审**：`该字段/它` 这类指代没有运营可见选择器时不会自动变成已批准断言，只能由运营在配置里确认。
4. **派生待审问题需要三重证据**：显式询问措辞 + 可解析的运营标签 + 具名身份；任一缺失仍整轮拒绝（`FOCUS_REQUIREMENT_GAP_WITHOUT_EVIDENCE`），不做猜测。
5. **跨用例顺序只在同一设计内成立**：设计被拆成多个可独立执行分组时，组间顺序仍由分组顺序决定，不具备事务性顺序语义。
6. **表述观察不参与评分**：`presentation` 只是记录；是否把非中文说明纳入质量门槛需要单独的产品决策。
7. **真实读写生命周期未验证**：本轮修复只涉及只读请求与规划。

## 7. 第四轮 A/B 计划（全新需求）

- **目的**：验证本轮修复在真实模型上的效果，且不复用第 1–3 轮任何已评分原文。
- **需求来源**：新写 4 条需求（D/I/G/P 各 1 类），措辞族与前三轮不同（例如“把它返回里的 …”“据此获取…”“按…的拒绝规则”“先…再…”的全新组合）；每条只使用新夹具里的接口与身份，逐条标注期望（可完整执行 / 应局部待审 / 应拒绝）。
- **夹具**：新建第 4 个 loopback 只读项目（不复用 META/FLOW/HOLD 的任何路径、标签或身份名），8–12 个 GET，含 1 条动态取值链、1 条跨用例顺序、1 条权限询问、1 条内容断言。
- **位置与预算**：4 条原文 × 2 版（baseline 修复前 / candidate 本次修复）× 2 轮 = 16 位置；每位置 ≤ 4 次模型、8 次目标 HTTP；预计 ≤ 64 次真实调用，冻结前预登记调用/Token 上限，不做预检调用。
- **冻结流程**：先冻结 evaluator 与夹具哈希，再冻结两版源码 SHA；跑完只做冷复算，不修复、不重跑、不替换失败位置。
- **验收口径**：以第三轮同类指标为准（错误拒绝、静默漏项、正确局部待审、任务范围越界、可完整执行），并新增“表述类错误拒绝必须为 0”；任一项不达标即判不接受。

## 8. API 读写生命周期验收判断

**暂不能开始。** 理由：
1. 本轮与第三轮的全部证据都是只读请求；写入需要独立的隔离命名空间、唯一资源前缀、清理与幂等证据，现有 loopback 夹具没有这些约束。
2. 第三轮的核心质量门槛（错误拒绝、静默漏项）尚未达标，先做写入验收会把“规划/断言缺陷”和“写入安全”混在一起，无法归因。
3. 写入需要新的显式授权（可写目标、清理策略、失败回滚、配额），当前里程碑未包含。

建议顺序：先做第 4 轮只读 A/B 并达到门槛 → 再单独立项“读写生命周期”，用专用隔离项目跑 create→verify→cleanup 与失败回滚。

## 9. 提交与推送

<<COMMIT>>
