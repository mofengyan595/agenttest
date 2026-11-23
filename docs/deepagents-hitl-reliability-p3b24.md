# P3-B2.4 离线根因诊断

2026-11-17。P3-B2.3 冻结真实验收为 **0/5**（发布门限 4/5），结论仍为 **NO-GO**，P3-B3 未获准。本阶段只做离线诊断：不启动真实模型调用、不启动完整回归、不启动新的五轮验收，不修改 60 秒超时或模型调用预算，不删除或放宽任何 Focus 约束。全部结论来自既有归档的只读回放。

## 结论摘要

1. **3/5 到 0/5 的变化不能归因于 P3-B2.3 的代码修改。** 首次 Focus 请求在两个里程碑里是**逐字节相同**的（47,360 字节，SHA-256 `897e1ed241ce6a8f…c66870f99`），共 10 次观测：P3-B2.2 的 4 次全部通过、1 次超时；P3-B2.3 的 4 次通过、2 次被拒、1 次超时。同一输入的输出差异来自模型采样与服务端延迟，不是提示词、Schema 或候选集合的变化。
2. **确证 3 项 Harness 缺陷、1 项终止规则交互缺陷**，均有归档样本级离线复现（见 §7）。最关键的一项是：一次 Focus 提案同时含两个**互相独立**的违规时，校验器只报第一条，而修正次数只有一次；修正链把唯一一次机会用在第一条上，第二条随后直接判死整轮。
3. **模型侧的失败占多数**：`JSON_SYNTAX` 四次逐字节复现；预检的 `OPENAPI_UNKNOWN_BODY_FIELD` 是模型把测试值写进字段名槽位；`arguments[1].name` 一类错误在两轮里都出现。
4. **三次超时的服务端状态与用量仍然不可核实**（`UNKNOWN`，不推定为零）。同一 Focus 请求在别处以 2.8–34.7 秒完成，说明超时不是请求本身的性质；`UNKNOWN` 门禁按规则终止了 v2、v5。
5. `NO_PROGRESS` 在 v3 是按配置规则触发的（`no_progress_rounds=3` / 上限 3），但它在**仍有保留的形式修复次数**时终止了整轮，这是规则交互问题，不是"没有触发"的缺陷。

## 1. 证据、范围与只读性

- HEAD `f311721`（"Record P3-B2.3 diagnostic verification"）；P3-B2.3 的代码提交为 `77b5cbd`，冻结源码摘要 `4e088caa…`。
- 未提交改动：`docs/deepagents-hitl-reliability-p3b23.md`、`docs/progress.md`。本轮只新增本文档与 `.eduwork/p3b24-analyze/` 下的分析脚本，未修改任何源码、未暂存、未提交。
- 归档：`.eduwork/p3b23-acceptance-f311721/`（五轮，含 `model-outputs.jsonl`、`phase-*.json`、`lab/workspace/tasks.sqlite3`）与 `.eduwork/p3b22-retest-20261117T052048Z-d4a1f9a3/`（同构）。两处 SQLite 一律以 `mode=ro&immutable=1` 打开。
- 回放方式：`RecordingModel` 在生成时写下的 `context` 就是完整校验上下文，因此可以用现有 `TestFocusModel._decode_output`、`TestDesignModel._decode_output`、`construct_plan` 在同一输入上重放历史输出。回放期间模型服务与目标 HTTP 调用均为 0。
- 已知限制：`model-outputs.jsonl` 用 `default=str` 序列化上下文，`planning_ir` 变成 pydantic repr 字符串，无法直接用于实例输入路径；本文用 `phase-05.json` 的 `planning_ir` 投影重建了 `literal_slots` 所需的最小对象，`source_start` 等字段不在投影中但不参与该路径。
- 分析脚本（都可重复执行，共 26 个，全部在 `.eduwork/p3b24-analyze/`）：本文表格由 `calls.py`（61 次调用表）、`latency.py`（吞吐）、`fingerprints.py`（跨轮同请求）、`focus_observations.py`（10 次同字节 Focus 观测）、`ledger.py`／`timeouts.py`（账本与 `UNKNOWN` 行）、`batches.py`（逐批次义务状态）、`list_records.py`／`v2_preflight_view.py`（原始输出视图）、`v2_instance_replay.py`、`focus_ab.py`、`focus_numeric_probe.py`、`focus_boundary_probe.py`、`construct_ab.py`、`repair_context.py` 生成。

## 2. 逐轮失败事件时间线

时间均为北京时间（UTC+08:00）。五轮窗口：v1 16:38:01.98–16:38:27.60、v2 16:38:29.09–16:42:51.76、v3 16:42:53.35–16:46:04.57、v4 16:46:06.27–16:50:15.68、v5 16:50:19.14–16:51:39.20。批次从预检到收尾合计 13 分 50.324 秒。

| 轮次 | 时刻（开始） | 事件 | 结果与去向 |
| --- | --- | --- | --- |
| v1 | 16:38:14.138 | Focus 第 1 次（`2a3074c9…`，47,360 B，5,781 ms，14,375 tokens） | `FOCUS_SOURCE_CLAUSE_MISSING`，`fields=[requirement_map.request_2]` |
| v1 | 16:38:20.027 | Focus 第 2 次（`950eb774…`，49,032 B，6,843 ms，14,899 tokens） | `FOCUS_INPUT_TEXT_CHANGED`，`fields=[requested_checks.1.exact_value]`；无第二次修正 → 16:38:27.6 `NEEDS_REVIEW`，0 次业务 HTTP |
| v2 | 16:38:40.911 | Focus 通过（`611e359b…`，6,469 ms） | — |
| v2 | 16:39:00.957 | `group_1` 第 1 次（`4cab790c…`，3,313 ms） | `DESIGN_INSTANCE_LITERAL_INVALID`：`instance=ai_5`、`slot=arg_1`、`required=[]` |
| v2 | 16:39:51.858 | `group_1` 第 2 次（`8a3d598a…`） | 通过，7 个实例构造完成 |
| v2 | 16:40:15.942 | `negative_batch_1`（`2f81b687…`，32,718 B） | 4 项提交：3 项实测、1 项 `OPENAPI_UNKNOWN_BODY_FIELD`（`web_rule_3_negative_10`）|
| v2 | 16:40:37.029 | `negative_batch_2`（`be97cc3e…`） | 3 项实测、1 项同上（`web_rule_3_negative_6`）|
| v2 | 16:41:10.661／16:41:29.270 | `negative_batch_3` 2/2、`negative_batch_4` 4/4 | 全部实测 |
| v2 | 16:41:50.792–16:42:50.812 | `negative_batch_5` 第 1 次（`6ff62a16…`，38,456 B） | `MODEL_TIMEOUT`/`DEADLINE`，60,032 ms，HTTP 缺失，用量 `UNKNOWN` → 16:42:51.8 `BLOCKED / MODEL_SERVICE_UNAVAILABLE`，19/31 |
| v3 | 16:43:04.769 | Focus 通过（`c9ce8b9d…`，14,359 ms，本轮最慢） | — |
| v3 | 16:43:26.601 | `group_1` 通过（`db29db33…`） | 7 个实例 |
| v3 | 16:43:54.038 | `negative_batch_1` 第 1 次（`b70568c9…`） | `JSON_SYNTAX/MISSING_COLON`，position 3048 |
| v3 | 16:44:03.042 | `negative_batch_1` 第 2 次（`bbcdaa3a…`） | 通过，4/4 |
| v3 | 16:44:28.461／16:44:50.391／16:45:08.573 | `negative_batch_2` 4/4、`negative_batch_3` 2/2、`negative_batch_4` 4/4 | 全部实测 |
| v3 | 16:45:31.520 | `negative_batch_5` 第 1 次（`69109a12…`，38,456 B） | `JSON_SYNTAX`，position 5235 |
| v3 | 16:45:44.739 | `negative_batch_5` 第 2 次（`9d855420…`，39,409 B） | `JSON_SYNTAX`，position 1969 |
| v3 | 16:45:54.619 | `negative_batch_6` 第 1 次（`ed87cd4c…`，38,521 B） | `JSON_SYNTAX`，position 1880 → 16:46:04.6 `PARTIAL / OBLIGATION_NOT_DISPATCHED`，`stop_reason=NO_PROGRESS`，21/31 |
| v4 | 16:46:20.729 | Focus 第 1 次（`653de8e3…`，47,360 B） | `FOCUS_SOURCE_CLAUSE_MISSING`，与 v1 同字段 |
| v4 | 16:46:28.618 | Focus 第 2 次（`7278f9ec…`，49,032 B） | 通过 |
| v4 | 16:46:45.997 | `group_1` 通过（`5d7d6372…`） | 7 个实例 |
| v4 | 16:47:11.925 | `negative_batch_1` 第 1 次（`2f63eac6…`） | `JSON_SYNTAX`，position 3202 |
| v4 | 16:47:23.540 | `negative_batch_1` 第 2 次（`95d267e3…`） | 通过，4/4；随后批次 2/3/4 全部实测 |
| v4 | 16:49:14.258–16:50:14.295 | `negative_batch_5` 第 1 次（`7d518f89…`，39,567 B） | `MODEL_TIMEOUT`/`DEADLINE`，60,047 ms，`UNKNOWN` → `BLOCKED`，21/31 |
| v5 | 16:50:38.221–16:51:38.284 | Focus 第 1 次（`ef68d9c5…`，47,360 B） | `MODEL_TIMEOUT`/`DEADLINE`，60,062 ms，`UNKNOWN` → `NEEDS_REVIEW`，0/31、0 次业务 HTTP |

`JSON_SYNTAX` 与 `MODEL_TIMEOUT` 都发生在**同一批次位置**（`negative_batch_5`，11 项 `replaceParcel` 已批准策略，请求最大 38–40 KB）。v3 在同一位置连续两次成功返回（11,404 / 10,900 tokens）但内容非法；v2、v4 在该位置直接超时。

## 3. Focus 拒绝原因表

P3-B2.3 的四份 Focus 原始输出（v1 两份、v4 两份）与一次超时，逐项如下。"合法提案是否存在"一列由 §5 的固定输入回放给出。

