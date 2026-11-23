# P3-B2.5 诊断修复：JSON 修复反馈与清理证据缺口

**范围**：本阶段只做确定性的诊断工作，不启动真实模型、不跑五轮验收、不进入 P3-B3。P3-B2.4 的
**NO-GO 结论不变**（五轮 0/5 达到 31/31）。

## 1. 证据保护

- `HEAD 43a6065`，工作区三项未提交文档（`p3b23`/`p3b24` NO-GO 记录、`progress.md`）原样保留。
- P3-B2.4 五轮原始证据完整：`.eduwork/p3b24-acceptance-43a6065/`（318 个文件、37.6 MB），
  逐轮含 `model-outputs.jsonl`（模型原文）、`phase-NN.json`（Trace、设计记录、清理结论、
  断言）、`target-events.jsonl`、`lab/workspace/tasks.sqlite3`（`model_usage_journal`、
  `task_events`、`http_execution` 账本）、`manifest.json`。
- 本轮只读这些文件，未改写、未删除；分析脚本另存于 `.eduwork/p3b25-analysis/`。

## 2. B1 根因：位置有了，缺什么结构没人说

### 2.1 真实语法错误

P3-B2.4 五轮共 **61 次设计调用**（v1 13、v2 14、v4 17、v5 17），其中 **14 次**被
`JSON_SYNTAX` 拒绝（v1 3、v2 4、v4 3、v5 4，占 23%）。逐条回放原文后，14 条全部是同一种
错误：`json` 报 `Expecting ':' delimiter`，即某个对象成员写成了 `"键"` 之后**直接跟下一个
成员**，缺了冒号和该成员自己的值。归档窗口一律长这样：

```json
{"location":"body","name","value":"测试包裹B","from_step":null,"select":null}
```

`name` 成员丢了 `:` 和值（v2 有一次是 `"name","quantity"`）。也就是说：**同一类模型笔误，
不是 14 种不同错误**；位置数值不同只因它落在不同的 `arguments` 数组下标处。

| 轮 | 失败位置 | 轮 | 失败位置 |
| --- | --- | --- | --- |
| v1 | 2043、1295、2509 | v4 | 2127、3677、2170 |
| v2 | 1295、1530、2844、2183 | v5 | 2742、2143、2044、5346 |

### 2.2 修复流程的六个问题

1. **真实错误是什么**：上面那条——成员缺冒号与值，属单点语法笔误。
2. **解析器能确定什么结构事实**：`position/line/column`、闭合 `reason=MISSING_COLON`、
   出错处前一个记号是不是一个"键位置"（前一个非空字符是 `{` 或 `,`）、以及该键是否为纯
   标识符。这足以点名"缺少冒号的成员"，且不需要回显任何模型文本。
3. **错误反馈是否进入下一次模型输入**：是。同一分组的下一次调用照常发生（v1 r3→r5、
   r6→r8、r14→r16；v4 r4→r20；v5 r2→r4 等），13/14 次的下一版就是合法 JSON。运行时的
   `repair_constraint()` 会把 `code/位置/原因` 拼进设计约束。**注意**：归档没有保存请求正文
   （`phase-*.log` 为空、`task_events` 只存阶段与请求数），所以"约束文本确实随请求发出"只能由
   代码路径与新增定向测试证明，不能由归档直接证明——这是证据边界，不当作已证事实。
4. **相同 position 是否对应相同输出摘要**：不是。14 条失败原文的 SHA-256 两两不同（例如 v1
   `657abded…`/`b2a08e40…`/`b4ca6b01…`），相同位置来自**相同的参数布局**，不是同一段输出重放。
   P3-B2.4 §13.6 里"同一位置连续 7 个监督轮"是错的：`phase-NN.json` 的 `design_records` 是
   累计快照，同一条记录被后续多个 phase 文件重复计入。见 §7 更正。
5. **模型是否收到明确的修复说明**：不完整。它收到 `position/line/column + reason` 和一句
   "只修正该处的语法"，但没有任何"这里该写什么形状"的信息；修复提示还自相矛盾地以
   "detail 给出了…" 开头而没有下文可核对。
6. **是否存在反馈丢失、截断或覆盖**：JSON 这条链路上没有截断——`_json()` 序列化工具结果时
   永远完整保留 `error`（含 `detail`），`repair_constraint()` 实测 538 字符，远低于 900 的
   上限。但存在**两处真实的信息缺口**：
   - JSON 诊断本身只到"位置 + 原因"，缺结构事实（本轮修的就是这处）。
   - **案例级预检拒绝**（`OPENAPI_UNKNOWN_BODY_FIELD` 等）会把 `preflight_diagnostic`
     完整存进案例记录，却在 `run_test_group` 的拒绝载荷里**只回一个 code 和一句通用提示**，
     结构化事实（`allowed_body_fields`、`unknown_body_positions`、`step_id`、`fields`）没有
     发给模型。见 §8.2。

## 3. 修复：结构化修复事实（不改接受条件）

`src/agenttest/plan_json.py` 的 `JSON_SYNTAX` 诊断由 4 个字段扩到 7 个，全部由解析器的偏移
与闭合词表推导，**不回显被拒文本**：

| 字段 | 修前 | 修后 |
| --- | --- | --- |
| `position/line/column` | 有 | 不变 |
| `reason` | 有（`MISSING_COLON` 等 9 个闭合值） | 不变 |
| `parser_error_type` | — | `JSON_DECODE_ERROR` |
| `expected_structure` | — | `{kind, expected, at_end}`，缺冒号时加 `after_key` |
| `repair` | — | 一句可执行的改法 |

实际输出（P3-B2.4 归档原文回放）：

```json
{"position": 1295, "line": 1, "column": 1296, "reason": "MISSING_COLON",
 "parser_error_type": "JSON_DECODE_ERROR",
 "expected_structure": {"kind": "OBJECT_MEMBER", "expected": "COLON_THEN_VALUE",
                        "at_end": false, "after_key": "name"},
 "repair": "对象的键后面必须有冒号和值；在该位置补上“:”与该成员的值。"}
```

- `after_key` 只在**偏移确实落在键位置**且键名是纯标识符（`^[A-Za-z_][A-Za-z0-9_]{0,39}$`）
  时出现；带空格、超长、或位置无法证明的键一律不点名。
- 另加一条确定性判断：`Expecting property name…` 但下一个非空字符是 `}`/`]` 时，实际是
  **成员列表尾部多了逗号**，于是给出 `MEMBER_LIST/NO_TRAILING_COMMA` 而不是"键缺引号"。
- 呈现层同步：`obligation_gate._diagnostic_text()` 把 `结构=OBJECT_MEMBER/COLON_THEN_VALUE
  （缺少冒号的键=name）；改法=…` 拼进运行时修复约束；`AgentSession._repair_hint("JSON_SYNTAX")`
  明确指向 `expected_structure`/`repair` 字段。

前后对比（同一份归档原文、同一批次策略）：

```
修前： code=JSON_SYNTAX；位置=第1行第1296列（第1295个字符）；原因=MISSING_COLON。
修后： code=JSON_SYNTAX；位置=第1行第1296列（第1295个字符）；原因=MISSING_COLON；
       结构=OBJECT_MEMBER/COLON_THEN_VALUE（缺少冒号的键=name）；
       改法=对象的键后面必须有冒号和值；在该位置补上“:”与该成员的值
```

## 4. 历史样本回放

新增 `tests/fixtures/p3b25/`，两条都是 P3-B2.4 的**逐字原文**（未截断、未改写）：

| 文件 | 来源 | 字数 | 失败位置 |
| --- | --- | --- | --- |
| `p3b24-v1-negative-batch-3.json` | release-v1 `negative_batch_3` r6 | 1,785 | 1295 |
| `p3b24-v2-negative-batch-3.json` | release-v2 `negative_batch_3` r4 | 1,642 | 1295 |

两条在修复后都得到 `reason=MISSING_COLON`、`after_key=name`、非空 `repair`，序列化后的
诊断里**没有** `测试包裹` 之类的字面值，也没有 `"value"`（紧随其后的记号）；`position` 与
`json` 原生偏移完全一致。

## 5. 是否改变了 JSON 接受语义

**没有。** 判定仍然是"`json.loads` 成功即通过"：解析器调用、`SYNTAX_REASONS` 闭合词表、
`JSON_SIZE`/`JSON_DUPLICATE_KEY`/`JSON_NONFINITE_NUMBER`/`JSON_STRUCTURE_LIMIT` 的限制一个
都没动；没有宽松解析、没有尾逗号容忍、没有单引号容忍、没有把非法 JSON 自动接受。新增测试
把这一点写成断言：

