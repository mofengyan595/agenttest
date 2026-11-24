# P3-B2.8 · 模型调用可靠性专项诊断

日期：2026-11-24（UTC+8）。候选 `f3733c14c5da6b33cfc7a9a70812566f72dd7c85`。两批 P3-B2.7 的原评分各为 **1/5、NO-GO**。

## 结论

- **Design 超时**：两批都在 `negative_batch_5` 第一次 Design 记录 `MODEL_TIMEOUT / DEADLINE`，分别耗时 60,062 和 60,078 ms，客户端均未记录响应头、HTTP 状态、正文或服务端 ID。证据最符合 Design 的内层 60 秒 asyncio 总时限。客户端无法据此判断网关是否收到了请求，以及时间花在连接、上传、排队还是推理。
- **Focus 修复**：第二批 v2、v3、v4 的六次调用均返回 HTTP 200，且 usage 为 KNOWN。回放确认缺项计算、来源引用和实际发送的反馈一致。第二份提案分别缺 1、24、23 项。v3、v4 的实际修复请求没有携带首份提案中可保留的合法映射快照，随后模型分别丢掉 24、22 项原有映射。
- **UNKNOWN**：五条中四条有明确超时类型。首批 v5 的另一条没有足够证据分类。两批都保留原始 UNKNOWN，账本未估算实际用量或放行该任务的后续模型调用。
- **下一步**：先补齐调用阶段和取消来源的可观测性，并在离线测试中验证 Focus 的保留映射反馈。当前证据不足以调整 60 秒配置或支持第三批同条件验收。

## 冻结身份与归档保护

本地 HEAD 和 `origin/master` 的本地跟踪引用与归档提交相同。用第二批既有的冻结检查逐文件比对离线候选。诊断开始前仅两份既有报告有改动。两批运行时源码摘要相同（`41a4a08f88ee9dbb42f5b9894ad31820cc708a8125f657eec950f48756949854`），归档检查的测试清单、公开配置、原评分器摘要也各自一致。首批 checkout 摘要为 `09f116e204a46476ef0293ef6a400e054c405c383df5bba7d0ab393ca2c98a96`，第二批为 `595888e770b13f71038b88c554d32e36d9775fc8de60877e5c02d2dc57772ee8`，差异来自第二批之前追加的报告和进度。冻结回归 checkout 摘要为 `2c591aa40840d725d77dc2f251fa3b2ce63a2becd25c13ff4d76e6bf3b5ee98b`。

首批 324 个文件与第二批记录的逐文件摘要一致。此次两批诊断前后分别比对 324 和 193 个文件，零变化。以只读、immutable 方式读取 SQLite 及 Durable Checkpoint。在新调查目录保留请求重编码摘要、所有缺项的 `operation_id/policy_id/check_ref/source_ref` 和 UNKNOWN 对账。审计脚本复用冻结编码器，使用会拒绝任何请求的 MockTransport，未输出原始提示、正文或凭据。[首批评分](../.eduwork/p3b27-acceptance-f3733c1/audit.json) · [第二批评分](../.eduwork/p3b27-retest-f3733c1/audit.json) · [调用可靠性原审计](../.eduwork/p3b27-retest-f3733c1/model-reliability-audit.json) · [逐项回放](../.eduwork/p3b28-diagnosis-f3733c1/replay.json)

| 原验收指标 | 第一批 | 第二批 |
| --- | ---: | ---: |
| 原评分达标 | 1/5 | 1/5 |
| 确认超时 / UNKNOWN | 3 / 4 | 1 / 1 |
| Focus / Design 拒绝 | 3 / 12 | 7 / 4 |
| 模型调用 / 已知 tokens | 116 / 942,732 | 55 / 486,848 |
| 业务 HTTP | 306 | 142 |

两批各三次健康探针均 READY / KNOWN，第一批耗时 0.531–1.313 秒，第二批 1.343–1.704 秒。探针用量与上表正式验收用量分开记录。第二批更少的业务请求也包含三轮在 Focus 结束、零业务 HTTP 的影响。第二批 v1 的 P3-B2.6 P1 引用反馈有一次真实修复证据，P3-B2.7 第三次剩余义务修复在两批均未触发。[原验收与复验报告](deepagents-hitl-reliability-p3b27.md)

## P0 · negative_batch_5 重复超时

### 逐调用对账

两条记录都是 `ecnu-max`、`design`、`negative_batch_5` 第 1 次。回放原始 Context 证实两条都请求 `replaceParcel` 的 `web_rule_7_negative_1`、`web_rule_7_negative_10`、`web_rule_7_negative_2`、`web_rule_7_negative_3`，均使用两个消息（system 和 user），输出上限均为 8,192 tokens。

| 字段 | 第一批 v2 | 第二批 v5 |
| --- | --- | --- |
| 调用 ID | `b20ea1f7ddeb4c3e8649566f51d799b9` | `3907e8459c2a4154af37469b06be553f` |
| 起止（11-23，UTC+8） | 21:45:56.195 → 21:46:56.255 | 22:53:10.564 → 22:54:10.656 |
| 单调时钟耗时 | 60,062 ms | 60,078 ms |
| 请求 / 内嵌 JSON Schema | 39,301 / 9,946 bytes | 38,456 / 9,922 bytes |
| 客户端输入上界 | 39,813 tokens | 38,968 tokens |
| 请求 SHA-256 | `f181e871f18415e57f2158857d514d9efc9dd2f5b701ffeaa6d53a75d1e22ecd` | `0ce0c6b763326b1e194ad3f1fe4e7739e6ba5a5bf76ca45b9da59037995e8e52` |
| 响应头 / HTTP 状态 | 客户端未记录 / null | 客户端未记录 / null |
| 部分正文 / 服务端请求与响应 ID | 0 记账 bytes / null、null | 0 记账 bytes / null、null |
| 超时 / 用量 | `MODEL_TIMEOUT / DEADLINE` · `UNKNOWN` | `MODEL_TIMEOUT / DEADLINE` · `UNKNOWN` |
| 原始 Context / SQLite 顺序 | `model-outputs.jsonl:23` / 第 27 条 | `model-outputs.jsonl:15` / 第 17 条 |
| 保存失败 ToolMessage 的 Checkpoint | `1f1e7443-4d89-6578-8079-4e2bc390249c` | `1f1e74d9-9a0d-6ff9-8049-7d25605c033f` |

输入上界是运行时的 UTF-8 请求字节数加 512，与实际分词用量分开。两条实际 input/output/total tokens 均为 null。phase Design 记录的 `model_call` 与 SQLite 字段一致。Checkpoint 留有同组 `run_test_group` 的失败 ToolMessage，phase 报告保留消费该结果前的已保存 checkpoint 游标。它们记录同一失败的不同环节。客户端在进入 HTTPX stream 前已把 `calls` 记为 1，两次都没有保存响应头或 16 KiB 分块的正文，不能据此判断网关侧是否收到请求或网络上是否已有不完整字节。[第一批 v2](../.eduwork/p3b27-acceptance-f3733c1/release-v2/) · [第二批 v5](../.eduwork/p3b27-retest-f3733c1/release-v5/)

| 预算证据 | 第一批 v2 | 第二批 v5 |
| --- | --- | --- |
| 上一阶段已存快照 | phase-05：23/44 次调用、196,487/420,000 tokens、38/120 HTTP | phase-04：15/40 次调用、120,827/360,000 tokens、32/120 HTTP |
| 失败调用之前，按 SQLite 逐条累加 | 26 次 KNOWN，217,973 tokens | 16 次 KNOWN，131,238 tokens |
| 失败后阶段 | phase-06：27/48 次调用、活动 token 上限 480,000、54/120 HTTP | phase-05：17/44 次调用、活动 token 上限 420,000、48/120 HTTP |
| 失败后门禁 | `usage_known` 拒绝下一模型预约，用量 used/remaining 为 null | 同左 |