| 样本 | 调用 ID | 拒绝码 | 失败字段 | 诊断详情 | 模型实际写了什么 | 归因 | 合法提案存在？ |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v1 第 1 次 | `2a3074c9c7594dcabe980bece50422c6` | `FOCUS_SOURCE_CLAUSE_MISSING` | `requirement_map.request_2` | `expected_requirement_keys=[request_1]`、`trusted_scope_clauses=[request_2]` | 多报一条 `requirement_map.request_2`；24 项检查（`createParcel` 10、`changeParcel` 4、`replaceParcel` 10）**全部**引用 `source_3`，其中 11 项带非空取值（`"blue"`×4、`1`×3、`0`×2、`4`×2）或 `exact_length`（0、25） | 两条独立违规：多报键 + 引用范围子句却给了取值 | 是（§5 A1c） |
| v1 第 2 次 | `950eb7741bda434f9c5aac50e92fcaeb` | `FOCUS_INPUT_TEXT_CHANGED` | `requested_checks.1.exact_value` | — | `requirement_map` 已改对，但把 `"blue"`→`"x"`、`1`→`0`（`4` 与 `exact_length` 未动），仍引用 `source_3` | 模型改了值 + 仍引用范围子句 | 是（§5 A2b） |
| v4 第 1 次 | `653de8e3810e477788b3ec02f1c7a61d` | `FOCUS_SOURCE_CLAUSE_MISSING` | 同 | 同 | 同样多报 `requirement_map.request_2`，但 24 项检查全部引用 `source_1` 且取值为 `null`——**只差删键一处** | 模型多报键（与 v1 同一请求、不同错误组合） | 是（§5 B1/B2，第 2 次即通过） |
| v4 第 2 次 | `7278f9ec2e96438695518436089a1da9` | — | — | 通过 | 把引用改成 `source_1`（整段任务原文），字符串检查填 `null`、数值检查填 `1/0/4` | — | — |
| v5 第 1 次 | `ef68d9c572754c6fa1a69cf34563c8dd` | `MODEL_TIMEOUT` | — | `timeout_kind=DEADLINE` | 无响应 | 服务端（§6） | 无法判断 |

**v1 的两条被拒提案**有共同结构：模型把"范围说明子句"当成可引用来源，并给它配了具体取值。服务端的规则是"引用范围子句时 `exact_value` 必须为 null"（`product_checks.validate_requested_checks`，`product_focus.py` L413-414 的提示同义），但模型端的 wire schema **没有表达这条约束**：`constrain_scope_check_sources`（`product_sources.py` L273-305）里的 `if "source_ref" in properties:` 只对已转换成分支生效，模型看到的仍是 `source_quote` + 可选非空 `exact_value`。

v1 与 v4 的第一次尝试证明这是**两次独立采样**：请求字节相同，v4 只多报了 `requirement_map` 的键（引用与取值都正确），第二次就通过；v1 同时多报键**并**给 11 项范围子句检查配了取值，第一次修正只修掉了键，整轮死在第二个违规上。同一个请求、同一段提示，两种错误组合——这正是 §7 C1 说的模型侧不稳定。

## 4. `JSON_SYNTAX`、预检与 `NO_PROGRESS` 根因表

### 4.1 `JSON_SYNTAX`

| 样本 | 请求字节 | 诊断 | 下一次尝试 | 回放 | 归因 |
| --- | ---: | --- | --- | --- | --- |
| v3 `negative_batch_1` 第 1 次 | 32,718 | `position=3048`、`line=1`、`column=3049`、`MISSING_COLON` | 模型自撰 note"…position 3048，仅修复该处语法"，第 2 次通过 | 逐字节复现 | 模型生成错误 |
| v3 `negative_batch_5` 第 1 次 | 38,456 | `position=5235`（列 5236） | note"…position 5235"，第 2 次仍 `JSON_SYNTAX`（1,969） | 逐字节复现 | 模型重复同类错误 |
| v3 `negative_batch_5` 第 2 次 | 39,409 | `position=1969`（列 1970） | 无第 3 次（无进展停止） | 逐字节复现 | 同上 |
| v3 `negative_batch_6` 第 1 次 | 38,521 | `position=1880`（列 1881） | 未再派发 | 逐字节复现 | 同上 |
| v4 `negative_batch_1` 第 1 次 | 33,858 | `position=3202` | 第 2 次通过 | 逐字节复现 | 模型生成错误 |

- 四次都是同一形态：`"name","value":…` 少一个冒号，文档只有一行，位置落在同一处负载结构上。**解析器没有缺陷**：拒绝、位置、行列、原因都准确，且拒绝时 0 次业务 HTTP。
- **反馈通道有缺陷（B 类）**：JSON 解析的字符位置同时出现在两条通道上——分组工具结果直接带 `detail.position/line/column/reason`（`product_agent.py` L794-803），运行时自撰修复约束还会把它渲染成"位置=第1行第5236列（第5235个字符）"（`obligation_gate._diagnostic_text` L280-282）。模型随即照抄成"仅修复该处语法"。但修复请求**不包含上一版原文**——用归档上下文重建 v3 批次 5 第 2 次请求（38,299 字节，指纹 `6fd653c0…`，与归档一致），在其中检索被拒原文的标记串返回 `False`。因此"只改那一个位置"是不可执行的指令，模型只能整篇重生成，第二次的位置（1,969）与第一次（5,235）毫无关系。
- 措辞"仅修复该处语法"是**模型自己写的**（`agent_runtime.transcript` 第 22 条 TOOL_CALL 的 `design_note`），不是 Harness 写的；Harness 的问题是把一个模型无法定位的数字交给了它。

### 4.2 预检 `OPENAPI_UNKNOWN_BODY_FIELD`

| 归档 | 批次 | 义务 | 步骤 ID | 失败字段 | 允许的 body 字段 | 模型写出的实参 | 归因 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| P3-B2.3 v2 | `negative_batch_1` case 2 | `web_rule_3_negative_10` | `check_meta_type_mismatch` | `plan.steps.0.arguments.1.name` | `meta`、`name`、`quantity` | `{"location":"body","name":"有效包裹名","value":"有效包裹名"}` | 模型把测试值写进字段名槽 |
| P3-B2.3 v2 | `negative_batch_2` case 3 | `web_rule_3_negative_6` | `neg6_check` | 同上 | 同上 | `{"location":"body","name":"测试包裹","value":"测试包裹"}` | 同上 |
| P3-B2.2 v2 | `negative_batch_1` | `negative_10`／`negative_2`／`negative_3` | `neg_meta_type_mismatch` 等 | 同上 | 同上 | `["测试包裹乙","测试包裹乙"]`、`["测试包裹丙","测试包裹丙"]`、`["测试包裹丁","测试包裹丁"]` | 同上，同类错误 3 项 |

- 模型错误明确：出站 Schema 的 body 字段集合是 `meta`/`name`/`quantity`，模型用产品名当字段名。同类错误在 P3-B2.2 出现 3 次、P3-B2.3 出现 2 次（且 P3-B2.3 同一批次里另外 3 项写法正确），属**模型输出不稳定**，不是预检器缺陷。
- 诊断充分性：`step_id`、`step_index`、`obligation_id`、`allowed_body_fields`、`unknown_body_positions` 全部在拒绝记录里，模型只需按 `allowed_body_fields` 改写即可。P3-B2.3 的修复 C 已在真实归档中生效（`obligation_coverage` 明确标出 `PREFLIGHT_REJECTED`，`remaining_policy_ids` 只列剩余义务）。
- 循环侧的行为问题：v2 在批次 1 被拒 1 项后直接进入批次 2，没有重试该义务，`web_rule_3_negative_10` 最终 `NOT_COVERED`。这属 Agent 决策（C 类），不是诊断缺陷；但也暴露出"预检拒绝后是否必须重试"没有确定性规则。

### 4.3 `NO_PROGRESS`

- 触发点：`execution_budget.observe`（L523-554）在 `no_progress_rounds >= no_progress_limit` 时 `stop("NO_PROGRESS", …)`。验收启动器把 `no_progress_limit` 设为 3（字段默认 2）。
- v3 归档值：`no_progress_rounds=3`、`stop_detail="连续 3 次动作没有闭合义务、解除阻塞或取得可信清理凭据。"` → **按预定规则触发**，不是误判或漏判。
- 但停止时仍有可用容量：`negative_batch_5` 只跑 2 次（`MAX_GROUP_RUNS=3`，第三次按 `product_agent.py` L711-718 专供形式错误，而该组最后一次诊断正是 `JSON_SYNTAX`），`negative_batch_6` 只跑 1 次，模型调用余额 22 次、业务请求余额 66 次。也就是说"每组保留一次形式修复"这条规则与"连续 3 次无进展即停"这条规则互相抵消，**保留的那次修复永远拿不到**。
- 归因分层：模型连续 4 次生成非法 JSON 是原因（C 类）；把"形式错误的修复机会"计入无进展并提前终止，是终止规则的交互缺陷（A 类，见 §7 A4）。

## 5. 固定历史样本 A/B 回放结果

全部在当前 HEAD 上、用归档原始输出与归档上下文回放；模型服务与目标 HTTP 调用 0 次。

### 5.1 Focus（`focus_ab.py`）

| 变体 | 输入改动 | 结果 |
| --- | --- | --- |
| A1 | v1 第 1 次原文 | `FOCUS_SOURCE_CLAUSE_MISSING`，`fields=[requirement_map.request_2]` |
| A1b | 仅删 `requirement_map.request_2` | `FOCUS_INPUT_TEXT_CHANGED`，`fields=[requested_checks.1.exact_value]` |
| A1c | 删键 **且** 把范围子句引用的取值置 null | **ACCEPTED** |
| A1d | 删键 **且** 把引用改成 `source_1`（保留原文取值） | `FOCUS_INPUT_TEXT_CHANGED`，`fields=[requested_checks.1.exact_value, approved_checks.1]` |
| A2 | v1 第 2 次原文 | `FOCUS_INPUT_TEXT_CHANGED`，`fields=[requested_checks.1.exact_value]` |
| A2b | 把范围子句引用的取值置 null | **ACCEPTED** |
| A2c | 把引用改成 `source_1` | `FOCUS_INPUT_TEXT_CHANGED`（两个字段） |
| B1 | v4 第 1 次原文 | `FOCUS_SOURCE_CLAUSE_MISSING`（与 A1 同） |
| B2 | v4 第 2 次原文 | **ACCEPTED** |

**A1b 是本轮最重要的单条证据**：v1 的第一次提案同时含两个独立违规，把第一个改掉之后第二个立刻出现。Harness 的修正链只把第一个写进修复载荷（`product_tasks.py` L765-773），于是唯一一次修正被用在"多报键"上；第二次提案撞上从未被告知的第二条规则（引用范围子句时取值必须为 null），而 `order_feedback` 已非空，L761 直接 `raise`，整轮结束。A1c 证明合法提案只差这两处、且两处都正是 Harness 已知的规则。

修复载荷的措辞也参与了这条链：`focus_repair_payload`（`product_tasks.py` L183-186）无条件追加"不要扩大范围，也不要重复同一个被拒的值。"。在"多报键"这种与取值无关的拒绝里，这句话直接诱导模型改值——归档里可见 `"blue"`→`"x"`、`1`→`0`。A1d/A2c 进一步显示：即使改引 `source_1`，被改过的字符串值仍会被 `_check_quote` 拒绝，所以这句提示换来的是**第二个违规**。

### 5.2 引用候选修正（`construct_ab.py`，P3-B2.2 v1 批次 7 原始样本）

| 变体 | 方案 | 结果 |
| --- | --- | --- |
| C1 | 归档原文：消费步骤引用未声明的 `createParcelForNegative8` | `CONSTRUCTION_REFERENCE_INVALID`／`reference=SOURCE_UNKNOWN`／`allowed_from_steps=[]` |
| C2 | 在前面声明一个已批准的 `createParcel` 步骤并正确引用 | **ACCEPTED**，构造顺序 `[createBaseParcel, replaceQuantityOutOfBound]` |
| C3 | 生产者声明在**消费步骤之后**、但被正确引用 | **ACCEPTED**，构造顺序相同 |
| C4 | 生产者声明在最后、引用写成不存在的 id | `CONSTRUCTION_REFERENCE_INVALID`，`allowed_from_steps=[]` |