- 合法 JSON 仍解析成功（含中文值与 `null`）；
- `{"name" "value"}`、`{"a":1,}`、`{"a":1,,"b":2}`、`{"a":01}`、`{'a':1}` 仍全部
  `JSON_SYNTAX`；
- `{"x":1,"x":2}` 仍是 `JSON_DUPLICATE_KEY`；
- 只新增诊断字段，不触及批准义务、预算、重试次数与 `NO_PROGRESS`。

## 6. 三项 `CLEANUP_UNPROVEN` 的真实原因

v1 的 `createParcelB/C/D` 属于 `group_1_case_3/4/5`（正向生命周期组，不是负向批次）。逐项核对：

| 项 | createParcelB | createParcelC | createParcelD |
| --- | --- | --- | --- |
| 1 创建请求 | `POST /parcels` | `POST /parcels` | `POST /parcels` |
| 2 创建结果 | 201，资源 `parcel_d9a514712b9b` | 201 | 201 |
| 3 删除请求 | `DELETE /parcels/{parcelId}` | 同 | 同 |
| 4 删除回执 | 204 | 204 | 204 |
| 5 清理回读 | **没有执行**（唯一的读在删除之前：200 存在性检查） | 没有（PATCH 200 在删除前） | 没有（PUT 200 在删除前） |
| 6 缺失原因 | 方案只排到"删除"为止，没有删除后的 absence 回读 | 同 | 同 |
| 7 持久化账本 | `write_effects` 行 `status=CLEANUP_UNPROVEN`、`basis=["LIFECYCLE_CLEANED","HTTP_201","DELETE_RECEIPT_WITHOUT_READ_BACK"]`、`diagnostic.basis=DELETE_RECEIPT_WITHOUT_READ_BACK` | 同 | 同 |
| 8 最终库存 | 靶场末态 `record_count=0` | 同 | 同 |

三个案例的 `cleanup_evidence` 都是 `[]`、`cleanup_requests=0`、`absence_expectations` 里只有
`direction="EXISTS"`（删除前存在性），没有任何 `direction="absence"` 的条目。

**分类：B——清理回读没有执行。** 不是 A（已删除但证明没落盘）：根本没有回读可落盘；
不是 C（回读失败/超时）：没有发出回读请求；不是 D（身份无法关联）：`resource_lifecycle`
带着 `create_step`、`cleanup_step`、`owner_run_id`、`id_sha256`，与创建记录一一对应。

`record_count=0` 说明靶场没有真实残留，但按既有规则**不据此改写判定**：204 不是清理证明，
三项继续保持 `CLEANUP_UNPROVEN`，v1 的 `WRITE_EFFECTS` 安全项失败保持有效。

## 7. 更正 P3-B2.4 §13 的两处表述

1. **"同一位置连续 7 个监督轮重复"不成立**。`phase-NN.json` 的 `design_records` 是累计列表，
   同一记录会被后续每个 phase 文件重复计入；按 `model_call.local_call_id` 去重后，v1 是 13 次
   设计调用 / 3 次 `JSON_SYNTAX`，v2 是 14 / 4，v4 是 17 / 3，v5 是 17 / 4。实际重试次数由
   `MAX_GROUP_RUNS=3` 约束，只有 v2 `negative_batch_7` 用满 3 次仍未产出合法 JSON。
2. **"2–5 项负向策略因此永远没有执行证据"也不准确**。14 次 `JSON_SYNTAX` 里 13 次的下一次
   调用就成功了；真正让义务停在 `NOT_EXECUTED` 的是**案例级预检拒绝**（见 §8.1）与
   v2 `batch_7` 的"形式错误用尽三次尝试"这两类原因。

判定（0/5、NO-GO、五轮证据全部保留）不受影响；变的是归因与下一步修法。

## 8. A4 只读分析：四种终止情形要分开看

### 8.1 逐项归类（P3-B2.4 五轮）

| 情形 | 定义 | 本轮证据 |
| --- | --- | --- |
| **1 形式错误导致的无效调用** | 调用本身没产出可用方案，但运行时仍可重试 | `JSON_SYNTAX` 14 次（14/61 设计调用），`MODEL_SCHEMA_MISSING_FIELD` 3 次；13 次重试成功，1 次（v2 `batch_7`）用尽三次 |
| **2 批准义务确实无法推进** | 已批准策略在当前授权/前置条件下无可执行形态 | v1 `not_covered`："negative_1…negative_10 缺少已确认的拒绝规则与预期状态"；v5："本组已批准策略中没有对应的负向规则" |
| **3 已批准的终止行为** | 规则明确许可的停止 | v3：Focus 两次被拒后零业务请求、`NEEDS_REVIEW`；v2：`NO_PROGRESS`（执行预算连续无进展）；v1/v4/v5：`ANSWER` 收尾后由门禁判定 |
| **4 有可用修复机会却提前结束** | 分组仍可派发、案例可修，但运行在重试上限/停滞计数处终止 | v1 `negative_batch_6`（3 个案例全被预检拒，之后没有新设计）、v5 `negative_batch_6`（4+3 个案例两轮同样报 `OPENAPI_UNKNOWN_BODY_FIELD`）、v4 `negative_batch_1`（4 个案例两轮同样 `POLICY_ARGUMENT_MISMATCH`）、v2 `batch_7` |

案例级拒绝的真实分布（四轮共 **32** 个被拒案例，逐条按 `case_id` 计数）：

| 代码 | 次数 | 模型看到的结构化事实 |
| --- | --- | --- |
| `OPENAPI_UNKNOWN_BODY_FIELD` | 24 | 案例记录里有 `fields`、`allowed_body_fields`、`unknown_body_positions`、`step_id`，**但拒绝载荷只回 code** |
| `POLICY_ARGUMENT_MISMATCH` | 4 | 同上，`fields` 为空 |
| `OPENAPI_OBJECT_FIELDS` | 2 | 同上 |
| `OPENAPI_VALUE_LENGTH` | 1 | 同上 |
| `DESIGN_ABSENCE_PRECONDITION_MISSING` | 1 | 同上 |

（早期草稿写的 "31 个 / 27 个" 是漏计一行 `negative_batch_6_case_*_2` 的笔误，§11 已按逐
`case_id` 复核更正。）

即：**情形 1 的主要代价不是 JSON，而是"解析成功、预检拒绝"这一类语义错误拿不到可执行的
修复事实**；模型于是用同样的形状再提交一次（v5 `batch_6` 的 `_2` 后缀案例就是第二轮产物）。

### 8.2 方案一：保留终止机制，只改善错误反馈

> 本轮已按本方案实施（仅案例级反馈传递），实现与实测见 **§11**。

- **改动**：`run_test_group` 的"全部案例被拒"分支（`product_agent.py:829-836`）把
  `cases[0].preflight_diagnostic` 的 `fields`/`detail`（含 `allowed_body_fields`、
  `unknown_body_positions`、`step_id`、`step_index`）以及每个被拒案例的
  `{case_id, reason, fields}` 一并放进 `error`；`_repair_hint` 为这些 code 各加一句
  "按 allowed_body_fields 删掉多余字段/按 fields 修正该位置"。JSON 侧已在本轮完成。
- **安全性**：不改接受条件、不改重试上限、不改 `NO_PROGRESS`；只增加模型可见的诊断文本，
  且全部来自已脱敏的 `Diagnostic`（字段路径 + 允许值名，不含请求值）。
- **预算影响**：单次调用请求体略增（估计每个被拒案例 < 200 字节），调用次数不变。
- **完成率影响**：不保证提高；乐观情形是把"两轮同样错误"变成"第一轮就修对"，减少
  情形 1 的浪费；保守情形是无变化（模型仍可能忽略反馈）。**不能表述为完成率提升**。

### 8.3 方案二：重复形式错误时改变终止策略

- **改动**：同一分组在**同一形式错误码**上第二次失败时，立即把该组标成 `NOT_DISPATCHED`
  并写清原因（code + 次数 + 最后一次结构化事实），不再消耗第三次尝试；`decide()` 的
  `OBLIGATION_ATTEMPTS_EXHAUSTED` 分支同时记录"被谁耗尽"。
- **安全性**：更早终止=更少尝试，不会新增业务请求，也不会放宽任何判定；风险是**过早在
  真能修好的分组上放弃**（v4 `batch_4` 第三次才成功，v5 `batch_2` 第三次才成功），会把现在
  少数能救回的分组变成固定漏项。