两轮的任务硬上限为 64 次模型调用、650,000 tokens、120 次业务 HTTP、900 秒。快照展示的是自适应活动限额。两轮均以 `MODEL_USAGE_UNKNOWN` 停止模型调用，执行预算 `stop_reason` 为 null。SQLite 无后续调用，证据没有显示数值预算耗尽。

### 实际生效的超时层

| 层 | 冻结配置与行为 | 这两次调用的证据 |
| --- | --- | --- |
| Design 传输总时限 | `OpenAICompatiblePlanner._generate` 先编码和预约，再用 `asyncio.timeout(settings.timeout)` 包住 HTTPX 请求及读流，配置为 60 秒 | Python `TimeoutError` 映射为 `DEADLINE`。约 60 秒且 planner 自己记录错误，最符合该层 |
| HTTPX 阶段时限 | `AsyncClient(timeout=60)` 的连接、读、写和连接池时限按阶段生效。读取时限作用于等待数据的阶段 | 对应异常分别映射 `CONNECT/READ/WRITE/POOL`。这两条均未记录这些类型 |
| Design 调用外壳 | `ProductTasks` 以 `settings.timeout + 1`（61 秒）调用 `propose_design`，外壳还覆盖生成器的其他工作 | 通常给内层留出转换与记账时间。外层先取消可能留下 UNKNOWN 及空错误类型 |
| 任务外层 | `ProductTasks` 用 900 秒减去已用的活跃时间后设外层截止，可能比单次边界更早 | 无任务 deadline 终止或预算停止证据。已有调用的位置及错误记录支持内层边界先触发 |