- C1 说明修正后的空候选对该样本是**正确**的：该提案除消费步骤外没有任何节点，原诊断把"消费步骤自身"列为候选才是缺陷，现在已修掉。
- C2/C3 说明修正**没有排除合法来源**：无论是先声明还是后声明，只要节点存在且批准绑定匹配，构造都接受；数组位置在无序编码里本来就没有执行含义。
- C4 定性了残留问题：候选只取"当前位置之前的节点"（`plan_construction.py` L108-127 的 `list(nodes.values())[:index]`），所以当一个合法来源声明在**后面**、而模型恰好把 id 写错时，诊断会给出空集并附"无，需提交符合已批准绑定的前置步骤"，与事实不符。这会让模型误以为必须新增节点。属诊断完整性缺陷（§7 A3），不是门禁缺陷；**该缺陷已在 §11 修复**（本表的"空集"是修复前的观测）。

### 5.3 实例输入与负向取值（`v2_instance_replay.py`、两个探针）

| 变体 | 输入 | 结果 |
| --- | --- | --- |
| D1 | v2 `group_1` 第 1 次原文：`ai_5.arg_1="acceptance-parcel-renamed"` | `DESIGN_INSTANCE_LITERAL_INVALID`，`detail={instance: ai_5, slot: arg_1, required: [], hint: "按该槽位的批准 Schema 填写全部必填字段。"}` |
| D2 | 把 6 个数值 `exact_value` 全改成 99999 | **ACCEPTED**，且解码后保留 99999 |
| D3 | 把 `boundary_outside` 的 `exact_value` 改成 2（批准区间 1–3） | Focus **ACCEPTED** |

- D1 的失败本身是模型错误：该槽位批准 Schema 是 `{"type":"string","minLength":1,"maxLength":24}`，模型写了 25 个字符。但诊断把对象型字段 `required` 用在标量约束上，`required=[]` 加"填写全部必填字段"对"超长 1 个字符"毫无信息量，属诊断缺陷（§7 A2）。
- D2 **不是缺陷**：该批负向检查都带 `allow_value_variants: true`（24 项全部为 true，操作者批准过变体权限），任意越界数值都是被授权的变体。因此数值路径的"宽松"是批准范围内的行为。
- D3 也不是缺陷：`negative.select_negative_approval(..., override=True)`（`negative.py` L136-166）会用 `NEGATIVE_VALUE_BOUNDARY_DIRECTION_CHANGED` 拒绝改变了越界方向的变体，`product_checks.split_requested_checks` 把该检查降级为 `pending`，因此边界内取值不会进入执行。Focus 接受、下游拒绝，职责划分正确。

## 6. 超时与用量

| 轮次 | 本地调用 ID | 起止（北京时间） | 阶段／批次 | 请求摘要 | 网关请求 ID | 本地错误类型 | 用量 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v2 | `6ff62a16507642afa1cef601d03e1400` | 16:41:50.793–16:42:50.813（60,032 ms） | design / `negative_batch_5` 第 1 次 | 38,456 B，`c59781befcdcf77dff25df85f4d8df7fecaa89dffb356b2ee87f543f6cbcfbb1` | `null` | `MODEL_TIMEOUT`，`timeout_kind=DEADLINE`，HTTP 状态缺失 | `UNKNOWN` |
| v4 | `7d518f8950de4a3b856489ba64b43442` | 16:49:14.258–16:50:14.296（60,047 ms） | design / `negative_batch_5` 第 1 次 | 39,567 B，`d8f151976eea4a5a7ff73d341c56be0b68e878f049c90460b31f946b917fdf80` | `null` | 同上 | `UNKNOWN` |
| v5 | `ef68d9c572754c6fa1a69cf34563c8dd` | 16:50:38.222–16:51:38.284（60,062 ms） | focus / 第 1 次 | 47,360 B，`897e1ed241ce6a8fc04efcc816f0e11f3965bc60ffa1595458febe5c66870f99` | `null` | 同上 | `UNKNOWN` |

- 账本核对：五轮 `model_usage_journal` 共 61 行（2/17/22/19/1），`KNOWN` 58 行合计 481,990 tokens，`UNKNOWN` 3 行；`model_usage_exceptions` 与 `model_usage_reconciliations` 五轮均为空，即三条未知从未被"补记"成已知。`UNKNOWN` 不推定为零。
- 三条记录的 `provider_request_id`、`provider_response_id`、`http_status` 均为 `null`；`timeout_kind=DEADLINE` 只能说明客户端 60 秒墙钟到期，无法区分连接、排队、推理或传输。**服务端状态与真实用量不可核实**；需要获授权的逐请求记录（`ecnu-max`，16:41:50–16:42:51、16:49:14–16:50:14、16:50:38–16:51:38 三个窗口，配合上面的请求摘要）才能对账。
- 同一请求的跨轮观测（`focus_observations.py`）：字节相同的 Focus 请求共 10 次，P3-B2.2 的 4 次 200（2,828／34,703／3,516／2,860 ms）、1 次超时；P3-B2.3 的 4 次 200（5,781／6,469／14,359／7,797 ms）、2 次业务拒绝、1 次超时。**同一字节的请求既有 2.8 秒完成，也有 34.7 秒和两次 60 秒超时**，说明超时不是请求内容的性质。
- 延迟分布：58 次已知调用输出吞吐 2.4–242.5 tokens/s；最慢的已知调用是 v2 16:39:04 的 47,375 ms（仅 113 tokens）。服务端延迟波动非常大，且 v3 的 14,359 ms Focus 调用正好落在第一次超时（16:41:50）之后不久。
- 同一请求在网关侧回报的输入计数有两种：13,034 与 13,124（差 90），同样是"同一字节、不同服务端计数"的证据。
- 重试策略：`model_retry.py` 只对 429/500/502/503/504 做有界重试（`MAX_GATEWAY_RETRIES=2`），超时不在其列；`UNKNOWN` 用量按门禁停止后续付费调用。本轮**不修改** 60 秒超时、不修改模型调用预算、不新增重试。

## 7. 已确证缺陷与待核实风险

分类：A 已复现的 Harness 缺陷；B 提示或 Schema 问题；C 模型输出不稳定；D 模型服务／网关；E 证据不足。

### A 类（已复现）

**A1 · Focus 单次修正 + 首错优先，使两个独立违规必然判死整轮**
- 位置：`src/agenttest/product_tasks.py` L761-773（`order_feedback` 非空即 `raise`）、`src/agenttest/request_requirements.py` L630-644（`requirement_map` 键集合先校验）、`src/agenttest/product_checks.py` L84-103（`requested_checks` 后校验）。
- 样本：v1 `2a3074c9…` → `950eb774…`；复现见 §5.1 A1b/A1c。
- 说明：约束本身不动。缺陷在于"一次诊断只带一条独立违规，而修正机会只有一次"。

**A2 · `DESIGN_INSTANCE_LITERAL_INVALID` 对标量约束输出对象型 `required` 与误导性提示**
- 位置：`src/agenttest/instance_design.py` L92-97（`required=approved.get("required", [])`、hint 固定为"填写全部必填字段"）。
- 样本：v2 `4cab790c…`，`ai_5.arg_1` 25 字符 vs `maxLength=24`；复现见 §5.3 D1。
- 影响：模型拿不到"超长 1 个字符"这个事实，只能盲改；同一轮第 2 次通过属运气。

**A3 · 引用候选只考虑"数组中更早的节点"，漏报合法来源**
- 位置：`src/agenttest/plan_construction.py` L108-127（`list(nodes.values())[:index]`）。
- 样本：§5.2 C4（生产者声明在后、引用写错 → `allowed_from_steps=[]`）；C2/C3 证明后声明节点完全合法。
- 影响：仅影响拒绝时的候选提示；不会接受非法方案，也不会拒绝合法方案。

**A4 · `NO_PROGRESS` 把"仍有保留形式修复机会"的观察计入无进展**
- 位置：`src/agenttest/execution_budget.py` L523-554；与 `src/agenttest/product_agent.py` L707-718（第三次运行专供形式错误）冲突。
- 样本：v3（`no_progress_rounds=3`、`negative_batch_5` 仅 2 次运行、`negative_batch_6` 仅 1 次、模型调用余额 22）。
- 说明：规则**按其定义正确触发**；缺陷在两条预算规则的交互。修 A4 会改变终止行为，需操作者确认。

### B 类（提示或 Schema）

**B1 · `JSON_SYNTAX` 修复提示给出模型无法定位的字符位置**
- 位置：`src/agenttest/product_agent.py` L794-803（分组结果直接回传 `detail.position/line/column/reason`）、`src/agenttest/obligation_gate.py` L280-282（运行时自撰修复约束把同一位置渲染成"第 N 个字符"）。
- 样本：v3 批次 5 两次尝试（5,235 → 1,969）；§4.1 已证明修复请求不含被拒原文。

**B2 · `FOCUS_SOURCE_CLAUSE_MISSING` 的语义名与条件不一致**
- 位置：`src/agenttest/request_requirements.py` L634-644（实际条件是键集合"不相等"，多报也报 MISSING）。
- 样本：v1/v4 第 1 次（`fields=[requirement_map.request_2]`）。名称保留、提示已列出 `expected`/`trusted`，影响低。

**B3 · 模型端 Schema 未表达"引用范围子句时 `exact_value` 必须为 null"**
- 位置：`src/agenttest/product_sources.py` L273-305（`if "source_ref" in properties:` 只覆盖已转换分支）。
- 样本：v1 第 1、2 次都给 `source_3` 配了非空取值（v4 第 1 次没有，可见并非必犯）。收紧此项属于**加约束**，不放宽任何现有限制。

### C 类（模型输出不稳定）

- **C1**：字节相同的 Focus 请求 10 次观测，6 次通过、2 次被拒、2 次超时；`temperature=0` 也复现差异。
- **C2**：`arguments[1].name` 值-名错位在 P3-B2.2 出现 3 次、P3-B2.3 出现 2 次。
- **C3**：v3 连续 4 次 `MISSING_COLON`，同一模型在相邻调用里能输出合法 JSON。
- **C4**：Agent 在预检拒绝后未重试被拒义务（v2 `web_rule_3_negative_10`）。

### D 类（模型服务／网关）

- **D1**：三次 60 秒客户端超时；同一请求在别处 2.8–14.4 秒完成；已知调用吞吐 2.4–242.5 tokens/s。
- **D2**：超时不在有界重试范围内，`UNKNOWN` 门禁直接终止整轮；是否允许"超时后重试一次"属操作者权限，本轮不改。

### E 类（证据不足）

- **E1**：三次超时的服务端排队、推理与真实计量——**无法核实**。
- **E2**：v5 首次调用即超时，无法判断是首包延迟还是长生成；该轮无任何已知用量。
- **E3**：同一请求的输入计数出现 13,024/13,124 两种值，原因（分词器版本、工作节点或统计口径）无证据。