- **预算影响**：省下每次失败调用（约 14k–20k tokens）；本轮若能省下 v4/v5 各 2 次，约
  60k–80k tokens。
- **完成率影响**：更早终止**不会**提高完成率，多数情况会略微降低（上述两次"第三次成功"
  会被砍掉）；收益是账面更诚实、浪费更少。
- **结论倾向**：方案二只在"同一错误码重复且反馈没有任何新信息"时才值得启用；与方案一组合
  时，应先用方案一把反馈补齐，再观察是否还需要方案二。

## 9. 本轮验证

| 检查 | 结果 |
| --- | --- |
| 新增 B1 定向测试 `tests/test_p3b25_json_repair.py` | 13 passed |
| 历史 JSON 失败样本回放（2 条逐字原文） | 位置/键/结构事实全部命中，无字面泄漏 |
| 解析器与反馈相关测试（`test_diagnostics`、`test_obligation_gate`、`test_archive_json_budget`、`test_json_literals`） | 通过 |
| A1/A2/A3 定向测试（`test_p3b24_diagnostics`、`test_p3b24_references`、`test_p3b23_diagnostics`、`test_product_agent`） | 通过 |
| 合并运行 8 个测试文件 | **174 passed** |
| `ruff check`（改动文件 + 新测试） | All checks passed |
| `git diff --check` | 干净 |
| 未运行 | 全量冻结回归、真实模型调用、真实业务靶场 |

## 10. 下一轮最小修复建议

1. **~~先修案例级拒绝的反馈~~（本轮已实施，见 §11）**：只在案例级拒绝载荷里补白名单事实，
   不改预检条件、不改重试与预算。
2. **再评估方案二**：只有在方案一上线后仍有"同一错误码重复且无新信息"的样本时才启用，
   且必须先有"不降低完成率"的证据。
3. **清理证据（报告，不实施）**：预检目前允许"创建后只删除、没有删除后 absence 回读"的
   方案通过，导致运行结束时才暴露 `CLEANUP_UNPROVEN`。若要前移，需要在预检阶段对"计划了
   创建、且该资源有已批准 absence 策略、却没有删除后回读"的方案显式拒绝并给出可修复 code；
   这会改变校验条件，必须先报告再实施。
4. **B2**（`FOCUS_SOURCE_CLAUSE_MISSING` 码名与多键语义）留待 A4 之后。

## 11. 案例级预检拒绝反馈（本轮实施）

### 11.1 根因

P3-B2.4 的真实拒绝发生在**预检**而不是设计阶段：`negative_batch_6` 这类分组的设计记录是
`PROPOSED`、分组状态是 `DESIGNED`，随后每个案例在发送请求前被拒。此时 `run_test_group`
走"全部案例被拒"分支，而该分支的 `error` 只有
`{code, message, retryable, hint}` —— `code` 来自 `cases[0].reason`，`hint` 是同一句通用话术。

`product_tasks` 其实已经在该案例行上写好了 `preflight_diagnostic`：

```json
{"layer":"openapi","code":"OPENAPI_UNKNOWN_BODY_FIELD",
 "fields":["plan.steps.0.arguments.1.name"],
 "detail":{"step_id":"create_for_name_too_long","step_index":0,"obligation_id":"web_rule_3",
           "allowed_body_fields":["meta","name","quantity"],"unknown_body_positions":[0]},
 "kind":"SCHEMA_MISMATCH"}
```

它随 `payload.cases[]` 一起下发，但没有进入 `error`：模型读到的"这次为什么被拒"仍然只有一个
代码。另一层风险是直接把这个 `detail` 整体拼进提示——那是未经筛选的结构，`Diagnostic.detail`
只限制了键数，不限制值的形状。

### 11.2 修改文件

| 文件 | 改动 |
| --- | --- |
| `src/agenttest/case_diagnostics.py` | **新增**：案例级拒绝的白名单投影（`case_failure`/`project_cases`/`rejection_error`/`public_diagnostic`/`repair_kind`） |
| `src/agenttest/product_agent.py` | "全部案例被拒"分支改用 `rejection_error(...)`；新增一行 import |
| `tests/test_p3b25_case_diagnostics.py` | **新增** 21 项定向测试 |
| `tests/fixtures/p3b25/cases-archive.json` | **新增** 归档原文案例行（v1/v4/v5，逐字） |

不新增 Schema 校验器：分类与事实全部复用预检已经写入的 `preflight_diagnostic`，模块只做
"挑选 + 分类 + 定长"，绝不重新判定合法性。

### 11.3 真实案例的修复前后

`release-v5 / negative_batch_6`（归档原文）：

| | 修前 | 修后 |
| --- | --- | --- |
| `error.code` | `OPENAPI_UNKNOWN_BODY_FIELD` | 同（不丢失） |
| 字段路径 | — | `fields: ["plan.steps.0.arguments.1.name"]` |
| 合法字段名 | — | `allowed_body_fields: ["meta","name","quantity"]` |
| 未知字段下标 | — | `unknown_body_positions: [0]` |
| 归属步骤/义务 | — | `step_id: create_for_name_too_long`、`obligation_id: web_rule_3`、`policy_ids: ["web_rule_3","web_rule_7_negative_4"]` |
| 修复类别 | — | `repair_kind: UNKNOWN_FIELD` |
| 可执行提示 | "核对必填参数或绑定后提供新的设计约束…" | "该对象只接受 allowed_body_fields 中的字段名：删除 fields 指出的多余成员（body 下标见 unknown_body_positions），其余步骤、策略与预期不变。" |
| 字节 | 177 B | 769 B（单案例）/ 1,924 B（7 案例，已按上限截为 4 + `omitted_cases: 3`） |

全部 32 个被拒案例回放后的覆盖度：

| 指标 | 数量 |
| --- | --- |
| 被拒案例总数 | 32 |
| 现在带字段路径（`fields`） | 21 |
| 现在带合法字段名（`allowed_body_fields`） | 18 |
| 仍只有代码 + 类别 + 提示（预检未记录路径，不臆造） | 5（4× `POLICY_ARGUMENT_MISMATCH`、1× `DESIGN_ABSENCE_PRECONDITION_MISSING`） |
| 单次拒绝载荷最大值（真实分组，7 案例） | 1,924 B |

### 11.4 反馈字段映射表

| 输出键 | 来源 | 允许的形状 | 上限 |
| --- | --- | --- | --- |
| `code` | `case.reason` → `preflight_diagnostic.code` → `PLAN_REJECTED` | `^[A-Z][A-Z0-9_]{0,95}$` | 1 |
| `case_id` | `case.case_id` | 标识符（`AKIA…`/长十六进制/密钥词一律拒绝） | 1 |
| `repair_kind` | 关闭映射（见下） | 6 个值之一 | 1 |
| `fields` | `preflight_diagnostic.fields` | 掩码路径：标识符/`*`/≤2 位下标，逐段校验 | 8，超出计数进 `omitted.fields` |
| `step_id` | `detail.step_id` → `case.step_id` | 标识符 | 1 |
| `obligation_id` | `detail.obligation_id` | 标识符 | 1 |
| `policy_ids` | `case.planned_policy_ids` | 标识符，排序去重 | 6，超出 `omitted.policy_ids` |
| `step_index` | `detail.step_index` | 0–999 整数 | 1 |
| `allowed_body_fields` / `allowed_fields` / `unknown_fields` / `missing_fields` / `allowed_from_steps` | 同名 `detail` 键 | Schema 字段名标识符 | 6，超出 `omitted.<key>` |
| `unknown_body_positions` | 同名 `detail` 键 | 0–99 整数 | 6，超出 `omitted.<key>` |
| `parameter_location`/`expected`/`expected_type`/`actual_type`/`constraint`/`violation`/`reference` | 同名 `detail` 键 | 闭集枚举词 `^[A-Z][A-Z0-9_]{0,63}$` | 1 |
| `limit` / `allowed_count` | 同名 `detail` 键 | 0–999 整数 | 1 |
| `hint` | 本模块的固定文案（按 code 优先、否则按类别） | 常量 | 1（与顶层相同则只保留顶层） |
| `omitted_cases` | 超出的案例数 | 整数 | 1 |

`repair_kind` 的关闭映射（只映射代码本身能证明的）：