最早到期的绝对截止点决定取消来源。HTTPX 各阶段计时可能比 60 秒总时限晚开始，因此外层到期时无法仅凭 `DEADLINE` 识别已进入哪个传输阶段。Agent 使用另一个 `deep_harness/chat_model.py` 传输路径，有 HTTPX 阶段时限，未在该路径用 60 秒的 asyncio 总时限包住整段流。首批 v3 的 `READ` 与其 122.938 秒整次耗时相容，现有记录无法还原各阶段耗时或推定发生了几次读取。[异常分类与 Design 传输](../src/agenttest/llm_planner.py#L50) · [Design 外壳](../src/agenttest/product_design.py#L750) · [任务截止与传参](../src/agenttest/product_tasks.py#L702) · [Agent 传输](../src/agenttest/deep_harness/chat_model.py#L336)

### 同类成功请求

回放中可比较的 `negative_batch_5` 共七次，其中四次 HTTP 200 且 JSON 可接受，一次 HTTP 200 但 JSON 拒绝，两次无响应证据。下表均为四项策略、两个消息、输出上限 8,192；tokens 为服务端报告的 input/output。

| 批次 / 轮次 / 组 / 尝试 | 请求 B | Schema B | ms | input / output | JSON 与 Design |
| --- | ---: | ---: | ---: | ---: | --- |
| 首批 v1 · 5 · 1 | 38,456 | 9,922 | 33,313 | 9,600 / 1,695 | 有效，通过解码 |
| 首批 v4 · 5 · 1 | 38,435 | 9,922 | 7,797 | 9,684 / 1,815 | 有效，通过解码 |
| 首批 v4 · 5 · 2 | 39,641 | 9,958 | 6,375 | 10,005 / 1,771 | 已收到，无效 JSON |
| 首批 v4 · 5 · 3 | 39,439 | 9,946 | 58,390 | 9,878 / 1,767 | 有效，通过解码 |
| 第二批 v1 · 5 · 1 | 38,456 | 9,922 | 6,297 | 9,682 / 1,731 | 有效，通过解码 |
| 首批 v2 · 5 · 1 | 39,301 | 9,946 | 60,062 | UNKNOWN | 无可解析响应证据 |
| 第二批 v5 · 5 · 1 | 38,456 | 9,922 | 60,078 | UNKNOWN | 无可解析响应证据 |
| 首批 v1 · 4 · 1 | 35,375 | 8,428 | 5,578 | 8,782 / 1,388 | 有效，通过解码 |
| 第二批 v5 · 4 · 1 | 35,375 | 8,428 | 42,500 | 8,688 / 1,358 | 有效，通过解码 |

同为 38,456 B / 9,922 B 的三条请求摘要不同，结果跨越约 6 秒、33 秒和超时。39,439 B 的同组请求成功，39,641 B 的请求也在 6.4 秒获得响应。样本只有两个同组超时，内容和运行时不同，无法估计请求体大小与超时的关联。成功样本已经排除了一个固定的约 39 KB 响应门槛解释。

**已证实**：同一组的首次 Design 在两批均被记为 `DEADLINE`，均无记录到的响应，失败后 UNKNOWN 门禁生效。**高可信推断**：触发的是 Design 内层 asyncio 的 60 秒总时限，现有证据未显示 HTTPX 分段超时、任务截止或 JSON 解码参与。**仍不可判定**：客户端等待的具体阶段、网关是否入队、服务端执行或用量、请求结构和超时的因果关系。

## P1 · Focus 一次修复后仍缺映射

### 原始响应与冻结解码

用原始 Context 重建请求，与 SQLite 的请求字节数和 SHA-256 逐条相等。下表的映射数是从原始响应按对应操作的批准目录解码的负向 `requested_checks` 条目。六份提案均未通过完整 Focus 校验，因此没有可持久化的已接受 Focus。三轮最终均为 `NEEDS_REVIEW`，业务 HTTP 为 0。

| 轮次 | 第一次提案 | 第二次提案 | 变化 | 最终原因 |
| --- | --- | --- | --- | --- |
| v2 | `selected`，0/24，缺 24；`FOCUS_REQUESTED_CHECKS_MISSING` | `selected`，23/24，缺 1 | 新增 23；两次仍选中相同六个操作 | 缺 `createParcel / web_rule_3_negative_2 / check_3 / source_3` |
| v3 | `selected`，24/24，缺 0；因多余 `requirement_map.request_2` 被拒，码为 `FOCUS_SOURCE_CLAUSE_MISSING` | `all_ready`，`operations={}`，0/24，缺 24 | 正确删除多余条目，范围模式切换后丢失原有 24 项 | `FOCUS_REQUESTED_CHECKS_MISSING`，请求级无显式映射 |
| v4 | `selected`，23/24，缺 `createParcel / web_rule_3_negative_2 / check_3 / source_3` | `selected`，1/24，缺 23 | 同一组六个操作，保留 `createParcel / web_rule_3_negative_3 / check_4 / source_3`；丢失 22 项，原缺项未补 | `FOCUS_REQUESTED_CHECKS_MISSING` |

v2 的第一份、v3 的第二份均缺整套 24 项。目录映射为 `createParcel` 的 `web_rule_3_negative_*`（10 项）、`changeParcel` 的 `web_rule_6_negative_*`（4 项）、`replaceParcel` 的 `web_rule_7_negative_*`（10 项）。对应 `check_ref` 在每个操作下独立编号。10 项组按策略后缀 `1,10,2,3,4,5,6,7,8,9` 依次映射 `check_1..check_10`，4 项组按 `1,2,3,4` 映射 `check_1..check_4`。这些缺项的 `source_3` 都可在实际编码的 `input_sources` 中解析为当前范围子句。v4 第二份的精确缺失集合是该 24 项目录去掉 `web_rule_3_negative_3`。回放复算六份提案的缺项集合与归档逐项相等，三份最终拒绝的完整结构化诊断也相等。[逐项映射、解码结果与两次请求摘要](../.eduwork/p3b28-diagnosis-f3733c1/replay.json)

| 轮次 | 第一次 HTTP / bytes / ms / 已知 tokens | 第二次 HTTP / bytes / ms / 已知 tokens | 第二次实发请求 SHA-256 |
| --- | --- | --- | --- |
| v2 | 200 / 47,360 / 13,406 / 13,991 | 200 / 55,324 / 17,610 / 16,213 | `0dd224610ead1cf6ddbd7b0004c63b00e8fe8710184f7dc1c9954d2fc6767580` |
| v3 | 200 / 47,360 / 4,797 / 14,278 | 200 / 50,684 / 1,640 / 14,060 | `fd598e8de3956e15d394a1e297a4050d8f3b82e284c502fafe2c800e7c5f212a` |
| v4 | 200 / 47,360 / 41,719 / 14,330 | 200 / 51,158 / 7,219 / 14,453 | `baa463556a820f53d49e0221ba8173d9ba892f741a770ad8911eb80167289b30` |

### 反馈传递与保留项

- v2、v4 的实发 `previous_error` 与原始 Context 的投影相等。分别完整包含 24、1 项缺失映射，`check_ref` 均归属指定操作，`source_ref` 可解析。提示要求在 `selected` 的对应 `requested_checks` 中补齐，保留已有合法项，按规则置 null，不为可信范围子句新增 `requirement_map`。两次都没有 `omitted` 或 `omitted_detail`。
- v3 的实发反馈要求删除 `requirement_map.request_2`，保留 `request_1`，并给出可信范围子句。首份映射完整，所以 A1 没有生成缺项诊断。模型完成了指定的删除，但下一份选择了通用可用的 `all_ready` 枚举并清空显式映射。整个发布范围的原校验仍要求 24 项，因此拒绝。
- A1 在这三个首轮请求中各有一个实际错误家族。没有证据显示聚合或脱敏吞掉了具体缺项、错配了引用，或出现两条相反的修复指令。三轮的第二次请求都包含当前批准目录和错误反馈，但 `previous_result` 和 `previous_requested_checks` 没有携带本轮被拒提案中已有效的选择。反馈要求重写完整提案；v3 的来源修复也没有明确钉住现有 `selected` 操作和 24 项保留映射。对 v4，文字要求保留合法项，但没有列出应复制的 23 项。
- 生产路径在第一次拒绝后调用 `focus_repair_payload`，投影到实际 Focus 的 `previous_error`。第二次输出会重新走解码、来源、范围与 requested checks 校验。三次最终拒绝都符合已有规则。P3-B2.6 的完整性门禁阻止了无证据映射被纳入后续执行。[修复投影和单次修复](../src/agenttest/product_tasks.py#L165) · [Focus 编码与来源校验](../src/agenttest/product_focus.py#L419) · [批准负向检查校验](../src/agenttest/product_checks.py#L172) · [Focus wire](../src/agenttest/product_focus_wire.py) · [模型输入脱敏](../src/agenttest/model_input.py)

**已证实**：有效 HTTP 响应后的模型提案触发三次最终拒绝。缺失条目诊断及来源完整送达。v3、v4 的修复上下文没有携带从首份提案提取的、可保留的有效映射。**高可信推断**：对重写完整提案的模型，显式保留集合可以减少修复单字段时改动有效内容的机会。**仍不可判定**：模型为何省略或删除特定映射，以及添加保留集合后真实成功率会如何。当前没有复现错误的缺项计算或诊断投影丢失。

## UNKNOWN 用量与恢复安全

| 批次 / 轮次 | 调用 ID | 阶段 / 组 | bytes / ms | 精确分类 |
| --- | --- | --- | --- | --- |
| 首批 v2 | `b20ea1f7ddeb4c3e8649566f51d799b9` | Design / 5 | 39,301 / 60,062 | 已证实 `DEADLINE` |
| 首批 v3 | `900d170e99384ae294cb2b2b3615c68c` | Agent / 第 10 次 Agent 调用 | 46,219 / 122,938 | 已证实 `READ`；Agent Trace 和账本逐字段一致 |
| 首批 v4 | `f98cc31b1b5f411b9a170e1ecd6cfad1` | Design / 6 | 38,500 / 60,062 | 已证实 `DEADLINE` |
| 首批 v5 | `fa75b056a6b14e19a393e5842a7c81d6` | Design / 4 | 35,375 / 318,656 | 证据不足。账本无 `reason/error_type/timeout_kind`，未归档可重编码的该次 Context |
| 第二批 v5 | `3907e8459c2a4154af37469b06be553f` | Design / 5 | 38,456 / 60,078 | 已证实 `DEADLINE` |

五条都已在本地发起调用尝试，均无 HTTP 状态、可识别的服务端请求或响应 ID、已记账正文及实际 token 用量。没有可归入“完整响应缺 usage”或“响应内 usage 不一致”的记录。首批 v5 的 318.656 秒和空分类字段不足以判断超时、任务取消或服务端延迟。phase 最终保留 `MODEL_USAGE_UNKNOWN`，没有记录任务截止。输入保守上界不能作为实际用量。

两批分别 112/116 和 54/55 次有已知用量。账本先写 `PENDING`，然后根据观测结算，五次均写入 `UNKNOWN`。无补录、豁免、后续同任务模型调用。恢复门禁查询未解决的 `PENDING/UNKNOWN` 以阻止新的调用。历史 448 条业务 HTTP 尝试均为 `EVIDENCE_COMMITTED`，无超过首次的尝试记录。原审计显示两批 83、38 条 POST 均匹配 HITL，37、20 次创建均有同身份删除后的 404 回读，无越权、未知写入或写入重放。未改变历史用量与评分。[用量门禁](../src/agenttest/task_store.py#L232) · [两批历史审计](deepagents-hitl-reliability-p3b27.md) · [只读账本对账](../.eduwork/p3b28-diagnosis-f3733c1/replay.json)

## 离线验证

- 冻结编码器回放 10 轮的 17 次 Focus、52 次有原始 Context 的负向 Design。请求字节数与摘要逐条匹配账本。第二批 v2–v4 的六份 Focus 原始响应复现历史拒绝，完整缺项清单未截断。
- `MockTransport` 四个用例通过。内层 asyncio 截止到期记录 `DEADLINE`，响应头之前的 ReadTimeout 记录 `READ`，响应头后读到 16,384 B 再中断时保留 HTTP 200、mock 请求 ID 和已读字节数。父协程先到期时 planner 保留一次尝试和 UNKNOWN，分类为空。最后一项只证明该日志形态可能出现，首批 v5 的原因仍未确定。CONNECT、READ、WRITE、POOL 的异常映射逐项核对通过。[Mock 结果](../.eduwork/p3b28-diagnosis-f3733c1/mock-timeout.txt)
- 定向 pytest 覆盖 `test_model_timeout_budget.py`、`test_p3b26_focus_completeness.py`、`test_product_focus.py` 和 `test_p3b25_model_input.py`，结果 **66 passed、1 既有 xfailed**（21.45 秒）。Ruff、`git diff --check` 通过。测试与回放均为离线，本轮真实模型调用、业务 HTTP 均为 0。

## 后续候选与优先级

1. **先补阶段遥测。** 在现有调用 ID 下记录单调时间戳、发送阶段、是否收到响应头、首次可见正文、内层或父任务取消来源、请求/Schema 字节数和安全过滤后的服务端 ID。保持 UNKNOWN 结算和写入防重放。用 MockTransport 覆盖先到的外层截止、先到的内层截止及收到部分正文后的 READ。对于已有两条重复超时，可按日期和开始时间向服务方查日志，现存调用没有 provider ID 可直接关联。
2. **离线改进 Focus 局部修复表达。** 携带经过现有单项规则验证的有界保留三元组 `operation_id/check_ref/source_ref`，以及必须修改的路径，要求输出完整提案且保留当前有效范围。用 v2 的全缺和尾项遗漏、v3 的多余来源子句、v4 的 23→1 作为反例。只投影安全引用和应用生成约束，避免传回原始拒绝正文、自由文本或凭据。模型仍需显式提交所有项并重新验证。
3. **先保留模型和时限。** 若同一环境开始记录到可定位的连接异常或服务方能提供请求关联证据，再决定修复哪个传输边界。若网关不提供足够证据，可后续单独批准用等价冻结输入做不同模型或网关的对照。现有数据不支持增加 60 秒上限、重试次数或组运行上限。

**暂不建议第三批同条件真实验收。** 两批已复现两种可区分的失败模式。等客户端阶段遥测可验证且 Focus 修复反馈离线通过，再按独立授权决定是否启用真实验证。P3-B2.7 两批各保留 **1/5、NO-GO**。

## 2026-11-24 · P0 Focus 保留映射反馈

对已确认的整项目正负向发布范围，独立复核被拒提案中的负向映射。只保留当前已批准且就绪的策略，核对所属操作、可用身份、未决权限、作用域来源和唯一性，再执行既有操作 bundle 解码、来源解析、RequestedCheck 类型检查及负向映射校验。只有引用已确认范围子句且默认值约束通过的条目进入 `keep_requested_checks`。显式值、受限发布或关联现有资源而无法独立确认的条目继续走原校验；提案里的值和自由文本不会复制到保留清单。

已有修复请求内，`required_scope_mode=selected` 来自可信整项目范围，`keep_requested_checks` 只含 operation/check/source 三元组。原有 A1 缺项和编辑要求仍然送达。模式有误时不会复制旧模式。模型须重新显式提交完整映射并通过原有校验，仍只有一次修复机会。

| 第二批归档 | 修复请求要求保留 | 修复请求要求补齐 | 历史第二份重放 | 独立 Mock 合法替换第二份 |
| --- | ---: | ---: | --- | --- |
| v2 | 0 | 24 | 缺 1，拒绝 | 24 项通过 |
| v3 | 24 | 0 | 切为 `all_ready` 后缺 24，拒绝 | 24 项通过 |
| v4 | 23 | 1（`createParcel / check_3 / source_3`） | 缺 23，拒绝 | 24 项通过 |

三轮首发请求的 SHA-256 与归档一致。新的第二次请求已用 MockTransport 捕获实际编码，保留与缺项集合互不重叠，A1 的必删项也仍在。独立 Mock 对照复用历史已通过的提案，检验原校验可以接受完整的第二份输出；该对照不衡量真实模型是否会按反馈修复。[逐项三元组与新请求摘要](../.eduwork/p3b28-diagnosis-f3733c1/focus-repair-validation.jsonl)

P0 实现位于 [product_focus.py](../src/agenttest/product_focus.py)，沿用 `focus_repair_payload` 的既有投影。定向覆盖 Focus、A1、显式值、受限范围和脱敏，**85 passed、1 个既有 xfailed**。四个新增反例覆盖重复映射、错误模式、无法解析的来源及长度冲突。[Focus 测试](../tests/test_p3b26_focus_completeness.py)

## 2026-11-24 · P1 模型阶段遥测

Planner 和 Agent 的 `ModelCallStats.telemetry` 记录闭集字段。时间单位为本地调用起点后的单调毫秒，缺失阶段不补写。JSON 指提供方响应信封。首次正文阶段为 HTTPX 向应用交付首个非空解码块，受原有缓冲影响；它无法定位网络首次到字节、网关接收或开始生成的时间。

| 字段 | 观测依据 |
| --- | --- |
| `request_prepared` / `dispatch_started` | 请求编码成功 / 本地进入 HTTPX 流式请求入口 |
| `headers_received` / `first_body_chunk` / `body_complete` | 已返回响应对象 / 应用收到首个非空块 / 流迭代完成 |
| `json_parsed` / `usage_validated` / `call_ended` | 响应信封解析成功 / 用量字段类型及加和有效 / 调用进入结算 |
| `interruption_source` | 实际到期的请求、Focus、Design 或任务时限，明确的 HTTPX 阶段异常，显式用户取消或服务关闭。候选来源不唯一或未传到调用时记 UNKNOWN |
| `usage_evidence` | 完整校验、响应未读完、解析失败、usage 缺失、类型非法、总数不一致，或证据不足。用量格式有效与预算和账本是否接受仍分别判断 |

既有 `timeout_kind`、错误分类、60 秒配置、`timeout + 1` 调用外壳、任务时限及取消传播保持原语义。SQLite 继续使用现有 stats JSON，Agent Trace 和任务报告使用同一调用结算记录，终止时报告可附带当前任务账本的最后一条调用。旧账本字段缺失时仍可读。进程硬退出留下 PENDING 时维持原门禁，无法凭缺失阶段判定已派发、请求结果或具体中止来源。UNKNOWN 用量仍为空，恢复校验不放行未知写入重放。

P1 修改 [model_timing.py](../src/agenttest/model_timing.py)、[report.py](../src/agenttest/report.py)、[llm_planner.py](../src/agenttest/llm_planner.py)、[chat_model.py](../src/agenttest/deep_harness/chat_model.py) 与 Focus/Design/任务时限和 TaskStore 的现有接入点。定向 **48 passed**，其中新增 26 项覆盖完整响应、无响应头、读取中断、部分正文、内外层时限、用户取消、服务关闭、usage 异常、SQLite 对账和旧记录。Planner 实际请求体与编码结果逐字节一致，Agent 请求无遥测字段。[遥测测试](../tests/test_p3b28_model_timing.py)

## 开发阶段结论

Durable Checkpoint、引用修复、分组准入和清理回读相关回归 **45 passed**。在 gateway 暂停用例中，任务报告与 SQLite 的调用 ID 和遥测一致，带遥测的 Trace 经 Durable Checkpoint 重新载入后与原数据一致。两条历史 `negative_batch_5` 请求重新编码的字节数与 SHA-256 均匹配原归档。三组定向共 **178 passed、1 个既有 xfailed**，Ruff 和 `git diff --check` 通过。两批 517 个归档文件的摘要一致。

目前无需 Patch-based Repair。先以新反馈观察模型是否仍在修正单项时删除合法映射。若后续授权的验收再次复现该现象，再评估只允许局部编辑的方案。遥测已补齐客户端可见边界，服务端阶段仍需服务方证据。原两批评分各 **1/5、NO-GO**，历史日志和账本未改写。本阶段真实模型和业务靶场调用均为零。

## 2026-11-24 · 完整离线冻结回归

**离线 FAIL，未达到提交与 CI 核查条件。** 本次使用项目 `.venv` 的 Python 3.11.15、pytest 9.1.1，在 `master` 的候选工作区通过 `scripts/run_frozen_regression.py` 执行一次默认完整集。冻结基线 HEAD 与本地 `origin/master` 同为 `f3733c14c5da6b33cfc7a9a70812566f72dd7c85`。冻结前无并行测试或验收进程，Windows Git 恢复探针、Ruff、`git diff --check` 通过。运行保留原网络保护、测试收集和断言。正式模型与真实业务靶场调用均为零。[启动命令](../.eduwork/p3b28-freeze/run-20261124T041710Z/launch.json) · [运行环境](../.eduwork/p3b28-freeze/run-20261124T041710Z/environment-before.json)

| 指标 | 本轮实测 |
| --- | --- |
| pytest / 冻结入口退出码 | 1 / 1 |
| passed / failed / errors | 3,048 / 31 / 0 |
| skipped / xfailed | 15 / 1 |
| collected / reported | 3,095 / 3,095 |
| missing / extra / repeated / nonunique | 0 / 0 / 0 / 0 |
| 相对 P3-B2.7 | 新增 30 个 ID，移除 0 个；31 个旧用例由 passed 变为 failed |
| pytest 耗时 / 冻结入口耗时 | 2,329.45 秒（38 分 49.45 秒）/ 2,339.75 秒（38 分 59.75 秒） |

所有收集项均有唯一终态。P0 新增 4 项和 P1 新增 26 项全部实际运行并通过。冻结前用三轮原始 Focus 响应和 Context 回放，v2–v4 保留 0、24、23 项，补齐 24、0、1 项。历史第二份提案仍分别缺 1、24、23 项并被拒。保留三元组与缺项无重叠；整项目范围、必改字段、脱敏和一次修复上限通过定向证据及全量中的相关用例核对。[前置原始响应回放](../.eduwork/p3b28-freeze/run-20261124T041710Z/focus-archive-replay.log) · [逐 ID 终态](../.eduwork/p3b28-freeze/run-20261124T041710Z/regression/tests.json)

| 兼容性核查 | 结果 |
| --- | --- |
| Focus、聚合诊断和 JSON 修复相关 | 151 passed、1 个既有 xfailed |
| Design 与引用相关 | 216 passed、15 failed；15 项集中于引用诊断的报告读回比较 |
| 模型遥测、用量与预算相关 | 238 passed、3 failed；相应失败为组合规划评测读回校验 |
| SQLite、Checkpoint、取消及恢复相关 | 117 passed |
| HITL、清理、脱敏及写入防重放相关 | 分别 49、88、98、81 passed |
| P3-B2.7 分组准入与剩余义务修复 | 上一版新增的 17 项全部 passed |
| Windows Git 环境恢复 | 前置探针 passed，全量 `test_nl_usage.py` 5/5，原 46 个失败位置 46/46 passed |

这些主题按实际测试 ID 分组，部分测试涉及多个主题。P1 用例覆盖阶段顺序和未观测阶段、原 HTTPX 分类、内外层时限、部分响应、usage 三种无效情形及 UNKNOWN 空用量。Trace、SQLite、任务报告、Durable Checkpoint 以及旧账本、取消和恢复门禁的新增相关断言全部通过。全量仍发现下述对象比较回归。[完整审计与所有失败 ID](../.eduwork/p3b28-freeze/run-20261124T041710Z/audit-summary.json)

### 31 个失败的共同原因

P1 的 `ModelCallTelemetry._started` 是用于相对计时的 Pydantic 私有属性。报告落盘时不序列化该属性，读回时默认工厂会重新取单调时钟。Pydantic 对象比较包含私有属性，导致报告序列化数据与遥测阶段一致时，原对象和读回对象仍可能不相等。只读最小探针复现了这种差异。[私有时钟定义](../src/agenttest/model_timing.py#L34) · [读回探针](../.eduwork/p3b28-freeze/run-20261124T041710Z/report_roundtrip_probe.json)

| 失败位置 | 数量 | 实际失败条件 |
| --- | ---: | --- |
| `test_batch_workflow.py` | 3 | Mock 模型路径的报告对象与读回对象不相等 |
| `test_petclinic_chain.py` | 5 | 执行报告与读回对象不相等 |
| `test_reference_diagnostics.py` | 15 | 预期业务终态相等，报告对象比较失败 |
| `test_composed_planning.py` | 8 | 冷复算判定 `report.model_call != row.model_stats`，报 `Planning report/rescore mismatch` |

组合规划的 8 个失败目录已额外读回 10 对保存的调用记录。每对模式化 JSON 与报告摘要均一致，私有时钟不同使对象比较失败。前一轮开发定向测试校验了序列化字段和账本证据，未覆盖完整的报告对象往返比较。提交前需要恢复报告比较兼容性，并让全量回归达到既定放行标准。本轮保留失败，未修改实现、测试或再次运行。[冷复算对账](../.eduwork/p3b28-freeze/run-20261124T041710Z/composed-report-evidence.json) · [原始 pytest 日志](../.eduwork/p3b28-freeze/run-20261124T041710Z/regression/pytest.log)

### 冻结摘要与耗时

冻结前后完整 checkout SHA-256 均为 `4f958fa14bd7e8e7eba8c29a6f8e936b13264f0858b1d7b356dc5bef5bf6f16f`。纳入 1,721 个 Git 可见文件，运行中零新增、删除或字节变化。三个未跟踪候选文件均纳入摘要，分别是本报告、`src/agenttest/model_timing.py`、`tests/test_p3b28_model_timing.py`。凭据、实际 `.env`、SQLite、模型原文和运行证据均未纳入。P3-B2.7 两批 517 个验收文件和更早受保护的冻结及验收档案无漂移，两批正式结果仍各为 **1/5、NO-GO**。[冻结前清单](../.eduwork/p3b28-freeze/run-20261124T041710Z/identity-before.json) · [冻结后清单](../.eduwork/p3b28-freeze/run-20261124T041710Z/identity-after.json)

| 文件类别 | 数量 | SHA-256 |
| --- | ---: | --- |
| 源码 | 185 | `a0e705022ac4a269948781fe9df4ea3b8bb66c8a1efb08b2df64dfd7da9c9222` |
| 测试 | 226 | `6617ec2cd55997eb8dac9ea06acf35844579915221c9714457cc532a3ac75ec0` |
| 配置、脚本与 Web | 443 | `ac6689de55132147d36f703b4c4155d9c9a80011c28f174e6ace802034db6adc` |
| 文档 | 826 | `aeaad6e8f139ce5982efe758d77fc74fa302f6b575b8af8d4137d219b38a4729` |
| 其他 | 41 | `94371130bc554e2e3031b54b970a01963d08027d74987d61e5b7b068cfcb3623` |

| 最慢测试 | call 耗时 |
| --- | ---: |
| `test_full_denominator_is_cold_regraded_and_resealed_wrong_results_fail` | 100.87 秒 |
| `test_fresh_root_controls_and_simulated_trials_are_frozen_and_cold_verified` | 65.35 秒 |
| `test_fresh_diagnostic_tasks_reuse_runner_and_cold_regrading` | 60.25 秒 |
| `test_new_tasks_archive_rescores_and_rejects_resealed_regrading` | 52.90 秒 |
| `test_new_reference_tasks_use_shared_runner_and_cold_regrading` | 51.54 秒 |

审计完成后只追加本报告和进度。历史 FAIL、NO-GO、评分及证据保留原样。

## 2026-11-24 · 遥测报告往返兼容性修复

首次完整冻结仍为 **FAIL**。本次以 `f3733c14c5da6b33cfc7a9a70812566f72dd7c85` 为 HEAD，逐文件比对失败候选 `4f958fa14bd7e8e7eba8c29a6f8e936b13264f0858b1d7b356dc5bef5bf6f16f`，修复前只存在该次冻结报告和进度两个追加变更。首次 pytest 日志和逐项终态列出了同一组 31 个失败 ID。[原始审计](../.eduwork/p3b28-freeze/run-20261124T041710Z/audit-summary.json)

### 根因与修复

失败候选中的 `model_timing.py:34` 将 `_started` 声明为 `ModelCallTelemetry` 的 `PrivateAttr(default_factory=time.monotonic)`。Pydantic 2.14.0 反序列化时会执行该默认工厂，对象相等性还会比较私有属性。修改前用一个带遥测的 `RunReport` 验证，JSON、持久化字段和摘要均相等，私有时钟和报告对象不相等。前次日志中 23 项直接失败于报告对象比较，另外 8 项在组合规划冷复算的 `report.model_call != row.model_stats` 处失败。[修改前探针](../.eduwork/p3b28-roundtrip-repair/roundtrip-before.json) · [原冷复算证据](../.eduwork/p3b28-freeze/run-20261124T041710Z/composed-report-evidence.json)

Planner 和 Agent 在单次调用中持有各自的单调计时起点。标记阶段和结束时显式传入起点，只把算出的相对毫秒写入遥测。同一适配器的各次调用使用各自的统计对象，供既有回调、账本和报告使用。报告反序列化无需初始化运行时时钟，Pydantic 的默认对象比较继续判断所有持久化遥测字段。修复只涉及 `model_timing.py`、`report.py`、`llm_planner.py` 和 `deep_harness/chat_model.py`。原异常传播、用量分类、取消、预算和请求配置保持原语义。[计时与持久化字段](../src/agenttest/model_timing.py#L26) · [Planner 调用](../src/agenttest/llm_planner.py#L473) · [Agent 调用](../src/agenttest/deep_harness/chat_model.py#L439)

修复后读回复现探针保存的同一份 JSON，报告对象、JSON 和摘要一致。首次回归中 8 个组合规划失败目录的 10 对报告与调用记录重新读回后对象相等，保存的历史摘要均保持匹配。核验只读，首次回归判定仍为 FAIL。[修复后探针](../.eduwork/p3b28-roundtrip-repair/roundtrip-after.json) · [首次冻结报告读回核验](../.eduwork/p3b28-roundtrip-repair/composed-report-after.json)

### 定向验证

原 31 个精确测试 ID 全部执行并通过，pytest 退出 0。原有业务断言和范围保留。两个批次合计收集和报告 **245 个不同 ID，235 passed、10 个既有 skipped、0 failed**。没有新增 skip 或 xfail，逐项检查无漏项和重复。[31 个原始 ID 的逐项终态](../.eduwork/p3b28-roundtrip-repair/original/summary.json) · [兼容性批次定向日志](../.eduwork/p3b28-roundtrip-repair/compatibility/pytest.log)

| 首次冻结失败位置 | 本次精确复验 |
| --- | ---: |
| `test_batch_workflow.py` | 3/3 |
| `test_composed_planning.py` | 8/8 |
| `test_petclinic_chain.py` | 5/5 |
| `test_reference_diagnostics.py` | 15/15 |

| 关键兼容性检查 | 实测结果 |
| --- | --- |
| P0 新增、P1 新增、P3-B2.7 分组准入 | 4/4、26/26、17/17 |
| 本次新增报告往返回归 | 8/8，覆盖带遥测和旧版报告读回、不同起点相同数据、持久化字段不等、同一 Planner 的重叠调用和组合规划冷复算 |
| 组合规划、报告验证、报告发布和文件 | 19 passed、8 passed、25 passed / 7 既有 skipped、4 passed / 3 既有 skipped |
| SQLite/Checkpoint、取消、恢复 | 9 passed、5 passed、20 passed |
| 超时、执行预算、用量对账与豁免作用域 | 9、13、10、3 passed |
| 脱敏、Windows Git 环境恢复所在模块 | 14、5 passed |

阶段记录保持单调顺序，只标记已观察的阶段。P1 和相关恢复用例核对了中断与用量证据、UNKNOWN 空值保留、Trace / SQLite / Checkpoint 与报告一致，以及取消和防重放门禁。本次新增并发用例使用同一 Planner 的两个 Mock 请求独立计时和结算。冷复算通过真实报告读回与摘要校验。[新增往返测试](../tests/test_p3b28_report_roundtrip.py) · [逐文件审计](../.eduwork/p3b28-roundtrip-repair/audit-summary.json)

Ruff、`git diff --check` 通过。首次冻结证据目录的 19 个文件、P3-B2.7 两批 517 个验收文件及更早受保护归档摘要一致。**已具备再次离线冻结的条件**，本次定向结果不改变首次全量 FAIL。P3-B2.7 两批仍各为 **1/5、NO-GO**。本次真实模型调用、真实业务靶场访问均为零。

## 2026-11-24 · 第二次完整离线冻结回归

**离线 FAIL，未达到提交与 CI 放行条件。** 在 `master` 以 HEAD `f3733c14c5da6b33cfc7a9a70812566f72dd7c85` 的修复候选运行一次默认完整集。项目 `.venv` 的 Python 3.11.15、pytest 9.1.1 执行原 `scripts/run_frozen_regression.py`。候选相对首次冻结只变化 8 个已审计路径，涵盖 4 个实现文件、2 个测试文件、2 份文档。Ruff、`git diff --check` 和 Windows Git 环境恢复探针通过，无并行回归。原测试收集、网络保护、超时和预算配置一致。真实模型和真实业务靶场调用均为 0。[启动命令](../.eduwork/p3b28-freeze/run-20261124T052056Z/launch.json) · [运行环境](../.eduwork/p3b28-freeze/run-20261124T052056Z/environment-before.json)

| 指标 | 首次冻结 | 第二次冻结 |
| --- | ---: | ---: |
| pytest 退出码 | 1 | 1 |
| 收集 / 报告 | 3095 / 3095 | 3103 / 3103 |
| passed / failed / errors | 3048 / 31 / 0 | 3086 / 1 / 0 |
| skipped / xfailed | 15 / 1 | 15 / 1 |
| missing / extra / repeated / nonunique | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 |
| 文件数 / 运行期漂移 | 1721 / 0 | 1722 / 0 |
| pytest 耗时 | 2329.45 秒 | 2454.23 秒 |

第二次保留全部 3095 个旧 ID，新增恰为往返兼容性修复的 8 个 ID。原 31 个失败 ID 均转为通过，另有 1 个旧 ID 从通过转为失败。3103 个 ID 均具有唯一终态。冻结入口总耗时 2464.171 秒，pytest 实际耗时 40 分 54.23 秒。[原始日志](../.eduwork/p3b28-freeze/run-20261124T052056Z/regression/pytest.log) · [逐项终态](../.eduwork/p3b28-freeze/run-20261124T052056Z/regression/tests.json) · [完整审计](../.eduwork/p3b28-freeze/run-20261124T052056Z/audit-summary.json)

| 审计对象 | 第二次冻结 |
| --- | --- |
| 首次失败的 31 个 ID | 31/31 passed |
| P0 / P1 / P3-B2.7 分组准入 | 4/4 · 26/26 · 17/17 passed |
| 报告往返、计时隔离和冷复算新增用例 | 8/8 passed |
| Windows Git 环境恢复及原失败位置 | 环境探针 passed，46/46 passed |
| Focus / Design 与引用 | 151 passed、1 个既有 xfailed / 231 passed |
| 模型遥测、用量与预算 | 245 passed、1 failed |
| SQLite、Checkpoint、取消及恢复 | 117 passed |
| HITL、清理、脱敏及未知写入防重放 | 分别 49、88、98、81 passed |

表中主题按测试 ID 归类并可重叠。冻结前的历史 Focus 回放确认 v2–v4 分别保留 0、24、23 项，补齐 24、0、1 项。保留与缺项集合无重叠，原第二份提案仍因缺 1、24、23 项被拒，修复请求仍限制为一次。历史报告 JSON 复读相等，计时起点未进入报告持久化字段。报告相等性、组合规划冷复算、不同观测值比较、并发计时隔离、Trace、SQLite、Checkpoint 与终止报告对账的相关用例通过。[Focus 回放](../.eduwork/p3b28-freeze/run-20261124T052056Z/focus-archive-replay.log) · [历史摘要对账](../.eduwork/p3b28-roundtrip-repair/composed-report-after.json)

### 唯一失败的链路

`test_unknown_model_usage_stops_remaining_positions_without_retry` 的本地模拟用 `unavailable(self, public)` 替换内部 `_generate`。往返修复后的调用传入 `context, stats, started=started`，两者的参数不兼容，模拟函数体中的请求准备和 503 注入没有执行。Planner 的异常处理留下 `MODEL_INVALID_RESPONSE / MODEL_ENVELOPE_INVALID`。本次保存的 `GA1001` 第 1 轮报告显示 `calls=0`、`usage_state=NOT_SENT`、HTTP 状态与请求摘要均为空。冷复算校验冻结请求摘要时抛出 `Unfrozen model request`。因此本用例没有走到它原本设计的 UNKNOWN 用量停机验证路径。发布前仍需解决内部调用与该用例的兼容问题。[模拟函数](../tests/test_generic_arguments.py#L185) · [内部调用](../src/agenttest/llm_planner.py#L480) · [本次失败记录对账](../.eduwork/p3b28-freeze/run-20261124T052056Z/failure-diagnosis.json)

### 冻结身份与最慢用例

第二次前后 checkout SHA-256 均为 `468dbea03bf3ec54aec6f9b80f64e23cbfa15c08709d8d3a8530692fc7e5978c`。1722 个 Git 可见文件全部纳入清单，源码、测试、配置与文档在回归期间零漂移，真实凭据、SQLite、模型原文和运行日志未入候选。首次 FAIL 的 19 个文件、定向修复的 21 个文件、P3-B2.7 两批的 517 个验收文件及更早受保护归档摘要一致。P3-B2.7 两批仍各为 **1/5、NO-GO**。[冻结前清单](../.eduwork/p3b28-freeze/run-20261124T052056Z/identity-before.json) · [冻结后清单](../.eduwork/p3b28-freeze/run-20261124T052056Z/identity-after.json)

| 最慢用例 | call 耗时 |
| --- | ---: |
| PocketBase 全分母冷复算 | 97.85 秒 |
| 新引用诊断任务冷复算 | 66.97 秒 |
| 根响应规划冷复算 | 65.08 秒 |
| 新通用引用任务冷复算 | 62.71 秒 |
| 新参数任务档案复算 | 62.13 秒 |

只追加本报告和进度。首次与第二次全量结果分别保留为 FAIL。

## 2026-11-24 · 测试替身接口兼容性修复

第二次冻结的唯一失败来自 `test_unknown_model_usage_stops_remaining_positions_without_retry`。旧替身 `unavailable(self, public)` 无法接收 `OpenAICompatiblePlanner.generate` 传入的 `stats` 和关键字参数 `started`。Python 在进入替身函数体前拒绝调用，导致请求编码和 503 注入均未执行。冻结报告留下 `calls=0`、`NOT_SENT`、空请求摘要和 `MODEL_ENVELOPE_INVALID`，冷复算因请求摘要与冻结值不一致报 `Unfrozen model request`。[第二次冻结失败证据](../.eduwork/p3b28-freeze/run-20261124T052056Z/failure-diagnosis.json) · [冷复算校验](../src/agenttest/argument_slot_check.py#L239)

仅调整测试替身为 `unavailable(self, public: dict, stats: ModelCallStats, *, started: float) -> str`。它仍用 `prepare_request` 生成请求和摘要，使用传入的单次调用统计记录模拟 503，并用 `started` 标记请求准备阶段。替身仍抛出 `TEST_INFRA_ERROR / MODEL_SERVICE_ERROR`，用量状态仍由生产端结算。生产接口、冷复算器和原安全断言沿用第二次冻结候选。[最终替身](../tests/test_generic_arguments.py#L185) · [生产调用契约](../src/agenttest/llm_planner.py#L473)

### 同类接口审查

在 Git 可见的 Python 文件中搜索定义、调用、覆盖和属性字符串，并对本阶段的归档回放与诊断脚本补充搜索。活跃代码中直接替换 Planner 私有 `_generate` 的入口只有本次用例。未发现同名的 Mock、AsyncMock 或 `patch.object` 替换需要调整。

| 文件和位置 | 调用方式与核对结论 | 修改 |
| --- | --- | --- |
| `llm_planner.py:473,642`；`test_generic_arguments.py:185,191` | `generate` 传入上下文、单次 `stats` 和关键字 `started`；旧测试替身缺后两项 | 仅替身 |
| `feedback.py:50`、`goal_spec.py:46`、`native_tools.py:11`、`product_design.py:304`、`product_focus.py:427`、`product_probe.py:22`、`product_supervisor.py:129` | Planner 子类定制编码、解码或消息取值，均继承当前私有生成接口 | 无 |
| `eval_v2/runner.py:117`、`composed_planning.py:63`、`product_tasks.py:414` | Context、冻结与账本包装器通过公开 `generate(context)` 委托 | 无 |
| `deep_harness/chat_model.py:325,394,417,438,462,488` | Agent 的 LangChain 同步 `_generate` 转调 `_agenerate`；后者向传输、用量、消息与结算接口传递单次统计和计时起点 | 无 |
| `deep_harness/loop.py:195,207`、`experiments/deepagents_p1/ab_runner.py:211–213`、`experiments/deepagents_p1/audit_harness.py:61,71` | 脚本回放覆盖 LangChain 的 `_agenerate`；A/B 计数包装器把该公开框架形状转发给基类 | 无 |
| `llm_planner.py:67,516,667,693`、`chat_model.py:397,468`、`model_timing.py:35,39,76`、`report.py:148` | 错误体诊断、`mark`、用量解析和遥测收尾接收 `started`；deadline 的 `telemetry` 作为可选关键字使用 | 无 |
| `test_llm_planner.py`、`test_p3b25_model_input.py:82`、`test_p3b28_model_timing.py:83,94,139–172`、`test_p3b28_report_roundtrip.py:45–49,78–100` | HTTP 测试替身在 transport 层或公开入口；任务 deadline 替身接收现有的两个实参，计时探针为 `mark` 显式传入起点 | 无 |
| `.eduwork/p3b28-diagnosis-f3733c1/replay_focus_repair.py:43,54,77`、`replay.py:165`、`mock_timeout.py:42` | 归档回放通过公开生成或请求编码入口 | 无 |
| `.eduwork/p3b28-freeze/run-20261124T041710Z/report_roundtrip_probe.py:10`、`.eduwork/p3b28-roundtrip-repair/probe_before.py:49` | 两份探针使用旧版 `mark` 重现各自冻结时的缺陷，作为历史证据保留 | 无 |

### 落盘证据与复验

按文件运行后的 `GA1001` 第 1 轮归档记录了 **1 次模拟调用、HTTP 503、`MODEL_SERVICE_ERROR`**。请求 SHA-256 为 `6880dc51e4a51b8123be0b057008e0130e15ec48d4feba0fd3396317a5b60e49`，与冻结请求一致。用量状态是 **UNKNOWN**，token 数是 **null**，证据为 `INCOMPLETE_RESPONSE`。后续 8 个位置全部为 `NOT_ATTEMPTED`，新增模型调用和业务请求均为 0。3 个固定控制产生 6 次模拟业务请求，真实模型调用为 0，冷复算验证全部 12 个位置且未新增调用。[最终落盘证据](../.eduwork/p3b28-test-double-repair/file/usage-evidence.json)

| 验证范围 | 结果 |
| --- | ---: |
| 原失败 ID 单独运行 | 1/1 passed |
| 定向回归不同 ID | 201/201 passed |
| 首次冻结失败用例 | 31/31 passed |
| P0 / P1 | 4/4 · 26/26 passed |
| 往返兼容 / P3-B2.7 | 8/8 · 17/17 passed |
| 最终补丁按 `test_generic_arguments.py` 文件入口运行 | 20/20 passed |

定向批次覆盖模型调用摘要、用量门禁、账本、SQLite、恢复和冷复算。临时落盘断言移至产物审计，最终提交候选的测试文件只保留替身调整及原断言，随后整文件复验。各批次范围存在重叠，按 201 个不同 ID 计。Ruff 和 `git diff --check` 通过，没有删除用例、增加 skip 或 xfail。[定向逐项终态](../.eduwork/p3b28-test-double-repair/targeted/tests.json) · [最终文件复验](../.eduwork/p3b28-test-double-repair/file/summary.json)

**第三次完整冻结的前置条件已满足，尚无第三次完整回归结果。** 两次历史完整回归仍为 FAIL，原失败归档和受保护证据的文件摘要一致。P3-B2.7 两批真实验收各为 **1/5、NO-GO**。

## 2026-11-24 · 第三次完整离线冻结回归

**离线 PASS，满足提交及启动 CI 验证的离线前置条件。** 在 `master`、HEAD `f3733c14c5da6b33cfc7a9a70812566f72dd7c85` 使用项目 `.venv` 的 Python 3.11.15 和 pytest 9.1.1，通过原 `scripts/run_frozen_regression.py` 执行一次默认完整集。启动前确认无并行回归或验收进程。相对第二次冻结仅测试替身和两份报告文档变化，生产代码、配置和原测试断言经核对保持一致。Ruff、`git diff --check`、Windows Git 环境恢复探针和历史 Focus 回放通过。回归沿用已有网络保护，真实模型及真实业务靶场调用均为 0。[启动命令](../.eduwork/p3b28-freeze/run-20261124T064454Z/launch.json) · [运行环境](../.eduwork/p3b28-freeze/run-20261124T064454Z/environment-before.json) · [Focus 回放](../.eduwork/p3b28-freeze/run-20261124T064454Z/focus-archive-replay.log)

| 指标 | 第一次冻结 | 第二次冻结 | 第三次冻结 |
| --- | ---: | ---: | ---: |
| 结论 | FAIL | FAIL | PASS |
| pytest 退出码 | 1 | 1 | 0 |
| 收集 / 报告 | 3095 / 3095 | 3103 / 3103 | 3103 / 3103 |
| passed / failed / errors | 3048 / 31 / 0 | 3086 / 1 / 0 | 3087 / 0 / 0 |
| skipped / xfailed | 15 / 1 | 15 / 1 | 15 / 1 |
| missing / extra / repeated / nonunique | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 |
| 文件数 / 运行期漂移 | 1721 / 0 | 1722 / 0 | 1722 / 0 |
| pytest 耗时 | 2329.45 秒 | 2454.23 秒 | 2569.59 秒 |

第三次保留第二次冻结的全部 3103 个 ID，无增删。唯一终态变化是 UNKNOWN 停机用例由 failed 转为 passed。保留的一项警告来自 pytest 对带构造函数的 `TestDesign` 类的收集提示，逐 ID 对账无遗漏。pytest 实际耗时 **42 分 49.59 秒**，冻结入口总耗时 **43 分 0.41 秒**。[原始日志](../.eduwork/p3b28-freeze/run-20261124T064454Z/regression/pytest.log) · [逐项终态](../.eduwork/p3b28-freeze/run-20261124T064454Z/regression/tests.json) · [完整审计](../.eduwork/p3b28-freeze/run-20261124T064454Z/audit-summary.json)

| 审计对象 | 实际结果 |
| --- | --- |
| 前两次失败的测试 ID | 32/32 passed |
| P0 / P1 新增用例 | 4/4 · 26/26 passed |
| 往返兼容性新增用例 | 8/8 passed |
| P3-B2.7 分组准入 | 17/17 passed |
| 历史 Windows Git 失败位置 | 46/46 passed，恢复探针通过 |
| Focus / Design 与引用 | 151 passed、1 个既有 xfailed / 231 passed |
| 模型遥测、用量及预算 | 246 passed |
| SQLite、Checkpoint、取消及恢复 | 117 passed |
| HITL、清理、输入脱敏及写入防重放 | 分别 49、88、98、81 passed |

主题分组按测试 ID 归类，部分测试涉及多个主题。报告往返、并发计时隔离和组合规划冷复算均通过。原安全、预算、超时、评分及恢复规则沿用第二次冻结的实现和配置。[审计分组与终态](../.eduwork/p3b28-freeze/run-20261124T064454Z/audit-summary.json)

### UNKNOWN 停机的本轮证据

`test_unknown_model_usage_stops_remaining_positions_without_retry` 在本轮实际落盘的 `GA1001` 第 1 轮报告中记录 **1 次模拟 503**，结论为 `TEST_INFRA_ERROR / MODEL_SERVICE_ERROR`。请求摘要与冻结请求及归档请求重新计算的 SHA-256 均一致。用量状态为 **UNKNOWN**，token 数为空。总模型调用为 1，后续 8 个位置全部 `NOT_ATTEMPTED`，后续模型调用和业务请求均为 0。3 个固定控制产生 6 次模拟业务请求。12 个归档位置全部通过冷复算，冷复算新增模型调用为 0。[本轮落盘证据](../.eduwork/p3b28-freeze/run-20261124T064454Z/unknown-usage-evidence.json)

### 冻结身份及最慢用例

冻结前后 checkout SHA-256 均为 `8eb6fba84a9cf87a764a9cc9dbad6d54c0ec527e24f2b831185d2e25d53ae060`。1722 个 Git 可见文件全部纳入清单，源码、测试、配置与文档在回归期间零漂移。实际凭据、SQLite、模型原文及运行证据均未纳入候选。前两次完整冻结、往返修复和替身修复证据及受保护历史档案的文件摘要一致。P3-B2.7 两批真实验收仍各为 **1/5、NO-GO**。[冻结前清单](../.eduwork/p3b28-freeze/run-20261124T064454Z/identity-before.json) · [冻结后清单](../.eduwork/p3b28-freeze/run-20261124T064454Z/identity-after.json)

| 最慢用例 | call 耗时 |
| --- | ---: |
| PocketBase 全分母冷复算 | 101.49 秒 |
| 新引用诊断任务冷复算 | 66.66 秒 |
| 根响应规划冷复算 | 66.48 秒 |
| 新通用引用任务冷复算 | 66.13 秒 |
| 新参数任务档案复算 | 65.42 秒 |

仅追加本报告和进度。首次与第二次结果保留为 FAIL，第三次独立记录为 PASS。本阶段完成离线放行，提交和 CI 验证尚未执行。