## 8. 最小修复建议与优先级

按"最小改动 + 用归档样本做回归"的原则排序；均不改动审批范围、请求预算、模型调用预算、60 秒超时、断言与 4/5 门限。

| 优先级 | 缺陷 | 最小修复 | 所需回归（以归档样本为输入） |
| --- | --- | --- | --- |
| P0 | A1 | 让一次 Focus 拒绝携带**全部独立违规**：在 `decode_requirements` 失败时仍先跑 `validate_requested_checks` 的引用/取值检查，把两组字段合并进同一 `diagnostic.fields`；或按违规类别（来源键 vs 取值）各保留一次修正。同时把 `focus_repair_payload` 的"不要重复同一个被拒的值"改为**只在被拒字段确实是取值时**出现。 | 用 v1 第 1 次原文断言一次拒绝同时报出 `requirement_map.request_2` 与 `requested_checks.*.exact_value`；用 v1 第 2 次原文断言修复载荷不含"不要重复同一个被拒的值"；断言 A1c 形态（键与取值都改对）被接受；断言拒绝时 0 次业务 HTTP。 |
| P0 | A2 | `instance_design.instance_response` 的 `detail` 增加被违反的约束本身（`constraint`、`limit`、`actual`），标量约束不再输出 `required`；hint 改为"按该槽位的批准 Schema 填写：{约束}"。 | 用 v2 `4cab790c…` 的 `ai_5.arg_1` 原文断言 `detail` 含 `maxLength=24`、`actual=25`；断言对象型槽位仍给出 `required` 列表；断言拒绝时 0 次业务 HTTP。 |
| P1 | A3 | 候选池改为"除自身、被插入的认证节点与负向结果之外的全部已提交节点"，仍按批准绑定、选择器与角色关系过滤。 | 用 §5.2 的 C4 断言 `allowed_from_steps == ["createBaseParcel"]`；C1 仍为空集；C2/C3 仍通过；断言提示文本与诊断一致。 |
| P1 | B1 | `JSON_SYNTAX` 的修复通道改为**可执行**：分组结果或修复约束附带位置附近的有界原文片段（例如前后各 40 字符，经既有脱敏与长度上限），或去掉位置只保留"缺少冒号，请重新输出完整 JSON"。 | 断言修复约束包含片段或不再包含 `position` 数字；断言片段长度上限与脱敏；断言不泄露请求体之外的模型输入。 |
| P2 | B3 | `constrain_scope_check_sources` 同时处理仍为 `source_quote` 的分支，使"引用范围子句 + 非空 `exact_value`"在模型端不可表达。 | 断言重建的 Focus Schema 中该类分支的 `exact_value` 为 `const`/`null`；断言 v1/v4 原文仍被拒（诊断不变）；现有合法提案仍通过。 |
| P2 | B2 | 保持码名，仅在 hint 中写明"多出的键：…；必须包含的键：…"。 | 断言 hint 同时含多出键与必需键；断言码名不变。 |
| P3 | A4 | `no_progress` 计数在"该分组仍保留一次形式修复"时不计入，或把停止条件改为"连续 N 次无进展且无可派发的形式修复"。**此项改变终止行为，需操作者确认后再实施。** | Mock 三轮 `JSON_SYNTAX` 后仍派发一次形式修复；随后仍无进展才停；断言 `UNKNOWN` 门禁、预算上限与 `strict_complete=false` 不变。 |

不建议在诊断阶段做的改动：放宽 `NON_REPAIRABLE_FOCUS`、把 Focus 的取值规则降级为提示、给超时加自动重试、扩大模型调用预算或拉长 60 秒超时。

**实施状态**：A1、A2 已按本表完成最小修复（§10），A3 已完成（§11）。A4、B1、B2、B3 本轮**未动**，`docs` 中关于它们的结论保持原样。

## 9. 下一轮验收建议

1. **保持 NO-GO 与既有五轮证据不变。** 3/5 与 0/5 都是真实观测，8 组 31/31 目标未达成的判断不变。
2. **先落 P0 两项（A1、A2）并只跑定向回归**，用本文列的归档样本作固定输入，不启动真实模型。A1 是唯一"单点即可把整轮从 0/31 变成可继续"的缺陷，A2 是同一轮里第二次浪费调用的地方。
3. **P1 两项（A3、B1）随后做，同样只跑定向回归。** B1 的收益是让"形式错误 → 一次修正"这条链真正可执行。
4. **A4 与超时重试都必须由操作者单独授权**：前者改变终止行为，后者改变付费调用预算；两者都不属于本轮授权范围。
5. 三项超时的用量对账只有在获得授权的服务端逐请求记录后才能补全。在此之前保持 `UNKNOWN`，不得用平均值、探针或额度变化补算。
6. **不要**用"再跑一次五轮"来验证修复：模型侧不稳定性（C1、C2）本身就会让 5 轮结果来回摆动（现有两次观测是 3/5 与 0/5，输入字节相同）。P0/P1 修复的验收标准应是"归档样本上的确定性诊断与拒绝行为"，而不是通过轮数。
7. 若操作者要求重启发布验收，仍沿用原 4/5 门限与原冻结配置，另行启动；本轮不启动，也不进入 P3-B3。

## 10. 最小修复结果（A1、A2）

本轮授权范围：只修 §7 已确证的 A1、A2。**未启动真实模型调用、未启动五轮验收、未跑全量冻结回归、未调整模型预算／超时／安全约束。** 修复只改变"拒绝时告诉模型什么"，不改变"接受还是拒绝"。

### 10.1 A1 · 一次 Focus 拒绝携带全部独立违规

**根因**（两处叠加）

1. `product_focus._decode_output`（L533-541）在 `decode_requirements` 抛错时立即 `raise`，后面的 `decode_operation_bundles` → 引用检查 → `validate_requested_checks` 根本不会执行。因此"多报 `requirement_map` 键"和"引用范围子句却给了非空 `exact_value`"这两条独立规则，永远只有第一条进入诊断。
2. `validate_requested_checks` 逐条 `raise`，同一提案里第 2 个及以后的违规条目全部被吞掉（v1 第 1 次有 17 个位置违反取值规则，旧诊断只报 1 个）。
3. `focus_repair_payload` 的固定提示以"不要扩大范围，也不要重复同一个被拒的值。"结尾。在"多报键"这类与取值无关的拒绝里，这句话把模型推向改取值——归档里正是 `"blue"`→`"x"`、`1`→`0`，从而制造第二个违规。

**改动**（`src/agenttest/product_checks.py`、`src/agenttest/product_focus.py`、`src/agenttest/product_tasks.py`）

| 位置 | 改动 |
| --- | --- |
| `product_checks.py` | 新增 `CHECK_RULES`（每个码的 `violated_constraint`／`expected_rule`）、`_check_violation(...)`、`group_violations`、`grouped_failure`、`requested_check_violations`（**不抛错**，逐条收集，规则优先级与旧 `raise` 顺序逐条一致）；`validate_requested_checks` 变成"收集 → 合并 → 抛一次"的薄壳，首个违规的码仍是 `diagnostic.code`。 |
| `product_focus.py` | `_decode_output` 捕获 `decode_requirements` 的失败，先完成 scope/blocked 的形状校验，再用与合法路径相同的顺序（`decode_operation_bundles` → `decode_source_refs` → `RequestedCheck`）收集检查级违规，最后由 `focus_violation_failure` 合成**一个**诊断：主码仍是原来的 requirement 码，`detail` 保留 `expected_requirement_keys`／`trusted_scope_clauses` 并新增 `violations`。 |
| `product_tasks.py` | 修复载荷删掉"不要重复同一个被拒的值"，改为"已批准的取值和原文都必须原样保留，换一个值不是修复"，并按 `detail.violations` 派发 `must_delete`／`must_be_null`／`must_match` 三类**显式编辑指令**；原有 `allowed_operations`／`requirement_keys`／`trusted_scope_clauses` 与提示关键子串全部保留。 |

**修复前后（v1 第 1 次原文，归档样本）**

| | 修复前 | 修复后 |
| --- | --- | --- |
| 码 | `FOCUS_SOURCE_CLAUSE_MISSING` | `FOCUS_SOURCE_CLAUSE_MISSING`（不变） |
| `fields` | `[requirement_map.request_2]` | `[requirement_map.request_2, requested_checks.1.exact_value, …]`（上限 8 条，见下） |
| `detail` | `expected_requirement_keys`／`trusted_scope_clauses` | 同左 **+ `violations`**：`[FOCUS_SOURCE_CLAUSE_MISSING ×1（must_delete=requirement_map.request_2）, FOCUS_INPUT_TEXT_CHANGED ×17（must_be_null=…）]` |
| 修复载荷 | 只提到"删多余键"，并附"不要重复同一个被拒的值" | `must_delete=[requirement_map.request_2]`、`must_be_null=[requested_checks.*.exact_value]`，提示明确"删除／置 null／原样保留" |

**如实说明的边界**：`Diagnostic.fields` 有 pydantic `max_length=8`，`detail` 的每个违规条目最多枚举 12 条路径（`MAX_VIOLATION_PATHS`），`count` 记录真实总数（本例 17）。被截断的不是"是否发现"，而是"逐条枚举列表"：类别、条数、规则原文与编辑动作都在，模型按规则处理全部同类条目即可。这是沿用既有诊断尺寸约束，未放宽任何校验。

### 10.2 A2 · 标量约束诊断

**根因**：`instance_design.instance_response`（L92-97）对所有 `validate_value` 失败都输出对象型的 `required=approved.get("required", [])` 和"按该槽位的批准 Schema 填写全部必填字段。"。标量槽位没有 `required`，于是"25 个字符超过 `maxLength=24`"被渲染成 `required: []` + 让人去补必填字段。

**改动**（`src/agenttest/instance_design.py`）：新增 `violation_facts`／`violation_hint`／`literal_failure_detail`，按 `validate_value` **相同的判定顺序**给出 `field_path`、`violation`（`MAX_LENGTH`／`MIN_LENGTH`／`MAX_ITEMS`／`MIN_ITEMS`／`TYPE`／`ENUM`／`OBJECT_FIELDS`／`PATTERN`／`MINIMUM`／`MAXIMUM`／`EXCLUSIVE_*`）、`constraint`、`limit`、`actual_length`／`expected_type`／`actual_type`／`missing`／`required`、`schema_rule`（原 `OPENAPI_*` 码）与针对性 `hint`。**不包含模型给的字面值**（该值可能是操作者数据或凭据）。判定仍由 `validate_value` 做，`raise` 位置、码、层级不变；仅当槽位确实是对象时才出现 `required`/`missing`。

**修复前后（v2 `group_1` 第 1 次：`ai_5.arg_1` 25 字符 vs `maxLength=24`）**