| 类别 | 判定 |
| --- | --- |
| `REFERENCE_ERROR` | 记录里有 `reference`（`ReferenceDiagnostic` 闭集） |
| `UNKNOWN_FIELD` | `OPENAPI_UNKNOWN_BODY_FIELD`、`OPENAPI_OBJECT_FIELDS`、任何 `*_SCHEMA_EXTRA_FIELD` |
| `MISSING_FIELD` | 任何 `*_SCHEMA_MISSING_FIELD` |
| `TYPE_MISMATCH` | 任何 `*_SCHEMA_TYPE_OR_CONSTRAINT` |
| `ARGUMENT_LOCATION` | `POLICY_ARGUMENT_MISMATCH`（该项只比较名字集合与已批准绑定） |
| `OTHER` | 其余；值域类（长度/上下界/枚举/前置条件）给按 code 的专门提示，不冒充类型错误 |

白名单之外的键一律丢弃：白名单之外新增一个 `detail` 键不会自动泄露。

### 11.5 脱敏验证

| 要求 | 做法 | 验证 |
| --- | --- | --- |
| 合法字段名可出现 | `allowed_body_fields` 等按标识符放行 | 归档 `["meta","name","quantity"]` 原样出现 |
| 请求字段值不得出现 | 白名单键之外全部丢弃 | `request_value: "测试包裹B"` 注入后不出现 |
| API Key / Authorization 不得出现 | `SECRET_SHAPED` 对**所有**字符串生效（`AKIA…`、`sk-…`、长十六进制、`secret/token/password/…` 词根），叠加在标识符模式之上 | `step_id: "AKIA5SECRETVALUE"` 被丢弃，`step_id` 不出现而不是回显 |
| 放进字段名槽的测试值不得回显 | 同上 + 路径逐段校验 | `allowed_body_fields: ["name","secret value!","AKIA…"]` → 只剩 `["name"]` |
| 位置/路径/Schema 信息可保留 | 掩码路径与整数下标放行 | `plan.steps.0.arguments.1.name` 保留 |
| 不接受/修复非法请求 | 不触碰预检，只读取已写入的诊断 | 预检测试仍拒绝、`dispatched == []` |
| 不改变预检通过条件 | 未改动任何预检代码 | 140 项预检/门禁/agent 测试通过 |
| 被拒案例 0 次业务 HTTP | 拒绝分支在预检之后、派发之前 | `requests == 0`、`ledger.requests` 不变 |

### 11.6 反馈完整性与体积

- 每个被拒案例至少产出 `{case_id, code, repair_kind}` + 顶层提示；`code` 永不丢失，来源优先级
  `reason → preflight_diagnostic.code → PLAN_REJECTED`。
- 案例顺序 = 计划顺序（不排序案例，避免错位）；`policy_ids` 排序去重；字段名去重保序。
- 上限：案例 4、字段名 6、下标 6、路径 8；超出部分写进 `omitted`/`omitted_cases` 计数，
  不静默丢弃。测试断言"7 个案例 → 保留 4 + `omitted_cases: 3`""10 个字段名 → 保留 6 +
  `omitted.allowed_body_fields: 4`"。
- 体积：真实最坏 1,924 B；单测断言上限 3,000 B。`_json()` 的 `error` 仍整体保留，因此这些
  事实不会被 6 KB 预算截掉。
- 重试次数与预算未变：仍由 `runs`/`MAX_GROUP_RUNS`/`formal_code`/`NO_PROGRESS` 决定。

### 11.7 是否改变预检接受语义 / Runtime 行为

| 问题 | 结论 |
| --- | --- |
| 预检接受语义 | **未变**。没有新增/删除/放宽任何预检条件，没有引入第二套 Schema 校验器，被拒案例依旧被拒、依旧 0 业务请求 |
| Runtime 执行行为 | **未变**。`retryable`、`code`、`group.status`、重试上限、`NO_PROGRESS`、obligation gate、预算、60 秒超时、清理判定、未知写入不重放全部未改；只多了一个 `error.cases` 字段和更具体的 `error.hint` |
| JSON 接受规则 | 未变（B1 只扩诊断字段） |
| Focus / HITL / 负向策略 | 未触碰 |

### 11.8 本轮定向验证

| 检查 | 结果 |
| --- | --- |
| 新增案例级诊断测试 `tests/test_p3b25_case_diagnostics.py` | 21 passed |
| B1 JSON 修复测试 `tests/test_p3b25_json_repair.py` | 13 passed |
| 四个新测试 + B1 + A1/A2/A3（`test_p3b23_diagnostics`、`test_p3b24_diagnostics`、`test_p3b24_references`） | 84 passed |
| 预检/门禁/Focus/agent（`test_composed_preflight`、`test_rw_acceptance_preflight`、`test_obligation_gate`、`test_product_agent`、`test_product_focus`、`test_focus_references`、`test_focus_approved_expectations`） | 140 passed, 1 xfailed |
| 合并 15 个文件（B1 + 案例级 + 解析器 + 预检 + A1/A2/A3，最终一次运行） | **288 passed, 1 xfailed**（1 项为既有 `xfail(strict=True)`） |
| `ruff check` | All checks passed |
| `git diff --check` | 干净 |
| 未运行 | 全量冻结回归、真实模型调用、五轮真实验收 |

### 11.9 剩余风险

1. **模型仍可能忽略反馈**：本轮只证明信息到达，未证明模型会用它；要证明必须等下一次授权的
   真实批次。
2. **5 个案例仍只有代码**：`POLICY_ARGUMENT_MISMATCH` 的预检没有记录差异字段名（它只比较
   名字集合）。要给出具体名字需要在 `policies.py` 里补 `detail`，属于预检改动，需先报告。
3. **`payload.cases[].preflight_diagnostic` 仍是未过滤的原始结构**（本轮 §12 已修复）
4. **`SECRET_SHAPED` 会误丢**：连续 12 位以上大写/数字的合法标识符（罕见）会被丢弃。方向是
   安全侧失败（少给事实，不泄漏）。

## 12. 模型输入安全与完整性专项（本轮实施）

### 12.1 完整数据流：三层，只有后两层会离开本机

| 层 | 位置 | 内容 | 本轮结论 |
| --- | --- | --- | --- |
| L1 本地归档 | `summary["cases"][].preflight_diagnostic`、`summary["design_records"][].diagnostic.detail`、`write_effects` | 预检/编译器写入的原始诊断，含 `window`、`hint`、`message`、`request_value` 等**原样**内容 | **保持原样**（审计证据），仅 `run` 落盘；任何修改都会破坏回放证据 |
| L2 工具结果（模型上下文） | `AgentSession.run_group` → `_json(payload)` → `StructuredTool` 返回字符串 → `ToolMessage.content` | `payload.error`、`payload.cases[]`、`payload.design`、`payload.obligation_coverage`、`payload.next_step` | 本轮修复：逐字段投影 |
| L3 模型端点请求 | `EcnuxChatModel._payload` → `_encode` → `POST {base_url}/chat/completions` | `messages[]`（system + tool）+ `tools` | 实测：L2 原文即 L3 正文，因此 L2 修好即 L3 安全 |

承载失败诊断的字段共 5 处：`error.cases`（本轮新增）、`error.detail`（设计层）、
`payload.cases[].preflight_diagnostic`、`payload.design.detail`、`supervision` 文本中的
`_diagnostic_text(...)`；另外 `focus_repair_payload` 的 `detail` 也是 L2 字段。

### 12.2 审计发现：原始诊断确实外发（修复前）

用合成哨兵值（`sk-audit…`、`Authorization: Bearer …`、`Bearer …`、`审计测试包裹B`、
`AKIAauditSYNTHETIC0001`、`SENTINEL-error-message-含敏感内容`）注入 L1 存储，再驱动真实
编码器（`EcnuxChatModel` + `httpx.MockTransport`，无网络、无真实凭据）测量三层：

| 场景 | 修复前 L2 工具结果 | 修复前 L3 请求体 | 泄漏来源 |
| --- | --- | --- | --- |
| A 案例级预检拒绝 | 5/6 哨兵出现（1,856 B） | 5/6 哨兵出现（3,101 B） | `payload.cases[].preflight_diagnostic` 原样复制 |
| B 设计层拒绝 | 4/6 哨兵出现 | 4/6 哨兵出现 | `design.detail` 与 `error.detail` 原样复制 |

`api_key` 哨兵在两层都不出现（没有生产者写它，且 `_encode` 会拒绝含凭据的请求体）。

### 12.3 修复：模型输入侧投影（不改 L1、不改预检）

新增 `src/agenttest/model_input.py`：按**值**投影，而不是只按键。

| 规则 | 行为 |
| --- | --- |
| 事实槽（任何不在 `TEXT_KEYS` 的键） | 只放行标识符、点分路径、枚举词、小整数、布尔、null；其它字符串**丢弃并计数**（`omitted`） |
| 文本槽（封闭集合：`hint`/`message`/`window`/`quote`/`negative_literal_hint`/`schema`/`violated_constraint`/`expected_rule`/`constraint`/`violation`/`expected`/`expected_type`/`actual_type`/`repair`/`reason`） | 保留，但凭据形状就地脱敏为 `[已脱敏]`，长度上限 240 |
| 凭据形状 | `Authorization: Bearer …`、`Bearer …`（含前缀）、`api_key=…`/`token=`/`secret=`/`cookie=` 赋值、`sk-…`、`AKIA/ASIA…`、32+ 位十六进制、JWT |
| 容器 | 每层最多 10 项、每个 detail 最多 12 键、序列化上限 2,400 字符；超限写 `omitted` 计数，不静默丢弃 |

接入点（4 处，全部是 L2）：

| 位置 | 改动 |
| --- | --- |
| `product_agent._case_summary` | `preflight_diagnostic` 改为 `public_diagnostic(...)`（案例级白名单，见 §11）；分组全部被拒时该字段置 `null`（事实已在 `error.cases`，避免重复占预算） |
| `product_agent.run_group` 设计分支 | `design.fields/detail` 与 `error.fields/detail/hint` 改用 `model_input.diagnostic_view(...)` |
| `obligation_gate._diagnostic_text` | 渲染结果再走 `redact(...)`，长度上限 600 |
| `product_tasks.focus_repair_payload` | 合并后的 `detail` 走 `safe_detail(..., items=2*MAX_REPAIR_PATHS+4, text=2000)`，多出的丢弃计数写 `omitted_detail` |

### 12.4 修复后：同一套哨兵值的暴露情况

| 哨兵 | L1 归档 | L2 工具结果 | L3 请求体 |
| --- | --- | --- | --- |
| `sk-auditSYNTHETIC…` | 未出现 | 未出现 | 未出现 |
| `Authorization: Bearer …` | **保留** | 未出现（案例槽）/ `[已脱敏]`（文本槽） | 未出现 |
| `Bearer …` | **保留** | 未出现 | 未出现 |
| `审计测试包裹B`（请求值） | **保留** | 未出现 | 未出现 |
| `AKIAauditSYNTHETIC0001`（字段名槽） | **保留** | 未出现 | 未出现 |
| `SENTINEL-error-message-含敏感内容`（塞进 `hint`） | **保留** | 仍出现 | 仍出现 |

最后一行是本轮明确保留的边界：`hint` 是应用自写的修复提示槽（归档里就是"补上删除步骤或去掉该
断言。"这类常量），内容级过滤无法把"规则句子"和"被塞进去的非凭据短语"分开；能证明的是
**凭据形状在该槽也会被脱敏**，而**事实槽一个值都进不去**。要彻底消除该槽风险，需要把 `hint`
换成按 code 的封闭提示表——那会丢掉 A1/A2/A3 的案例级修复提示，属行为改动，未做。

### 12.5 七案例反馈完整性（本轮改规则）

| 旧规则 | 新规则 |
| --- | --- |
| 最多 4 个案例，其余只给 `omitted_cases` 计数 | 前 4 个给完整事实；第 5–16 个给**紧凑行** `{case_id, code, repair_kind, compact: true}`；超过 16 才计 `omitted_cases` |
| 模型只能看到 4 个案例 | `error.cases_total` + `error.compact_cases` + 每个案例的 `case_id`/`code`，7 个案例全部可定位 |

真实 7 案例分组（`release-v5/negative_batch_6`）：4 条完整 + 3 条紧凑，`cases_total: 7`，
`compact_cases: 3`，体积 1,924 B（旧 177 B）。16 案例上限下体积 ~7.9 KB（其中 6 KB 是既有
`_json` 预览机制，非本轮引入）。

### 12.6 合法字段候选的截断诚实性（本轮改规则）

- 任何被截断的列表都带 `<key>_total` 与 `<key>_truncated: true`，并在 `omitted` 里计数。
- 截断后的提示改为"**只列出了部分**：total 是完整数量……**未列出的字段名不代表不合法**……
  不要因为没列出来就删掉合法字段"，不再出现"只接受这些字段名"的完整性断言。
- 优先保留与失败直接相关的候选：`allowed_body_fields` 截断前按"出现在 `fields` 路径里的名字
  优先，其余保持声明顺序"重排（确定性）。
- 不放宽 Schema 校验：预检条件一字未改，投影只影响模型看到什么。

### 12.7 离线验证（19 个文件，344 passed / 1 xfailed）

| 场景 | 覆盖 |
| --- | --- |
| 单案例拒绝 | `release-v5/negative_batch_6_case_1`，字段路径 + 3 个合法字段名 |
| 七案例同时拒绝 | 4 完整 + 3 紧凑，7 个 `case_id` 全部出现在请求体字节里 |
| 多种错误码混合 | `OPENAPI_UNKNOWN_BODY_FIELD` / `OPENAPI_OBJECT_FIELDS` / `POLICY_ARGUMENT_MISMATCH` / `OPENAPI_VALUE_LENGTH` / `DESIGN_ABSENCE_PRECONDITION_MISSING` |
| 十个以上合法字段 | 12 个 → 6 + `_total: 12` + `_truncated: true` |
| 敏感字段名 | `AKIAauditSYNTHETIC0001`、`token` 被丢弃并计数 |
| 敏感请求值 | `审计测试包裹B` 不进入 L2/L3 |
| 未提供字段路径的历史案例 | `release-v4/negative_batch_1_case_1` 仍给 `code` + `repair_kind` + 提示，不臆造路径 |
| 多个案例关联不同 `obligation_id` | 两案例各自保留 `policy_ids`，不串位 |
| 非法案例仍被拒、业务 HTTP 为 0 | 每个端到端用例断言 `payload["requests"] == 0` 且 `ledger.requests == 0` |

新增 `tests/test_p3b25_model_input.py`（14 项）：9 项在**真实编码器产出的 POST 字节**上断言
（不是只断言 `rejection_error` 返回值），5 项是投影单元测试（脱敏、事实槽丢弃、项/键/文本/
序列化上限、`redact` 对真实诊断词零改动）。

| 检查 | 结果 |
| --- | --- |
| `tests/test_p3b25_case_diagnostics.py` | 23 passed |
| `tests/test_p3b25_model_input.py` | 14 passed |
| B1 `tests/test_p3b25_json_repair.py` | 13 passed |
| A1/A2/A3 + agent + 门禁（9 文件） | 203 passed, 1 xfailed |
| 预检/解析/证据 + 账本（10 文件） | 141 passed |
| `ruff check src tests` | All checks passed |
| `git diff --check` | 干净 |
| 未运行 | 全量冻结回归、真实模型调用、五轮验收 |

### 12.8 是否改变接受/拒绝条件

| 项 | 结论 |
| --- | --- |
| 预检接受/拒绝 | **未变**。`product_tasks`、`product_checks`、`policy` 的判定逻辑未改；`focus_repair_payload` 只改"拒绝之后给什么"，不改"是否拒绝" |
| Schema / JSON / Focus 接受规则 | 未变 |
| HITL、批准义务、负向策略、预算、60 秒超时、`NO_PROGRESS`、obligation gate、清理判定、未知写入不重放 | 未变 |
| 模型可见字段 | 变了（这是本轮的目的）：`error.cases` 新规则、`_total`/`_truncated`、`payload.cases[].preflight_diagnostic` 改为白名单、`design.detail` 改为投影 |
| 本地归档 | 未变：`summary` 里的原始诊断一字未改（测试断言哨兵值仍在 L1） |

### 12.9 剩余风险

1. **文本槽是内容过滤，不是封闭集合**：`hint`/`message`/`window` 里被塞进去的非凭据短语仍会
   到达模型。凭据形状会被脱敏；彻底封闭需要改成按 code 的提示表（会丢 A1/A2/A3 的案例级提示）。
2. **`_json` 的预览会重复前部内容**：`error` 完整保留 + `preview` 取前 3 KB，极端 16 案例下
   工具结果约 7.9 KB。属既有机制，本轮未改（改动会影响已冻结的预算测试）。
3. **`SECRET_SHAPED`/`SECRET` 可能误丢**：混大小写的 `AKIA…`、`sk-…` 现在也会被丢弃；方向仍是
   安全侧失败。
4. **未证明模型会用上新反馈**：需要下一次授权的真实批次。

## 13. 清理证明预检（本轮实施）

### 13.1 现有清理证明契约（先确认，再改）