| | 修复前 | 修复后 |
| --- | --- | --- |
| `detail` | `{instance: ai_5, slot: arg_1, required: [], hint: "按该槽位的批准 Schema 填写全部必填字段。"}` | `{instance: ai_5, slot: arg_1, field_path: "body.name", violation: "MAX_LENGTH", constraint: "maxLength", limit: 24, max_length: 24, actual_length: 25, schema_rule: "OPENAPI_VALUE_LENGTH", hint: "长度超限：批准 Schema 要求 maxLength=24，本次 25 个字符。请缩短到 24 个字符以内。"}` |
| `fields` | `[]` | `["body.name"]` |
| 24 字符 | 通过 | 通过（不变） |
| 缺必填成员 | `required=[labels, options]` + "填写全部必填字段" | `violation=OBJECT_FIELDS`、`missing=["labels"]`、`required=[labels, options]`、hint 点名缺失字段 |

同类检查已覆盖 `minLength`、`maxLength`、`minItems`、`maxItems`、`minimum`／`maximum`／`exclusive*`、`enum`、类型不匹配、对象必填/多余字段：旧代码在这**每一类**上都输出同样的 `required` + "填写全部必填字段"，属同一缺陷。

### 10.3 归档样本离线回放（`​.eduwork/p3b24-fix/replay.py`，0 次模型／0 次业务 HTTP）

| 变体 | 输入 | 修复前 | 修复后 |
| --- | --- | --- | --- |
| A1 | v1 第 1 次原文 | `FOCUS_SOURCE_CLAUSE_MISSING`，只报 `requirement_map.request_2` | 同一诊断报 **2 类**：`requirement_map.request_2`（must_delete）+ `requested_checks.*`×17（must_be_null） |
| A1b | 仅删 `requirement_map.request_2` | `FOCUS_INPUT_TEXT_CHANGED`，只报 `requested_checks.1` | 仍拒，报该类全部 17 个位置（`count=17`） |
| A1c | 删键 **且** 取值置 null | ACCEPTED | **ACCEPTED**（接受条件未变） |
| A1d | 删键 & 改引 `source_1`（保留被改的值） | 拒 | 仍拒（4 处，附 `must_match`"不得用另一个字面值替代"） |
| A2 | v1 第 2 次原文 | `FOCUS_INPUT_TEXT_CHANGED`，1 个字段 | 仍拒，17 个位置 |
| A2b | 取值置 null | ACCEPTED | **ACCEPTED** |
| B1 | v4 第 1 次原文 | `FOCUS_SOURCE_CLAUSE_MISSING`，只报多报键 | 仍只报该键（该提案的引用与取值都合法，**没有**被凭空加违规） |
| B2 | v4 第 2 次原文 | ACCEPTED | **ACCEPTED** |
| C1 | v2 `group_1` 第 1 次原文 | `required: []` + "填写全部必填字段" | `MAX_LENGTH`／`maxLength=24`／`actual_length=25` |
| C2 | 把两处超长名截到 24 字符（`ai_5` 25→24、`ai_6` 26→24） | — | **ACCEPTED** |
| C3 | 在同提案里删掉对象槽位的必填成员 | `required` 列表 | `OBJECT_FIELDS`／`missing=["labels"]`，hint 点名缺失字段 |

### 10.4 新增测试与定向回归

- 新增 `tests/test_p3b24_diagnostics.py`（16 个用例，全部离线、确定性）：一次拒绝报出两类违规；仅删键仍报取值违规；修复后校验器零违规且 `requirement_map` 可解码；合法提案仍解码、无关非法提案仍按原码拒绝；修复载荷不含"不要重复同一个被拒的值"且含 `must_delete`／`must_be_null`；违规顺序稳定；25 字符仍被 `maxLength=24` 拒绝并报 `actual_length=25`；24 字符通过；缺必填仍列 `required`；标量 `detail` 不含 `required`、不含"填写全部必填字段"；`len(detail) <= 12`；`validate_value` 对同一取值的判定不变。
- 定向回归：`tests/test_p3b24_diagnostics.py`、`test_product_focus.py`、`test_product_checks.py`、`test_product_tasks.py`、`test_request_requirements.py`、`test_planning_flow_instances.py`、`test_negative_values.py`、`test_product_lengths.py`、`test_product_design.py`、`test_product_instance_prompt.py`、`test_product_schema.py`、`test_product_sources.py`、`test_product_scope.py`、`test_p3b23_diagnostics.py`、`test_obligation_gate.py`、`test_product_agent.py`、`test_batch_diagnostics.py`、`test_product_rejection.py`、`test_task_service.py`、`test_focus_references.py`、`test_focus_order_ambiguity.py`、`test_focus_approved_expectations.py`、`test_product_inputs.py`、`test_human_review.py`、`test_task_gate.py` → **404 passed, 1 xfailed**（xfail 为 P3-B2.3 期间已存在的 `strict` 归档用例，本轮的 16 个新用例全通过）。
- `ruff check src tests` → 通过；`git diff --check` → 无输出（exit 0）。

### 10.5 未被改动的部分（逐条对照）

| 约束 | 状态 |
| --- | --- |
| Focus 通过条件（校验器接受什么） | 未变：`requested_check_violations` 的判定顺序与旧 `raise` 顺序逐条一致，`validate_value`／`decode_requirements` 的接受集合未改；修复后 A1c、A2b、B2 仍 ACCEPTED |
| `decode_requirements` 自身诊断 | 未改（其 `detail`／`fields` 有测试做精确断言） |
| 主码与修复门 | 未变：主码仍是第一个违规的码，`repairable`／`negative_literal`／`NON_REPAIRABLE_FOCUS` 判定与修复轮数（1 次）均未改 |
| HITL 审批、已批准义务、负向策略、Schema 校验、请求派发 | 未改 |
| 预算、60 秒超时、`UNKNOWN` 门禁、清理与持久账本、未知写不重放 | 未改 |
| A2 的接受/拒绝判定 | 仍由 `validate_value` 决定：25 字符仍被拒，24 字符仍通过 |
| 字段约束 | 未放宽任何一项（`maxLength=24` 等原样） |
| A3（`plan_construction` 引用候选）、A4（`NO_PROGRESS` 与保留的形式修复） | **未动**；B1、B2、B3 未动 |

### 10.6 交付物与下一步

- 改动文件：`src/agenttest/product_checks.py`、`src/agenttest/product_focus.py`、`src/agenttest/product_tasks.py`、`src/agenttest/instance_design.py`、新增 `tests/test_p3b24_diagnostics.py`、本文档、`docs/progress.md`。未提交（保持上一轮 `HEAD f311721` 之上的未提交状态）。
- 源码摘要（SHA-256 前 16 位）：`product_checks.py` `495f39cbe8e25038`、`product_focus.py` `b44a392d89a5f082`、`product_tasks.py` `500997b4053b55ef`、`instance_design.py` `e3361633f2d2587d`、`tests/test_p3b24_diagnostics.py` `1cc39e017aa145ec`。
- **P3-B2.3 的 NO-GO 结论不变**：本轮没有新跑真实模型，既不证实也不否认五轮通过率；修复的价值是"下一次真实运行里，同一提案不会再因为只报第一个违规而浪费唯一一次修复"。
- 建议下一步（需操作者另行授权）：先只对 A3 做与 §10 同规格的最小修复与归档回放（C1/C4 样本已在 §5.2 固定），再决定是否重启五轮验收；A4 与超时重试仍需单独授权。**本轮不进入 P3-B3。**

## 11. A3 最小修复（引用候选漏报）与 A1 诊断完整性修复

两段授权合并记录：先只修 A3 并对 A1 的截断诊断做只读核查（§11.1-§11.6），操作者确认后又授权 A1 的两处最小修正（§11.7），范围仅限 `focus_repair_payload` 的截断与 `requested_check_violations` 的路径命名。

前一段授权：只修 A3，并对 A1 的截断诊断做只读核查。零真实模型调用、零业务 HTTP、未跑全量冻结回归、未启动五轮验收；未改动 A4、超时、预算、终止规则、HITL、义务门禁、Schema 接受条件、`NO_PROGRESS`、持久账本、清理与未知写重放。

### 11.1 A3 根因

`plan_construction.reference_failure`（旧 L109-129）有两个独立问题：

1. **只扫描数组里更早的节点**：`list(nodes.values())[:index]`。但本编码的数组位置**没有执行含义**（模块文档 L29 与 `construct_plan` 的拓扑排序都表明这一点），C3 变体已证明"生产者声明在后、引用写对"会被接受并自动前置。于是当一个合法生产者声明在消费步骤之后时，诊断给出的候选集是空，模型只被告知"无，需提交符合已批准绑定的前置步骤"。
2. **候选判定只读 `binding_rules`**：归档样本里消费方是负向策略（`web_rule_7_negative_8`），`binding_rules` 为 `null`，因此**即使生产者声明在前**，该谓词也永远返回空集。该槽位的批准事实其实在 `resource_lifecycles`（`create_operation=createParcel`、`path_parameter=parcelId`、`id_selector=$.id`）。

### 11.2 改动（只影响诊断与提示）

`src/agenttest/plan_construction.py`：

| 单元 | 作用 |
| --- | --- |
| `argument_matches(rule, argument)` | 一条批准绑定规则是否正好指向该槽位（位置/字段名/选择器；headers 名大小写不敏感，沿用旧比较）。 |
| `binding_sources(node, argument, policies, lifecycles)` | 批准来源有两条路径：显式绑定规则；或**生命周期身份**——负向变体按已批准的（路径参数, id 选择器）消费刚创建的资源。后者让"没有 `binding_rules` 的负向策略"也有可证明的生产者。 |
| `order_constraint(reordered, blocked)` | 一句话说明：哪些来源声明在本步骤之后（直接用即可，构造器会前置）、哪些被拒及原因。 |
| `prerequisite_closure(key)` / `reference_block(candidate)` | 拒绝条件：来源自身（经批准策略前序）依赖本步骤 → `CYCLE`；来源自己的批准前序在本方案中缺生产者 → `PREREQUISITE_MISSING`。 |
| 重写 `reference_failure` | 遍历**全部**节点，按"是否批准来源"筛选，再按声明位置与可用性分成三组；`allowed_from_steps` = 全部合法且可用的来源，`direct_from_steps` / `reordered_from_steps` 是它的两个子集，`blocked_from_steps` 带原因且**永不进入可用列表**。 |

诊断新增字段：`direct_from_steps`、`reordered_from_steps`、`blocked_from_steps`（仅非空时）、`order_constraint` 与 `reason`（同一个短句，`reason` 供既有渲染通道使用）。键数最多 9（+`case_index` = 10），仍在 `detail` 的 12 键上限内。`CONSTRUCTION_REFERENCE_INVALID` 的码、`fields`、接受/拒绝判定、`CONSTRUCTION_DEPENDENCY_CYCLE` 的判定与 `dsl.validate_plan` 的前序引用校验全部未改。

`obligation_gate.py` 本轮**未改动**：`_diagnostic_text` 本来就会渲染 `detail.reason`（"原因=…"），因此顺序约束能通过既有通道进入运行时修复约束；实测整段文本 203 字，远低于 `MAX_REPAIR_CHARS=900`。

### 11.3 归档样本与边界结果（`.eduwork/p3b24-fix/construct_after.py`）

| 变体 | 提案 | 修复前 | 修复后 |
| --- | --- | --- | --- |
| C1 | 归档原文：只有消费步骤，引用不存在的 `createParcelForNegative8` | 拒，候选 `[]` | 拒，候选 `[]`（确实没有生产者） |
| C2 | 生产者在前、正确引用 | ACCEPTED | **ACCEPTED** |
| C3 | 消费者在前、生产者声明在后、正确引用 | ACCEPTED | **ACCEPTED**（顺序 `[createBaseParcel, replaceQuantityOutOfBound]`） |
| C4 | 消费者在前、生产者声明在后、引用写错 id | 拒，候选 `[]` ← 缺陷 | 拒，`allowed=reordered=["createBaseParcel"]`，并给出顺序约束 |
| E1 | 自身引用 | 拒，候选 `[]` | 拒（`SELF_REFERENCE`），列出合法生产者 `createBaseParcel`，自身不在候选内 |
| E2 | 引用不存在的 id 且无任何节点 | 拒，候选 `[]` | 拒，候选 `[]` |
| E3 | 只存在其它已批准接口（`replaceParcel`） | 拒，候选 `[]` | 拒，候选 `[]`（不是本槽位的批准来源） |
| E4 | 来源自身依赖本步骤 | 拒，候选 `[]` | 拒，`blocked=[{createBaseParcel, CYCLE}]`，**不进入可用列表** |
| E5 | 来源缺少已批准前置 | 拒，候选 `[]` | 拒，`blocked=[{…, PREREQUISITE_MISSING}]` |
| E6 | 引用其它用例里的步骤 id | 拒，候选 `[]` | 拒，候选 `[]`（候选只来自本方案的 `nodes`） |

C4 的顺序约束原文：`合法来源 createBaseParcel 声明在本步骤之后：直接把它写成 from_step 即可，构造器会把它排到本步骤之前，不需要移动步骤位置。`

### 11.4 新增与调整的测试

- 新增 `tests/test_p3b24_references.py`（11 项，全部离线确定性）：后声明生产者被列出并带顺序约束；先声明生产者只出现在 `direct`，且不出现顺序提示；无 `binding_rules` 的负向策略按生命周期身份得到候选；自身引用被拒且姓名不在候选内；无生产者时候选集为空；其它接口不作候选；依赖本步骤的来源记为 `CYCLE` 且不进入可用列表；缺前置来源记为 `PREREQUISITE_MISSING`；**合法前序引用仍被接受**（顺序 `[生产, 消费]`）；经策略前序形成的循环仍被拒绝并给出具体路径。另含"其它用例的步骤 id 不作候选"。
- 调整 `tests/test_p3b23_diagnostics.py::test_v1_reference_hint_only_advertises_a_valid_earlier_producer` → `…_only_advertises_approved_producers`：原断言把"只列更早节点"写死成契约（C4 缺陷正是它的直接证据）。修改后仍然断言：码、`fields`、`design_failure_kind`、以及**未批准来源 `otherSeed` 永不出现在候选里**；只是把"更早"改成"批准来源 + 位置子集"。没有删除任何用例或安全断言。
- 定向回归 36 个文件：**603 passed, 1 xfailed**（xfail 为 P3-B2.3 期间已存在的归档 `strict` 用例）。Ruff 通过，`git diff --check` 无输出。A1/A2 归档回放（`replay.py`，11 个变体）与上一轮完全一致。

### 11.5 A1 诊断完整性核查（只读，结论：确有缺口；修复见 §11.7）

用 v1 第 1 次原始输出实测（`.eduwork/p3b24-fix/a1_completeness.py`）：

| 项目 | 修复前 | 修复后（§11.7） |
| --- | --- | --- |
| 真实违规条目 | 17（11 条 `exact_value` 非空 + 6 条 `exact_length` 非空，无重叠） | 同上 |
| 诊断内 `count` | 17 ✓ | 17 ✓ |
| 诊断内枚举 | 12（`MAX_VIOLATION_PATHS`） | 12（保持，另有 `count` 与规则文本覆盖） |
| 修复载荷实际点名 | `must_be_null` **12 条**，其中 4 条字段名写错 | **17 条，字段名全部正确** |
| 未被点名的真实位置 | **9**（3 条 `exact_value` + 6 条 `exact_length`） | **0** |

两个缺口（修复前）：

1. **载荷截断**：`focus_repair_payload` 对 `must_be_null` 取 `[:12]`，17 条里 5 条完全未点名。模型看不到自己被拒的提案（§4.1 已证明修复请求不含上版原文），所以"总数 17"不能替代"哪 17 个"。
2. **字段名不准**：`requested_check_violations` 在"只违反 `exact_length`"时仍把路径写成 `…exact_value`。规则文本说"两者都必须为 null"，语义仍正确，但逐项路径与事实不符，`must_be_null` 里因此有 4 条指错了字段。

结论：通用规则覆盖了**类别**，但这 9 个位置既不在枚举里、也无法从载荷推断出是哪些条目，因此"模型只修被列出的 12 项、其余 5 项（按位置计 9 处）不知道要改"的问题**存在**。已按 §11.7 修复，未扩大修改范围。

### 11.6 未改动与残留风险

- 未改：引用接受/拒绝规则、执行器前序引用校验（`DEPENDENCY_FORWARD_OR_UNKNOWN`）、`CONSTRUCTION_DEPENDENCY_CYCLE`、Focus 接受条件、HITL、批准义务、Schema 接受条件、业务 HTTP 派发、预算、60 秒超时、`UNKNOWN` 用量、未知写不重放、持久账本、清理规则、`NO_PROGRESS`、obligation gate。
- A4（`NO_PROGRESS` 与"每组保留一次形式修复"的交互）**保持未修**；D 类模型服务风险（三次超时、用量 `UNKNOWN`、同一请求 13,024/13,124 的输入计数差异）本轮无新证据，结论不变。
- 残留：`JSON_SYNTAX` 修复提示仍只给字符位置（B1）；`FOCUS_SOURCE_CLAUSE_MISSING` 码名与"多报键"语义不符（B2）；A1 的 `detail.violations[].field_paths` 仍按 12 条截断（诊断体量上限，模型侧修复指令已完整，见 §11.7.4）。

### 11.7 A1 修复载荷完整性与字段路径修正（已实施）

授权范围：只改 `focus_repair_payload` 的截断与 `requested_check_violations` 的路径命名；零模型调用、零业务 HTTP；未改接受/拒绝条件、修复轮次（仍为 1 轮）、Runtime、HITL、预算、终止、清理与安全门禁，A4 未动。

#### 11.7.1 两处实际差异

`src/agenttest/product_checks.py`（`requested_check_violations` 的范围子句分支）：

```python
# 修复前：值非空或长度非空都写成 exact_value
field = f"requested_checks.{position}.exact_value"
violations.append(_check_violation("FOCUS_INPUT_TEXT_CHANGED", [field], must_be_null=[field]))
# 修复后：只命名真正非空的字段；两者都非空则同一违规条目给出两条路径
paths = [f"requested_checks.{position}.{name}"
         for name in ("exact_value", "exact_length")
         if getattr(check, name) is not None]
violations.append(_check_violation("FOCUS_INPUT_TEXT_CHANGED", paths, must_be_null=paths))
```

`src/agenttest/product_tasks.py`（`focus_repair_payload` 的列举边界）：

```python
MAX_REQUESTED_CHECKS = 40          # FocusSelection.requested_checks 的 schema 上限
MAX_REPAIR_PATHS = 2 * MAX_REQUESTED_CHECKS   # 一个检查项最多两个字段
omitted = {key: len(values) - MAX_REPAIR_PATHS for key, values in edits.items()
           if len(values) > MAX_REPAIR_PATHS}
for key in edits:                  # 修复前：must_delete/must_be_null [:12]、must_match [:8]
    base[key] = edits[key][:MAX_REPAIR_PATHS]
if omitted:
    base["omitted"] = omitted      # 只有病态输入才会出现；hint 说明按 violated_constraint 处理
```

要点：上限由**已批准检查项数量**推出（40 × 2 = 80），因此任何 schema 合法提案都不会被截断；万一越界，`omitted` 给出剩余条数，`hint` 与同级 `violated_constraint`/`expected_rule` 是覆盖规则，不是静默丢弃。去重沿用"首次出现"顺序，输出确定；路径里只有字段名与位置，不含任何取值。`Diagnostic.fields` 的 8 项上限与 `MAX_VIOLATION_PATHS` 均未改。

#### 11.7.2 17 处覆盖证据（v1 原始归档样本）

`a1_completeness.py` 修复后输出：`actual offending positions: 17`、`payload must_be_null=17 entries`、`positions NOT named in the payload: 0`。载荷字段名逐条正确（`requested_checks.3.exact_length`、`.4.exact_length` 等原先把长度违规写成值违规的位置已改正），载荷 `fields` 仍是 8 项、`detail.violations[0].count=17`、载荷体积 3,481 字节（修复前 3,214）。

归档回放（`replay.py`，11 个变体）与修复前完全一致：A1/A1b/A1d、A2、B1、C1、C3 仍被拒，A1c、A2b、B2、C2 仍 ACCEPTED——**A1c（删掉多余键并把范围子句引用值置 null）仍被接受**，说明修复只影响提示，不影响判定。

#### 11.7.3 新增自动化测试（`tests/test_p3b24_diagnostics.py`，新增 8 项）

- 仅违反 `exact_length` → 只命名 `.exact_length`；两字段都违反 → 同一条目给出两条路径、`count=1`。
- 合法的第二个检查项（不同批准策略、无字面值）不被列为违规，`fields` 只有真实位置。
- 17 条违规时 `Diagnostic.fields` 仍为 8 项、`count=17`。
- 归档形状的 17 处位置全部出现在 `must_be_null`、顺序保持、无 `omitted`。
- 最宽合法提案（40 个检查项 × 2 字段 = 80 条）全部列出、不触发上限。
- 越界诊断（80+20）列出 80 条并给出 `omitted={"must_be_null": 20}` 与覆盖规则。
- 重复位置被去重、载荷里不含被拒取值。

#### 11.7.4 定向回归

`test_p3b24_diagnostics`（24）、`test_p3b24_references`（11）、`test_p3b23_diagnostics`、`test_product_focus/checks/tasks`、`test_request_requirements`、`test_plan_construction`、`test_reference_diagnostics`、`test_generic_references`、`test_obligation_gate`、`test_negative_values`、`test_approved_negative`、`test_negative_post_observation`、`test_product_lengths`、`test_planning_flow_instances`、`test_product_design`、`test_product_agent`、`test_batch_diagnostics`、`test_product_rejection`、`test_task_service`、`test_focus_*`、`test_product_inputs`、`test_human_review`、`test_task_gate`、`test_product_sources`、`test_product_scope`、`test_dsl_safety` 共 30 个文件：**512 passed, 1 xfailed**（既有归档 `strict` 用例）。Ruff 通过，`git diff --check` 无输出。

## 12. 优化版全量离线冻结回归（P3-B2.4 候选工作区）