| 问题 | 结论（以代码与归档为准） |
| --- | --- |
| 哪些资源必须清理 | 目标 `resource_lifecycles` 里每个 `create_operation` 产生的资源。`lifecycle.py` 的 `lifecycle_plan` 已强制 `set(creates) == set(deletes)`（`LIFECYCLE_PREPARATION_AND_CLEANUP_REQUIRED`），所以"创建了却没删除"不会被当成完成 |
| 哪些 absence 证明策略已获批准 | 两种形态：**(a)** 已批准策略的 `assertions.status.codes ⊆ {404,410}`（`absence_expectation`）；v1 里是 `web_rule_5_expectation_1`（`GET /parcels/{parcelId}` → 404，`OPERATOR_EXPECTATION_RULE`，绑定 `parcelId ← web_rule_3/$.id`）。**(b)** 关联键计数观测 `negative_post_observations` / `positive_post_observations`，但 `observation_receipts()` 只认**被拒写**（`negative` 策略），对"已执行创建 + 删除"不是清理证明，故本轮不作为清理证明 |
| 什么样的回读可以证明不存在 | 已批准的 404/410 回读**实际执行、通过**，且位于该资源的删除之后；`write_effects._absence_verified` 还要求 `absence_expectations[].identities[].producer == 创建步骤` 且 `row.deletions` 含本次 `cleanup_step` |
| 删除与回读的依赖 | 回读的路径参数必须绑定到**创建步骤**本身（`from_step=<create>, select=$.id`）；`lifecycle_plan(require_fresh_reads=False)` 只允许读取已创建的标识，因此合法回读天然依赖创建 |
| 身份如何从创建传到回读 | `ArgumentBinding(from_step=<create 步骤 id>, select=rule.id_selector)` → 执行时 `CreationJournal` 记录 `resource_id`/`id_sha256`；`LifecycleJournal` 记 `cleanup_step`，回读以同一绑定对齐同一 `id_sha256` |
| 已有哪些合法计划不能被新规则拒绝 | ①已经有"删除后 404 回读"的计划；②批量路径里由 `add_batch_cleanup` 自动补齐回读的计划；③只读计划；④目标没有 `resource_lifecycles` 的项目；⑤已批准负向 POST（不产生资源） |

### 13.2 规则（精确触发条件）

新增诊断码 **`DESIGN_CLEANUP_READ_BACK_MISSING`**（`Layer.POLICY`）。对一个计划里的**每个**
生命周期创建步骤 `c`（`c.operation_id` 是某个 `resource_lifecycle.create_operation`、
`c` 的策略非负向、方法为 `POST`、不是登录操作），令 `id = (c.id, rule.id_selector)`：

1. **实际创建资源**：满足上面的条件（负向 POST 与被拒请求不算）；
2. **需要清理**：该操作有 `resource_lifecycle` 规则（没有规则的项目不检查，不扩大清理义务）；
3. **已有批准的 absence 策略**：存在**恰好一个**策略 `p`，满足 `absence_expectation(p)`、
   `GET`、路径与 `delete_operation` 相同、`c.role ∈ p.roles`、只有一条 `path_parameters`
   绑定且 `name == rule.path_parameter`、`source_policy_id == c.policy_id`、
   `source_selector == rule.id_selector`；一个都没有 → **不拒绝**（无可批准的合法回读）；
   多于一个 → 视为歧义，同样不拒绝，交给人工；
4. **计划包含删除**：存在 `delete_operation` 的非负向删除步骤，其 `path_parameters[rule.path_parameter]`
   的 `(from_step, select)` 等于 `id`；取**最后一个**这样的删除作为基准；
5. **缺少删除之后的合法回读**：不存在位于该删除之后、`policy_id == p.id`、
   绑定等于 `id` 的步骤。

1–4 全真且 5 不成立 → 预检拒绝。诊断内容：

| 字段 | 值 |
| --- | --- |
| `code` | `DESIGN_CLEANUP_READ_BACK_MISSING` |
| `fields` | `plan.steps.<删除步骤下标>.path_parameters.<参数名>`（模型要改的位置） |
| `detail.create_step` / `delete_step` | 创建、删除步骤 id |
| `detail.lifecycle_create` / `lifecycle_delete` / `lifecycle_parameter` | `createParcel` / `deleteParcel` / `parcelId` |
| `detail.obligation_id` = `detail.read_policy` | 缺失证明对应的已批准策略 id |
| `detail.proof_kind` / `required_position` | `ABSENCE_READ_BACK` / `AFTER_DELETE` |
| `detail.missing` | `[read_policy]` |
| `detail.hint` | "删除步骤 X 之后缺少已批准的缺失回读……补一个位于删除之后的回读，用 404/410 证明该资源已不存在。删除返回 204 不能单独算清理完成，最终库存为 0 也不能替代逐资源证明。" |

不会新增业务请求：拒绝发生在 `validate_design_case`（预检）阶段，派发之前；本地归档
（`summary`）保持原样。

### 13.3 v1 B/C/D 原始计划：修复前后

用归档项目（`web_d045b57c…` 包裹靶场）+ 归档的 `plan_structure` 重放（`.eduwork/p3b25-analysis/v1_cleanup_read_back.py`）：

| 案例 | 原始计划 | 归档运行结果 | 新预检（同一计划） |
| --- | --- | --- | --- |
| `createParcelB` | create → read(200) → delete | `CLEANUP_UNPROVEN`（`DELETE_RECEIPT_WITHOUT_READ_BACK`） | **拒绝** `DESIGN_CLEANUP_READ_BACK_MISSING`，`read_policy=web_rule_5_expectation_1` |
| `createParcelC` | create → change → delete | 同上 | **拒绝**，同上 |
| `createParcelD` | create → replace → delete | 同上 | **拒绝**，同上 |

同一计划在四种反事实下的行为（三案例一致）：

| 变体 | 结果 |
| --- | --- |
| 原计划（证明已批准） | 拒绝 |
| 把批准撤掉（无 absence 策略） | 通过（`NO_APPROVED_READ`；不能要求未批准的请求） |
| 删除后补 404 回读 | 通过（`PROVEN`） |
| 回读放在删除之前 | 拒绝 |
| 回读绑定到别的资源 | 拒绝 |
| 删除后改用已批准 200 读 | 拒绝（200 不是缺失证明） |
| 交给 `add_batch_cleanup` 补齐 | 通过（自动加 `batch_cleanup_read_2`） |

**真实根因**：v1 的批量补齐（`add_batch_cleanup`）本来会给这三个计划补上
`batch_cleanup_read_2`，但那次运行走的是没有触发补齐的设计路径
（`verify_batch_cleanup=bool(group["policy_ids"])` 未成立），于是计划停在了删除凭据上。
新预检不依赖那条路径：只要缺证明就拒绝。

### 13.4 全归档影响面（四个验收轮，92 份报告）

`.eduwork/p3b25-analysis/archived_plan_scan.py` 逐份重放：

| 结果 | 数量 | 含义 |
| --- | --- | --- |
| `PROVEN → 通过` | **48** | 已带删除后 404 回读的计划，规则不动它们 |
| 不涉及（无生命周期创建） | **41** | 只读/负向用例，`rows == []` |
| `READ_BACK_MISSING → 拒绝` | **3** | 只有 v1 的 B/C/D |

即：本轮规则在全部历史归档中**只命中已知的三份缺陷计划**，没有误伤任何既有合法计划。

### 13.5 安全边界（逐条对应测试）

| 边界 | 实现 | 测试 |
| --- | --- | --- |
| DELETE 204 不单独算清理完成 | 回读缺失即拒绝；提示里点名 204 | `test_a_deleted_resource_without_its_proof_is_refused_with_locatable_facts` |
| 最终库存为 0 不能替代逐资源证明 | 只认同一身份的 404/410 回读；计数观测不算 | 同上 + 13.1(b) |
| 回读必须对应同一身份 | 比较 `(from_step, select)` | `test_a_read_of_another_resource_cannot_prove_this_one` |
| 删除前的回读不能当证明 | 要求 `offset > 最后一个删除` | `test_a_read_before_the_deletion_is_not_a_cleanup_proof` |
| 不允许跨资源借证据 | 同上（身份不匹配即不算） | `test_a_read_of_another_resource_cannot_prove_this_one` |
| 不允许跨任务借证据 | 规则只看当前 `Scenario`；执行侧 `_cross_case_cleanup` 要求 `owner_run_id` 相同（未改） | 既有 `test_write_effects` 套件 |
| 不允许未批准的 absence 策略 | 只接受唯一匹配的已批准策略；200 读不算 | `test_a_read_approved_for_a_different_expectation_is_not_a_proof` |
| 不允许循环依赖 | 回读只能绑定创建步骤（`lifecycle_plan`），不引入新边 | 既有 `test_resource_lifecycle` |
| Runtime 清理判定不变 | 未改 `write_effects`/`lifecycle` 一行 | `test_the_runtime_still_judges_the_same_unproven_cleanup` |