操作者授权执行一次全量离线冻结回归。零真实模型调用、零业务 HTTP、未启动新的五轮验收；未修改 60 秒超时、调用预算、`NO_PROGRESS`、任何安全策略或验收门槛，未进入 P3-B3。

### 12.1 冻结的候选工作区

| 项目 | 值 |
| --- | --- |
| HEAD | `f3117213099e1becd332a2f29e4b97e3a7af16aa`（未提交） |
| `git status --porcelain` | 11 行：8 个修改（`instance_design.py`、`plan_construction.py`、`product_checks.py`、`product_focus.py`、`product_tasks.py`、`tests/test_p3b23_diagnostics.py`、`docs/progress.md`、`docs/deepagents-hitl-reliability-p3b23.md`）+ 3 个新增（`docs/deepagents-hitl-reliability-p3b24.md`、`tests/test_p3b24_diagnostics.py`、`tests/test_p3b24_references.py`） |
| git 可见文件 | 1,701 |
| checkout SHA-256（回归前 = 回归后 = 运行后复算） | `fa9956d0e0bc0fcd0ed931e1f013eccc…` |
| `stash@{0}` | `Backup local edits before syncing master`（历史遗留，未触碰） |
| 正在运行的验收进程 | 无（启动前 `Get-Process python` 无匹配） |

源码与测试摘要（SHA-256 前 16 位）：`product_checks.py` `0a49079be7670f5e`、`product_tasks.py` `6681f4c64ece9bf7`、`instance_design.py` `e3361633f2d2587d`、`plan_construction.py` `c56c528963446d77`、`product_focus.py` `b44a392d89a5f082`、`obligation_gate.py` `48cfaeff80bf4180`（未改）、`diagnostics.py` `46e1a711a0d841e0`（未改）、`tests/test_p3b24_diagnostics.py` `50c7d63cf780c17a`、`tests/test_p3b24_references.py` `b7d1202843886d8b`、`tests/test_p3b23_diagnostics.py` `d0fddb6b86ce7ec9`、`scripts/run_frozen_regression.py` `68e83d3cd7892c45`、`pyproject.toml` `9947e91bfe89656d`。A1/A2/A3 的代码指纹逐项复核存在（`MAX_REPAIR_PATHS`、`getattr(check, name) is not None`、`violation_facts`、`direct_from_steps`、`binding_sources`）。两个新增测试文件按未被忽略的未跟踪文件计入 `checkout_identity`，因此前后摘要包含它们。

### 12.2 运行与结果

```powershell
.\.venv\Scripts\python.exe scripts/run_frozen_regression.py --out .eduwork/frozen-regression-p3b24
```

输出目录 `.eduwork/frozen-regression-p3b24/`：`before.json`、`after.json`、`pytest.log`、`tests.json`（审计）、`result.json`。

| 指标 | 实测 |
| --- | --- |
| 退出码 | `0`（启动器 `{"exit_code": 0, "same_checkout": true, "audit_exists": true}`） |
| 收集 / 执行 | **2,964 / 2,964** |
| passed | **2,948** |
| failed | **0** |
| skipped | 15（保留） |
| xfailed | 1（保留） |
| warning | 1（保留） |
| pytest 自报耗时 | **2,966.13 s（0:49:26）** |
| 墙钟（含两次全仓摘要） | **2,977.88 s（49 分 37.88 秒）** |
| 前后摘要 | `before == after == 运行后独立复算` |
| 用例审计 | `nonunique=[]`、`missing=[]`、`extra=[]`、`repeated=[]`；`collected == reported` |

保留项明细：15 个 skipped 全部是 Windows 平台的文件系统链接/符号链接与可选 Schemathesis 用例（`test_report_publication` 8 项、`test_report_files_portable` 3 项、`test_console_api` 2 项、`test_mcp_service`、`test_project`、`test_mature_tools_comparison` 各 1 项）；1 个 xfailed 是 P3-B2.x 期间保留的 `test_real_task_guard_preserves_extracted_text_without_repairing_model_plan[EnglishName-0]`；warning 是 `product_design.py:55` 的 `PytestCollectionWarning`（`class TestDesign` 带构造函数）。三者数量与历史冻结回归一致，未被删除或改写。

最慢 20 项合计 1,141.21 s，占 38.5%，全部是历史遗留的重型离线评测/重打分与进程中断恢复用例（`test_generic_arguments` 148.33 s / 141.76 s、`test_generic_references` 112.45 s、`test_pocketbase_acceptance` 95.30 s 等）；**新增的 A1/A2/A3 用例不在最慢榜内**。

### 12.3 修复覆盖证据

| 覆盖对象 | 本次执行的用例数 | 结果 |
| --- | ---: | --- |
| `tests/test_p3b24_diagnostics.py`（A1 载荷完整性 + A2 标量约束） | 24 | 全 passed |
| `tests/test_p3b24_references.py`（A3 引用候选） | 11 | 全 passed |
| `tests/test_p3b23_diagnostics.py`（调整后的候选契约） | 15 | 全 passed |
| `test_product_focus` / `test_product_checks` / `test_product_tasks` | 30 / 9 / 25 | 全 passed |
| `test_planning_flow_instances`（A2 路径）/ `test_plan_construction`（A3 路径） | 14 / 17 | 全 passed |
| `test_obligation_gate`（修复约束渲染） | 28 | 全 passed |
| `test_step_recovery`（进程中断与未知写恢复） | 20 | 全 passed |
| `test_human_review` / `test_task_gate`（HITL 与终止） | 3 / 18 | 全 passed |

被点名确认执行并通过的用例包括：`test_a_length_only_violation_names_exact_length_not_exact_value`、`test_a_check_that_breaks_both_fields_names_both_in_one_entry`、`test_the_repair_payload_names_all_seventeen_archived_positions`、`test_the_repair_payload_covers_the_widest_legal_proposal`、`test_a_payload_beyond_the_cap_counts_the_remainder_instead_of_dropping_it`、`test_an_over_long_literal_reports_the_real_length_rule`、`test_a_producer_declared_later_is_advertised_with_its_order_constraint`、`test_a_forward_reference_is_still_constructed_first_and_accepted`、`test_a_cycle_through_a_policy_prerequisite_is_still_refused`、`test_self_reference_is_refused_and_a_legal_producer_is_still_named`、`test_a_negative_variant_without_binding_rules_uses_the_approved_lifecycle_identity`。工具链：Ruff 与 `git diff --check` 在本候选上均为通过状态（§11.7.4）。

### 12.4 与历史耗时的比较（不作性能结论）

| 指标 | 历史（p3b23 冻结验收环境） | 本次 |
| --- | --- | --- |
| 收集/执行 | 2,929 | 2,964（+35，正好等于新增 A1/A3 用例数） |
| passed / skipped / xfailed / failed | 2,913 / 15 / 1 / 0 | 2,948 / 15 / 1 / 0 |
| pytest 耗时 | 2,436.92 s | 2,966.13 s（**+529.21 s，+21.7%**） |
| 单项均值 | 0.832 s | 1.001 s（+20.3%） |

同一条命令、同一 `-q --tb=short --durations=20` 组合、同一台 Windows 主机；本次没有更快。差异集中在既有的重型离线评测/重打分用例（最慢 20 项占 38.5%），新增的 35 个用例即使按均值计也只解释约 35 s。**单次运行的耗时差异不足以判断启动器或本轮修复的性能影响，也不能据此宣称性能改善**；如需结论应做多次重复测量。

### 12.5 结论与边界

- 全量离线冻结回归 **PASS**：退出码 0、0 failed、审计漏项/额外项/重复收集/重复执行均为 0、前后工作区摘要一致、2,964 项全部有且仅有一次执行记录。
- A1（17 处载荷完整性与字段路径）、A2（标量约束事实）、A3（引用候选与顺序约束）的定向用例全部在本次全量回归中被收集并执行通过；安全与恢复类用例（`test_obligation_gate`、`test_human_review`、`test_task_gate`、`test_step_recovery`）同样全部通过。
- 仍未解决、且本轮未动的风险：A4（`NO_PROGRESS` 与"每组一次形式修复"的交互）、B1（`JSON_SYNTAX` 只给字符位置）、B2（`FOCUS_SOURCE_CLAUSE_MISSING` 码名与多报键语义不符）、模型服务侧 60 秒超时与 `UNKNOWN` 用量（三次超时记录仍无服务端对账）。
- P3-B2.3 的 **NO-GO 继续有效**（本轮没有真实模型证据）；真实模型验收需另行授权，五轮验收与 P3-B3 均未获准。工作区仍为 `HEAD f311721` 之上的未提交状态，本轮未提交任何 commit。

## 13. 真实稳定性验收（P3-B2.4，2026-11-17）

操作者授权三次健康探针 + 连续五轮真实验收。全部沿用既定配置（`ecnu-max`、60 秒请求超时、31 项批准义务、24 项负向策略、隔离 parcels 靶场、持久化、`createParcel` HITL 批准、每任务 120 次业务 HTTP / 64 次模型调用 / 650000 tokens / 900 秒），未改 A4/B1/B2、未改发布门槛、未进入 P3-B3。

### 13.1 冻结现场

| 项目 | 值 |
| --- | --- |
| HEAD | `43a60657282999b93a69157e993cb135cc422530` |
| 工作区 | 1 项未提交历史文档 `M docs/deepagents-hitl-reliability-p3b23.md`（+28 行 NO-GO 记录，整批未变） |
| `checkout_sha256` | `6ea4b00976aaa9d29cfd5bf3e666325d9c8a729da422bef12b9468f2ac8ea534` |
| 源码摘要 | `e287b6057674b0177fcb2ce74caac697682acc9852ab9585f028c13a795c233d` |
| 回归证据 | `tests.json` `23ad03e9…`、`pytest.log` `478aa81b…`、`before == after`、2,964/2,948/15/1 |
| 逐文件摘要 | 11 个关键源码/测试/配置前缀全部匹配（`0a49079be7670f5e`、`6681f4c64ece9bf7`、`e3361633f2d2587d`、`c56c528963446d77`、`b44a392d89a5f082`、`48cfaeff80bf4180`、`50c7d63cf780c17a`、`b7d1202843886d8b`、`d0fddb6b86ce7ec9`、`68e83d3cd7892c45`、`9947e91bfe89656d`） |
| 公开配置摘要 | `cecf6dff2dccf853e1da5327457631243d9d9271f0575d1c6a823261aca52172`（与 P3-B2.3 完全一致） |
| 并行验收进程 | 无（`check_running_processes` 通过） |

启动器 `.eduwork/p3b24-stability-acceptance.py` 在每轮前后重算上述摘要并比对 `.env` 摘要，任一处漂移即中止。首版启动器因探针请求体是 `bytes` 而无法序列化，崩在第一次探针**发送之前**（零模型调用），该次尝试保留在 `.eduwork/p3b24-acceptance-43a6065-aborted-1/`；修正后重新起跑，三次探针才被消耗。

### 13.2 三次健康探针

| # | 本地调用 ID | 请求摘要 | 请求时间 | 耗时 | 可解析 | tokens | 服务端响应 ID |
| --- | --- | --- | --- | ---: | --- | ---: | --- |
| 1 | `66f77e39d6574ebb8d26d13cc1b59032` | `3ea045da…`（391 B，`{"ok":true}` 最小 schema） | 14:08:45Z | 1.687 s | 是（`{"ok":true}`） | 65（59+6，KNOWN） | `a91eac7c57864a3c8858253d0a35a86d` |
| 2 | `c13692d5035f40168f317903e3ef87d9` | 同上（字节完全相同） | 14:08:48Z | 1.500 s | 是 | 65（59+6，KNOWN） | `694d318ca42e46e2be119c4a5b99d9fd` |
| 3 | `6630c9e3f5b5479d8ae26df2edc81b96` | 同上 | 14:08:50Z | 1.297 s | 是 | 147（142+5，KNOWN） | `80c1fecf-e618-43c6-ae3a-d14a6646232a` |

三次串行、全部 `READY`、`usage_state=KNOWN`、`provider_request_id` 为空（网关未回传）而 `provider_response_id` 均有值；探针不触碰业务靶场（0 次业务 HTTP）。健康检查通过后才启动五轮。

### 13.3 五轮结果

证据：`.eduwork/p3b24-acceptance-43a6065/`（`batch.jsonl`、`health.jsonl`、`positions.jsonl`、`release-v1..v5/`），分级：`.eduwork/p3b24-audit/report.json`。

| 轮 | 批准义务 | 负向策略 | 终态 / 终止原因 | 停止原因 | 账本调用 / tokens | 业务 HTTP | 创建/清理 | 503 | 耗时 |
| --- | --- | --- | --- | --- | --- | ---: | --- | ---: | ---: |
| v1 | **27/31** | 20/24 | `PARTIAL` / `OBLIGATION_STALLED` | `ANSWER` | 48 / 404,570 | 82 | 11 清 11、**3 未证明** | 1 | 305.2 s |
| v2 | **29/31** | 22/24 | `PARTIAL` / `OBLIGATION_NOT_DISPATCHED` | `NO_PROGRESS` | 43 / 336,635 | 87 | 13 清 13 | 1（+1 复核） | 289.0 s |
| v3 | **0/31** | 0/24 | `NEEDS_REVIEW`（未进入运行时） | — | 2 / 29,434 | 0 | 0 | 0 | 25.2 s |
| v4 | **26/31** | 19/24 | `PARTIAL` / `OBLIGATION_ATTEMPTS_EXHAUSTED` | `ANSWER` | 49 / 408,914 | 79 | 14 清 14 | 1（+1 复核） | 326.1 s |
| v5 | **26/31** | 19/24 | `PARTIAL` / `OBLIGATION_STALLED` | `ANSWER` | 50 / 426,049 | 74 | 10 清 10 | 1 | 280.2 s |

合计：**192 次模型调用、1,605,602 tokens、322 次业务 HTTP、0 次超时、0 次 `UNKNOWN` 用量**；48 次创建全部证明清理，另 3 次有删除回执但缺少回读证明（`CLEANUP_UNPROVEN`）；4 轮各观测到一次真实 `readAvailability` 503。五轮合计 1,241.7 秒（20 分 41.7 秒，含探针与摘要核算），五轮本身 1,225.7 秒。

### 13.4 逐轮安全与清理

- 零越权：五轮业务事件全部落在 `/availability`、`/parcel-observations`、`/parcels` 与 `/parcels/parcel_*` 的批准方法内；`enforcement.boundary_rejected=true`，探针写入/编辑/执行/任务/读取全部被拒。
- 零未知写入重放：`http_execution` 每行 `attempt=1`、`phase=EVIDENCE_COMMITTED`；`write_effects.replayed=0`。
- 零未批准 POST：23/23、23/23、20/20 等 POST 均能对上已批准 HITL 组的步骤；33 次审批全部 `APPROVED`，零 `REJECTED`。
- 负向策略零重复：每个已执行策略恰好一次（`max_per_policy=1`）。
- 清理：48 次创建-删除全部带回读证明；v1 的 `createParcelB/C/D` 只有删除回执（`DELETE_RECEIPT_WITHOUT_READ_BACK`），靶场末态 `record_count=0`，没有真实残留，但门禁拒绝把它算作已清理——这也是 v1 唯一的安全项失败。
- 诚实性：五轮 `checks_passed` 均为 `false`，`complete` 均为 `false`；503 以 `ASSERTION_FAILED` 记录且 `cause_confirmed=false`、归入 `undecided`，没有把 503 当成通过。

### 13.5 A1/A2/A3 的真实运行效果

- **A1（已生效，3/5 轮）**：v3、v4、v5 的首次 Focus 提案都以 `FOCUS_SOURCE_CLAUSE_MISSING` 被拒，返回 `agenttest.focus-repair.v1` 载荷：`must_delete`、`must_be_null`、`violations`、318 字符提示（含"按 must_delete…omitted…"新句）。v4/v5 第二次提案即 `SELECTED`——修复成功，和 P3-B2.3 v1 的"修不好直接死"不同。v3 的第二次提案仍被 `FOCUS_INPUT_TEXT_CHANGED` 拒绝，但反馈已是**分组**形态：`fields` 8 项、2 个分组、`count` 合计 15、具体路径 14 条（归档形状的 17 处上限与 `omitted` 机制按设计工作）。
- **A2（未触发）**：五轮没有出现 `DESIGN_INSTANCE_LITERAL_INVALID` 等标量约束诊断，A2 的真实效果在本批没有样本。
- **A3（已生效，1 次）**：v5 `negative_batch_7` 第 21 次监督轮，模型把 `from_step` 指向不存在的步骤，返回 `CONSTRUCTION_REFERENCE_INVALID`，`detail` 带 `reference=SOURCE_UNKNOWN`、`allowed_from_steps=[]`、`direct_from_steps=[]`、`reordered_from_steps=[]`、`step_id`、`obligation_id=web_rule_7_negative_8`、`case_index=0`——空候选集被如实说明，没有编造生产者。

### 13.6 判定：NO-GO

门限不变（至少 4/5 轮达到 31/31）。实际 **0/5**，最高 v2 的 29/31，因此 **NO-GO**，五轮证据全部保留，不补跑、不替换样本，未进入 P3-B3。

失败分类：

| 类别 | 证据 |
| --- | --- |
| **B 模型提案/JSON 构造失败（主因）** | `JSON_SYNTAX` 在同一分组的同一字符位置反复出现：v1 `negative_batch_2` 位置 2043 连续 7 个监督轮、`negative_batch_3` 位置 1295 连续 6 轮、`batch_7` 2509；v2 位置 1295（6 轮）、1530（5 轮）、2844/2183；v4 3 次、v5 4 次。设计批次生成不出合法 JSON，2–5 项已批准负向策略永远没有执行证据。v3 的 `FOCUS_INPUT_TEXT_CHANGED`（13 处字面值被改写）同属此类。 |
| **A4（已测量）：有界修复把 B 类失败固化成漏项** | v2 以 `NO_PROGRESS` 结束、v1/v5 以"连续两次结束请求没有新增已测量义务"停止、v4 报 `OBLIGATION_ATTEMPTS_EXHAUSTED`；同一分组的重复形式失败被尝试上限截断，剩余义务停在 `NOT_EXECUTED`。 |
| **B1（已取证）：`JSON_SYNTAX` 提示只有字符位置** | 修复载荷仅 `{position, line, column}`（`hint` 为空串），模型在 7 个连续监督轮里在同一位置重复同样的错误——提示没有给出任何结构线索。 |
| **D 已批准的终止行为** | v3 两次 Focus 被拒后零业务请求、`NEEDS_REVIEW`、目标未被触碰；v1 的 3 次"删除无回读"被如实记为 `CLEANUP_UNPROVEN` 并阻止 GO，而不是算作已清理。 |
| **A Harness 代码缺陷** | 未发现：无崩溃、无越权、无重放、无把未完成标成 `COMPLETE`、无把 503 当成通过。 |
| **C 模型服务/网关** | 未发生：192 次调用全部 `KNOWN`，0 次超时，0 次 `UNKNOWN`。 |
| **E 证据不足** | 无：每一项未测义务都有对应分组的拒绝记录、`not_covered` 说明或未派发原因。 |

### 13.7 仍未解决

- **A4**：失败已量化——同一分组的重复形式失败会消耗有限重试，最终把 2–5 项负向策略永久留在 `NOT_EXECUTED`，并以 `ANSWER` 收场（v1/v4/v5 各 5 项以内的漏项）。
- **B1**：见 §13.6，提示缺结构信息，实测同一错误位置重复 7 轮。
- **B2**：v3/v4/v5 首次提案全部因 `requirement_map.request_2` 被拒，说明"可信范围子句"这一名字与"多键语义"（`must_delete` 1 项 + `must_be_null` 8 项）仍会让模型误解。
- **模型服务风险**：本批 0 次超时，仅说明"未复现"，P3-B2.3 的三次 `MODEL_TIMEOUT` 与 `UNKNOWN` 用量仍无服务端对账。
- **靶场 503**：4/5 轮观测到 `readAvailability` 503（`EXPECTED_200`），按既定规则记为"未证实原因的异常"，既不算通过，也不算 Harness 缺陷。

### 13.8 下一步建议

1. P3-B2.5 先修 **B1**（`JSON_SYNTAX` 给出可操作的结构提示：失败位置附近的期望记号/片段，仍不回显被拒取值）与 **A4**（同一分组重复同一形式失败时，改为一次性明确失败并把义务标成 `NOT_DISPATCHED` 的原因，而不是多轮静默重试后以 `ANSWER` 收场），再考虑 **B2** 的码名/多键语义对齐。
2. 修完后重新冻结、跑一次全量离线回归，再申请**新的一批**五轮（本轮不自动启动）。
3. 不进入 P3-B3；P3-B2.3 与 P3-B2.4 的 NO-GO 结论并存保留。

### 13.9 更正（P3-B2.5 复核归档原文后）

- §13.6 的"同一分组的同一字符位置连续 7 个监督轮重复"**不成立**：`phase-NN.json` 的
  `design_records` 是累计列表，同一条记录被后续每个 phase 文件重复计入。按
  `model_call.local_call_id` 去重后，本批 61 次设计调用中 `JSON_SYNTAX` 共 14 次
  （v1 3 / v2 4 / v4 3 / v5 4），其中 13 次的下一次调用即产出合法 JSON，只有 v2
  `negative_batch_7` 用满三次尝试。
- 因此"2–5 项负向策略因 JSON 失败永远没有执行证据"也不准确：真正让义务停在
  `NOT_EXECUTED` 的是**案例级预检拒绝**（四轮 31 个被拒案例中 27 个是
  `OPENAPI_UNKNOWN_BODY_FIELD` 等语义错误，且拒绝载荷只回 code，不带
  `preflight_diagnostic` 的结构化事实）以及 v2 `batch_7` 的形式错误耗尽。
- 判定不变：0/5、NO-GO，五轮证据全部保留。归因与修法见
  [P3-B2.5 诊断记录](deepagents-hitl-reliability-p3b25.md)。