### 13.6 脱敏（新诊断走既有白名单）

- 诊断里的所有事实都是标识符/枚举：步骤 id（DSL 限制 `^[A-Za-z][A-Za-z0-9_]{0,60}$`）、
  操作 id、参数名、策略 id、`ABSENCE_READ_BACK`/`AFTER_DELETE`；
- 模型输入侧仍走 `diagnostic_view` → `safe_detail`：事实槽只放行标识符/枚举，文本槽
  （`hint`）脱敏凭据并限长；
- 新增的 9 个 detail 键已加入案例白名单（`case_diagnostics.FACTS`），密钥形状的值会被丢弃并计数；
- 测试：`test_every_new_fact_survives_the_case_whitelist_and_drops_secrets`（把
  `AKIA…`/`Bearer …`/请求值塞进事实槽 → 全部丢弃并计数，`proof_kind` 与提示仍在）；
  `test_the_refusal_reaches_the_model_without_any_business_request`（真实 `run_group` 载荷：
  `requests == 0`、`ledger.requests == 0`、本地归档保留原样）。

### 13.7 hint 来源核查（只读，不改）

`src/agenttest` 里共 **38 处** hint 赋值（`.eduwork/p3b25-analysis/hint_audit.py`）：

| 来源 | 处数 | 说明 |
| --- | --- | --- |
| 应用常量 | 30 | 固定句子 |
| 封闭表 `HINTS`/`CODE_HINTS` | 4 | 按 `kind`/`code` 查表 + 固定截断后缀 |
| `instance_design.violation_hint` | 1 | 插值的是批准 Schema 的关键字、上限、批准字段名 |
| `_refusal(..., hint=)` 参数 | 1 | 调用方传常量或下面的聚合值 |
| `detail["hint"] or _repair_hint(code)` | 2 | 聚合：生产者写的 hint（本期新增者亦为标识符插值） |
| 本轮新增 | 1 | 只插值步骤 id / 参数名 / 策略 id |

结论：**没有发现**用户原文、模型原文或请求体值能直接进入 hint 的路径；`focus_repair_payload`
的 `hint` 来自应用常量，`merged` 里的模型侧内容只会进入受控事实键（经 `safe_detail` 投影）。
残余风险（与 §12.4 相同）：`hint` 是文本槽，未来若有生产者把自由文本写进去，会被脱敏和限长，
但不会被拒绝——这需要改成按 code 的封闭提示表，属行为改动，未做。

### 13.8 是否改变接受/拒绝条件与 Runtime 判定

| 项 | 结论 |
| --- | --- |
| 新增拒绝条件 | 只有一条：**存在已批准 absence 回读、计划删除了同一身份、但删除后没有该回读** |
| 预检其它条件 | 未改（Schema、策略授权、`validate_cleanup_dependencies`、`validate_expectation_preconditions` 均原样） |
| Runtime 清理判定 | **未改**：`write_effects.classify`/`_absence_verified`/`cleanup_message`、`lifecycle.py`、`executor.py` 一行未动；`CLEANUP_UNPROVEN` 的既有 basis 不变 |
| HITL、预算、终止、重试、未知写入不重放、obligation gate、60 秒超时、清理 reserve | 未改 |
| 业务 HTTP | 未新增：拒绝在派发前；三个归档计划若重放，请求数为 0（单测断言 `requests == 0`、`ledger.requests == 0`） |

### 13.9 修改文件与验证

| 文件 | 改动 |
| --- | --- |
| `src/agenttest/product_preconditions.py` | 新增 `cleanup_read_back_report`、`validate_cleanup_read_back`、`approved_absence_read`、诊断码与状态常量；`precondition_report` 未改 |
| `src/agenttest/product_design.py` | `validate_design_case` 在 `validate_cleanup_dependencies` 之后调用新检查（预检期） |
| `src/agenttest/product_agent.py` | `_repair_hint` 增加该 code 的可执行修法 |
| `src/agenttest/product_tasks.py` | 拒绝原因允许列表 + 该 code 的公开说明 |
| `src/agenttest/case_diagnostics.py` | `FACTS` 新增 9 个清理证明事实键；`CODE_HINTS` 新增案例级提示 |
| `tests/test_p3b25_cleanup_read_back.py` | 新增 14 项（含设计路径集成、真实 `run_group` 载荷、脱敏、Runtime 判定不变） |
| `tests/test_negative_post_observation.py` | 正向观测用例的计划补上已批准 404 回读（该计划原本没有清理证明）；断言未变 |
| `tests/test_p3b25_model_input.py` | 确定性用例排除墙钟字段 `seconds_left`，其余逐字节比较（原用例偶发失败） |

| 检查 | 结果 |
| --- | --- |
| 新增 `tests/test_p3b25_cleanup_read_back.py` | **14 passed** |
| 生命周期/身份/HITL/清理（`test_resource_lifecycle`、`test_rw_lifecycle_preparation`、`test_rw_lifecycle_next_round`、`test_write_effects`、`test_negative_post_observation`、`test_workspace_identity`）+ 模型输入 + B1 + 案例级 + A1/A2/A3（12 文件） | **157 passed** |
| 预检/设计/门禁/Focus/任务（`test_composed_preflight`、`test_rw_acceptance_preflight`、`test_product_agent`、`test_product_design`、`test_product_supervisor`、`test_obligation_gate`、`test_product_focus`、`test_product_tasks`） | **177 passed, 1 xfailed**（既有 strict xfail） |
| Focus/预算/账本/完成判定（6 文件） | **111 passed** |
| `ruff check src tests` | All checks passed |
| `git diff --check` | 干净 |
| 未运行 | 全量冻结回归、真实模型调用、五轮验收 |

### 13.10 剩余风险

1. **自动补齐只在批量路径生效**：`add_batch_cleanup` 仍由 `verify_batch_cleanup` 控制；本轮没有
   把它推广到所有设计路径（那会改变既有计划的步骤组成，属行为改动）。
2. **多候选 absence 策略视为歧义而不拒绝**：两个以上可证明策略时本轮选择"不拒绝"，理由是
   无法确定唯一合法证明；若要强制，需要先定义策略优先序（属授权语义改动）。
3. **关联键计数观测仍不是清理证明**：`observation_receipts` 只服务被拒写；把它扩展成"删除后
   count=0 即证明"会改 Runtime 判定，未做。
4. **规则只在设计/预检期生效**：直接构造 `Scenario` 走 `FixedPlan`/`saved_tests` 的路径不经过
   `validate_design_case`；这些路径本来也不能保存无清理证明的写回归（`lifecycle_plan` 会拒绝）。

## 14. 全量离线冻结回归（本轮执行一次）

### 14.1 冻结前顺序核查："删除之后"是否等于"删除之后执行"

结论：**是**。`plan.steps` 的下标就是执行顺序，不存在运行期拓扑重排；"声明在删除之后"
与"在删除之后执行"是同一件事，因此 13.2 的第 5 条判定成立。证据分三层。

**代码层**

| 事实 | 位置 |
| --- | --- |
| 执行器按下标顺序执行，没有任何排序 | `executor.py:194` `steps = scenario.steps`；`executor.py:234` `for step in steps:` |
| 前向引用被**拒绝**，不是被重排 | `dsl.py:199-204` `DEPENDENCY_FORWARD_OR_UNKNOWN`（`binding.from_step not in seen`） |
| 构造期对"来源声明在本步骤之后"给的是修复诊断，不是重排 | `plan_construction.py:55-59` `order_constraint`、`:215-216`、`:231-232` |
| IR 编译期产出稳定拓扑序，数组本身就是依赖序 | `planning_compiler.py:8` |
| 新检查跑在**最终** Scenario 上（三种插入之后），返回的正是被执行的那个对象 | `product_design.py:1014-1027` |

**探针层**（`.eduwork/p3b25-freeze/ordering_probe.py`，真实 lab 工作区 + Mock 传输，0 网络，0 模型）

| 计划 | 线上顺序 | 回读看到 |
| --- | --- | --- |
| create → **read → delete** | `POST, GET, DELETE` | `200`（资源还在），`ASSERTION_FAILED` |
| create → delete → **read** | `POST, DELETE, GET` | `404`，`PASS` |

即：把回读声明在删除前面，它**真的先执行**，只能看到 200；这不是"误判"，而是计划本身
无法证明清理。

**位置无关性**（同一探针前半段，纯预检）

| 变体 | 结论 |
| --- | --- |
| 合法顺序（create→delete→read） | `PROVEN`，通过 |
| 不相关步骤放在第 0/2/3/4 位（delete 与 read 之间、read 之后等 4 种） | `PROVEN`，通过（判定不变） |
| 缺回读（不相关步骤在第 0 位/末位） | `READ_BACK_MISSING`，拒绝（判定不变） |
| 回读在删除之前 | 拒绝 |
| 同一操作两次创建，各自删除、各自回读 | 两行都 `PROVEN` |
| 只给 A 回读 | 只有 `createA` 为 `PROVEN`，`createB` 仍 `READ_BACK_MISSING` |
| 把 A 的回读绑到 B 的身份 | A 仍 `READ_BACK_MISSING`（不串位，不借用） |

身份键是 `(创建步骤 id, id_selector)`，删除按"绑定源 == 该创建"匹配，回读按同一身份 + 删除后的
下标匹配；因此同操作多实例、同资源多次删除、无关步骤移位都不会改变结论。**未发现缺陷，未修改
预检规则。**

### 14.2 冻结身份

| 项 | 值 |
| --- | --- |
| 分支 / HEAD | `master` / `43a60657282999b93a69157e993cb135cc422530`（"fix: improve focus repair diagnostics and reference guidance"） |
| Git 可见文件 | 1711 个（`git ls-files --cached --others --exclude-standard`） |
| 冻结清单摘要 | `454ec39ac27c794ec4747accd2bf194016587666b5454d26e2db1d21e39a1245`（对"文件→SHA-256"映射取 SHA-256） |
| 脚本自身摘要 | `checkout_sha256 = 0ad4d94793a281665790ed579b4d52f2542076266eb16268653ca473255d4bab`（`eval_v2.freeze.checkout_identity` 的历史双十六进制格式，与上一条口径不同，不可比较） |
| 工作区 | 10 个已修改 + 8 个未跟踪（见 §14.4 表格）：`docs/p3b23`、`docs/p3b24`、`docs/progress`、`obligation_gate`、`plan_json`、`product_agent`、`product_design`、`product_preconditions`、`product_tasks`、`test_negative_post_observation`；新增 `docs/p3b25`、`case_diagnostics.py`、`model_input.py`、`tests/fixtures/p3b25/`、4 个 P3-B2.5 测试文件 |
| P3-B2.3 历史 NO-GO 文档 | `docs/deepagents-hitl-reliability-p3b23.md` SHA-256 `f700932df8e6e8955eaaef83caa63bf5fa85ccad121131b9d086f2dbc060545a`，回归前后均未变 |
| 并行进程 | 执行前 `python*` 进程数 = 0 |
| 冻结脚本 | `scripts/run_frozen_regression.py` 只写 `--out` 目录；输出目录在 Git 忽略的 `.eduwork/` 下；`before`/`after` 由脚本自算 |

### 14.3 执行

```
.\.venv\Scripts\python.exe -X utf8 scripts/run_frozen_regression.py \
    --out .eduwork/p3b25-freeze/regression-20261118T141436Z
```

`{"exit_code": 0, "same_checkout": true, "audit_exists": true}`。执行一次，未重跑。

### 14.4 结果审计

| 审计项 | 结果 |
| --- | --- |
| 退出码 | `0` |
| 汇总 | `3012 passed, 15 skipped, 1 xfailed, 1 warning in 2268.36s (0:37:48)` |
| collected / reported | `3028` / `3028`（一致） |
| missing / extra / repeated / nonunique | 全部为空 |
| 回归前后 checkout | `before == after`：同 commit、同 `checkout_sha256`、同 `worktree_status` |
| 源码/测试/配置漂移 | 无（脚本自算 `same_checkout: true`；`git status` 与冻结前 18 行逐条一致） |
| P3-B2.5 新增测试 | 64 项全部收集、全部通过（用例级 23、清理预检 14、B1 13、模型输入 14） |
| 警告 | 1 条既有 `PytestCollectionWarning`（`product_design.py:55` 的 `TestDesign` 被 `test_product_instance_prompt.py` 误当测试类收集），非本轮引入 |
| 耗时 | pytest 2268.36 s；wrapper 22:14:46→22:52:41（37 分 55 秒） |

| 领域 | 收集 | 失败 | 跳过/xfail |
| --- | --- | --- | --- |
| A1/A2/A3 诊断（`test_p3b24_diagnostics`） | 24 | 0 | 0 |
| 案例级预检反馈（`test_p3b25_case_diagnostics`） | 23 | 0 | 0 |
| B1 JSON 修复（`test_p3b25_json_repair`） | 13 | 0 | 0 |
| 模型输入脱敏（`test_p3b25_model_input`） | 14 | 0 | 0 |
| 清理证明预检（`test_p3b25_cleanup_read_back`） | 14 | 0 | 0 |
| 生命周期（3 文件） | 37 | 0 | 0 |
| 清理/写副作用（`test_write_effects`、`test_negative_post_observation`） | 16 | 0 | 0 |
| HITL（`test_human_review`、`test_step_recovery`、负向观测） | 27 | 0 | 0 |
| obligation gate | 28 | 0 | 0 |
| 预算 | 13 | 0 | 0 |
| 账本 | 6 | 0 | 0 |
| 完成判定 | 25 | 0 | 0 |
| 产品 Agent / 设计 / 监督者 | 78 | 0 | 0 |
| Focus（3 文件） | 64 | 0 | 1（既有 strict xfail） |
| JSON 预算 / 字面量 | 46 | 0 | 0 |
| 预检（composed / rw acceptance） | 17 | 0 | 0 |

跳过与 xfail 全部是既有的平台/可选依赖条件，不是本轮新增：

- 15 skip：POSIX symlink/FIFO/inode/nofollow（Windows 上不适用）与 `junction`/共享语义的
  反向条件、`schemathesis`/`langgraph` 的 `importorskip`；
- 1 xfail：`tests/test_product_focus.py::test_real_task_guard_preserves_extracted_text_without_repairing_model_plan[EnglishName-0]`，源码里是 `strict=True`，若意外通过本轮会失败；
- 4 个 P3-B2.5 测试文件内没有任何 `skip`/`xfail` 标记。

**没有发现**测试绕过、放宽安全断言或违规外发：环境里不存在任何模型凭据变量，冻结脚本强制
`LANGSMITH_TRACING=false`/`LANGCHAIN_TRACING_V2=false`，套件本身离线（Mock/自建 loopback）。
本轮改过的两个既有测试仍保留原断言（`verification.requests == 1`、`observations[0].http_status == [200]`、
`matching_resources == 1`；确定性用例除墙钟字段外逐字节比较）。

### 14.5 最慢测试（仅记录，不据此宣称性能变化）

| 耗时 | 测试 |
| --- | --- |
| 95.70 s | `test_pocketbase_acceptance.py::test_full_denominator_is_cold_regraded_and_resealed_wrong_results_fail` |
| 64.23 s | `test_generic_arguments.py::test_new_tasks_archive_rescores_and_rejects_resealed_regrading` |
| 62.54 s | `test_generic_references.py::test_new_reference_tasks_use_shared_runner_and_cold_regrading` |
| 61.10 s | `test_root_response_planning.py::test_fresh_root_controls_and_simulated_trials_are_frozen_and_cold_verified` |
| 60.81 s | `test_reference_diagnostics.py::test_fresh_diagnostic_tasks_reuse_runner_and_cold_regrading` |
| 49.46 s | `test_json_literals.py::test_development_archive_keeps_nine_positions_and_can_be_cold_scored` |

单次结果不足以判断性能趋势；历史基线 `2744 passed / 15 skipped` 的口径与本轮 `3012 passed`
不同批（其间新增 P3-B2.3/2.4/2.5 测试），故只报告事实，不做因果结论。

### 14.6 判定

**P3-B2.5 达到离线 PASS**：源码/测试/配置零漂移，3028 项收集=上报，0 失败，0 漏收集，0 重复，
0 非唯一，新增 64 项全部收集并通过，安全相关领域全部通过，历史 NO-GO 与归档均未改动。

具备申请下一批真实模型验收的条件（仅指"可以申请"）：本轮没有调用任何真实模型、没有向真实
目标发请求、没有执行五轮验收，这些仍需新的显式授权。
