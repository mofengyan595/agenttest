# L2 自然语言独立测试 Agent 开发记录（2026-11-10）

## 结论先行

在同一条模糊请求“帮我测一下条目管理功能，看看增删改查有没有问题。”上，Agent
最终独立完成了范围选择、计划构造和真实执行：

- 生产 Planning Engine 实际走 **`NORMALIZED_IR_COMPILER_V1`**；
- 6 个操作实例、0 个歧义、7 条依赖边、6/6 实例映射；
- 6 个业务 HTTP 请求，无重复、无未计划请求；
- 1 个 case `PASS`，1 个 case `ASSERTION_FAILED`（可用性接口实际 503，预期 200）；
- 任务最终为 `FINISHED`，隔离靶场记录数回到 0；
- 没有要求用户提供 HTTP 方法、状态码、执行顺序、字段样例或动态 ID 绑定。

但这不是“模型一次就稳定成功”：7 次有模型调用的真实运行中，只有 1 次达到
规范化 IR 的完整成功；真实模型调用累计 **12 次 / 134,590 tokens**，正好用完全部
12 次调用上限。L1 显式任务没有重新消耗真实模型位置，L3 上线前请求没有真实运行。

## 边界与预算

- 只在 loopback、自建、可丢弃靶场上执行；没有生产、真实业务或外部业务写入。
- 每次新靶场、新 workspace、新资源命名空间；失败不自动重放。
- 每任务：最多 2 次模型调用、12 个业务 HTTP、150 秒、单次模型请求 45 秒；
  单次实际输入必须 ≤20,000 tokens，输出 ≤4,096 tokens。
- 本轮真实调用总计 12 次；业务 HTTP 总计 12 个；成功任务靶场结束时
  `record_count=0`。
- 历史 M2/F1 冻结档案、rubric、验收结果和消费状态未修改、未重跑。

## 真实运行矩阵

| Attempt | 结果 | Focus | Planning Engine | 实例/歧义 | 模型调用 | Tokens | 业务 HTTP | Case 结果 |
|---|---|---|---|---:|---:|---:|---:|---|
| 1b | 预检，未执行 | — | `NOT_EXECUTED` | — | 0 | 0 | 0 | — |
| 2 | FINISHED | SELECTED | `LEGACY` | 0/0 | 2 | 21,608 | 6 | 1 FAIL / 1 PASS |
| 3 | NEEDS_REVIEW | SELECTED | `NOT_EXECUTED` | 10/5 | 2 | 22,970 | 0 | — |
| 4 | NEEDS_REVIEW | SELECTED | `NORMALIZED_IR_COMPILER_V1` | 6/0 | 2 | 22,229 | 0 | — |
| 5 | NEEDS_REVIEW | SELECTED | `NOT_EXECUTED` | 0/0 | 1 | 11,418 | 0 | — |
| 6 | NEEDS_REVIEW | REJECTED | `NOT_EXECUTED` | — | 1 | 12,434 | 0 | — |
| 7 | NEEDS_REVIEW | SELECTED | `NORMALIZED_IR_COMPILER_V1` | 6/0 | 2 | 22,252 | 0 | — |
| **8** | **FINISHED** | **SELECTED** | **`NORMALIZED_IR_COMPILER_V1`** | **6/0** | **2** | **21,679** | **6** | **1 FAIL / 1 PASS** |

`1b` 是字节级预检，不构成独立真实执行；`2/3/4` 是前三轮独立尝试；`5/6/7/8` 是针对
已定位缺陷的显式重试。没有自动重试或请求重放。

## 最终成功运行的证据

- Run：`runs/dev-agent-20261110/l2-attempt-8/run.json`
- Task：`3b6a68f5d80840bb97c4c685464f318b`
- Project：`web_3507abcb1b9f4cbc9f084b72`
- Target：`127.0.0.1:18301`，独立 `parcels` fixture
- Model：`ecnu-max`；2 次调用；input 20,382 / output 1,297 / total 21,679 tokens
- Focus 实际输入 11,342；Design 实际输入 9,040；均低于单次 20,000
- Planning：6 instances、0 ambiguities、mapping complete、7 dependency edges
- Compile：availability 1/1，CRUD 5/5，均 `proposed_nodes == constructed_nodes`
- HTTP 顺序与结果：
  1. `GET /availability` → 503（policy 预期 200）
  2. `POST /parcels` → 201
  3. `GET /parcels/{parcelId}` → 200
  4. `PATCH /parcels/{parcelId}` → 200
  5. `PUT /parcels/{parcelId}` → 200
  6. `DELETE /parcels/{parcelId}` → 204
- Case 1 `ASSERTION_FAILED`：`readAvailability` 的 `http_status` 期望 200，实际 503；
  `state` 期望 ready，实际 unavailable。失败解释保留“原因未确认”，没有把环境异常
  擅自判成业务缺陷。
- Case 2 `PASS`：create/read/change/replace/delete 全部状态和响应字段检查通过；
  资源生命周期 `CLEANED`，清理结果 `PASS`。
- 运行后 `GET /health` 返回 `record_count=0`。

## 覆盖、完成率与请求质量

最终成功任务：

- 操作覆盖：6/6（selected inventory 的 6 个操作全部执行）
- 场景覆盖：2/2 启动，2/2 完成，1/2 断言通过
- 规范化 IR 任务完成：1/1；实际业务 HTTP：6 个
- 计划请求：6 个；实际请求：6 个；重复/重放：0
- 未计划或未批准请求：0
- 有争议的范围噪声：1 个（`GET /availability` 是已批准操作，但不是“条目管理增删改查”
  的必要动作；Agent 把它解释为条目服务的可用性检查）。这不是越权请求，但说明
  操作相关性选择仍偏宽。
- 边界/负向：0 个实际执行；项目没有已批准的负向拒绝策略，Agent 没有自行发明
  400/404/409 断言，只在 suggestions 中提出待确认项。这是正确的不越权行为，但也是
  覆盖缺口。

跨全部真实运行：

- Task `FINISHED`：2/7（attempt 2 与 8）
- 规范化 IR 成功：1/7（attempt 8）
- 修复后首次真实重试成功：1/1（attempt 8；不代表稳定成功率）
- 业务 HTTP：12 个；模型调用：12 个；模型 tokens：134,590
  （input 125,762 / output 8,828）
- 模型耗时合计约 65,546 ms；全部运行 wall time 合计约 122.1 s（含靶场启动）

## 失败与修复日志

### 1. 多操作子句被误判为约束，Planning 不激活

`attempt-2` 已能执行，但 Planning Engine 为 `LEGACY`。根因是“帮我测一下条目增删改查”
被 Focus 映射为一条含多个不同 action 的 required clause，而 `planning_ir` 把所有
多 action 子句都当作无法确定实例身份的约束，导致 0 个 IR 实例。

修复：不同操作在同一子句中各产生一个 `REQUEST` 实例；只有同一操作重复出现才保留
`MISSING_INSTANCE_IDENTITY`。实例顺序和资源绑定仍由 policy lifecycle 推导，不按数组
位置猜。

### 2. 范围复述子句重复创建实例

`attempt-3` 生成 10 个实例、5 个歧义。第二个子句“看看增删改查有没有问题”只是对已
请求范围的复述，却被再次当成请求。

修复：若一个多操作子句的所有操作都已在前面请求过，且没有新的请求动词或显式状态，
则归为 `CONSTRAINT`，不重复创建实例。回归测试覆盖第一句 REQUEST、第二句 CONSTRAINT。

### 3. Design 在隐藏索引的多操作子句上被错误拒绝

`attempt-4` 已达到 6 实例/0 歧义，但 Design 被
`DESIGN_REQUIRED_ORDER_INSTANCE_INVALID` 拒绝。Design schema 不暴露
`requirement_action_index`，多操作来源只能提供 `requirement_source_id`；验证器却把
缺失索引默认成 0，导致 createParcel 的 step 被拿去和 actions[0]（readAvailability）
比较。

修复：当来源子句里目标 `operation_id` **只出现一次**时，由操作本身确定唯一索引；
显式索引与操作不匹配时，也只有唯一操作出现才纠正。重复操作仍拒绝，不靠数组位置猜。

### 4. Focus 把测试设计问题推回用户

`attempt-5` 只调用 1 次模型便返回 `needs_information`，要求用户提供名称、数量、标签、
期望等。这个请求本可由 schema 和已批准 policy 生成有效输入，属于把测试设计工作推回
用户。

修复：在 Focus prompt 中明确探索性测试无需样例值；不要要求用户设计测试、提供普通
有效字段值、精确预期状态码或执行顺序；只有真正的业务规则、身份、凭证或范围无法从
合同/policy/history 推导时才提问。

### 5. Focus 输出伪造用户输入，被验证器拒绝

`attempt-6` 进一步尝试选择范围，但输出带了一个不在原文/批准输入来源中的
`input_requirements` 值，触发 `FOCUS_INPUT_TEXT_CHANGED`。这是验证器正确拦截，不是
应放宽的检查。

修复：prompt 要求当前请求没有具体值时 `input_requirements` 必须为空；有效数据由
Design 依 schema 派生。没有绕过 `FOCUS_INPUT_TEXT_CHANGED`。

### 6. Design 仍回显默认 0，索引修复未命中

`attempt-7` 已通过 Focus/IR，但 Design 为每个 step 回显 `requirement_action_index=0`，
不是 NULL；旧修复只处理“缺失索引”，因此仍拒绝。原始模型回复通过新增的
`model-outputs.jsonl` 取证确认。

修复：当索引缺失、越界或指向错误操作，且目标操作在来源子句中唯一出现时，纠正为该
操作的真实索引；重复操作仍拒绝。新增“默认 0”回归测试。

### 7. 开发入口与取证工具问题

- 预检曾以 40,474 字节请求体误触发 20k token 上限（实际 provider input 约 11k）；
  开发入口分离“字节信封”与“实际 token 上限”，只在运行后核对实际 usage，不放宽
  产品默认。
- 开发入口需要 150 秒任务时钟，而生产默认是 600 秒；新增 `task_max_seconds(service)`，
  仅允许显式 `dev_max_seconds` 覆盖，生产仍 600 秒。
- 早期 model HTTP transport 取证没有生效；改为包住 Focus/Design 的 decode 阶段，
  记录原始模型输出和上下文到 `evidence/model-outputs.jsonl`。这不是生产数据通道。

## L1/L2/L3 分别评估

- **L1 显式任务**：已有显式 M2/F1 冻结证据和 19 个 Planning IR 回归测试通过；
  本轮没有重新消耗真实模型位置，也没有重跑冻结 M2/F1。因此本轮拿到的不是新的
  L1 真实模型完成率，不应把历史结果重写成新结果。
- **L2 模糊任务**：本轮唯一真实评价对象。7 次运行后达到 1 次规范化 IR 完整成功；
  首次真实运行是 `LEGACY`，修复后第 1 次重试（attempt 8）完成。该任务暴露了一个
  真实可用性失败，而不是全部 PASS。
- **L3 上线前请求**：没有真实运行。12 次模型调用上限已用完；没有用 L2 结果冒充 L3，
  也没有继续自动扩预算。代码层已有“多操作集合”回归，但不足以宣称 L3 成功。

## 新回归与 CI

新增/更新的离线回归：

- 多操作集合子句 → 每操作一个实例；
- 已请求范围的复述子句 → 约束、不重复实例；
- Design 来源 id 无索引 → 按唯一操作解析；
- Design 默认 0 索引 → 按唯一操作纠正；
- Focus prompt 不把普通测试设计推回用户、无具体值时输入为空；
- 开发入口只允许显式降低任务时钟。

本地目标套件：`tests/test_planning_ir.py tests/test_request_requirements.py
tests/test_product_instance_prompt.py` = **68 passed**；`ruff check .` 通过。

全量离线：`.venv\Scripts\python.exe scripts/check_full.py --jobs 4` =
**2660 collected / 2645 passed / 0 failed / 0 errors / 15 skipped**，
`source_unchanged=true`、`exact_test_id_coverage=true`、`successful=true`，耗时
985.64 秒。开发入口依赖真实模型和临时 fixture，不进入无密钥 CI；离线回归进入现有
Python CI。最终提交后的 CI 在交付回复中给出核验结果。

## 仍然存在的通用问题

1. **操作相关性**：Agent 会把 `/availability` 纳入 CRUD 范围。需要在规划阶段做
   feature/operation 相关性判断，而不是只靠“已批准”。
2. **负向断言审批**：删除后读回 404/410、字段边界、非法枚举等没有已批准 policy；
   Agent 只能建议、不能执行。需要合同派生的负向 policy 或明确审批流程。
3. **模型稳定性**：7 次仅 1 次规范化成功；Focus 会发散、Design 会回显默认索引。
   需要受限的验证反馈/修复机制，但不能自动改断言或重放写请求。
4. **隐藏索引的根因**：当前验证器靠“操作唯一”反推；同一操作多次出现仍需要更强的
   实例身份句柄，不能长期依赖默认值。
5. **token 预算单位**：开发入口仍用 UTF-8 字节信封避免超限，实际 token 只在事后核对；
   需要 tokenizer 或 provider 的请求前计数，减少误拒。
6. **清理后可见性**：只验证 DELETE 204，没有验证资源消失；需要已批准 read-after-delete
   规则。
7. **健康探针污染**：runner 的 `/health` 也写入 target events；分析时要和业务请求分开。
8. **L3 未真实验证**：多 API 上线前场景还没有真实完成率。
9. **取证工具是开发包装**：`RecordingModel` 包住 decode 仅用于开发；应做成正式的
   model event/usage sink，而不是 monkeypatch。
10. **失败解释**：503 的失败解释明确“原因未确认”，这是正确的保守行为；但报告使用
    者仍需要环境/契约线索来区分 fixture 故障和真实缺陷。

## 下一步（需要新的显式预算/授权）

1. 给 L3 一条独立真实预算（至少 2 次模型调用），在全新 fixture 上验证“上线前一轮测试”
   的范围、正常/边界/负向组合，不重放本轮写请求。
2. 给 L1 一条独立显式对照（不改冻结证据），用于同版本 before/after 比较。
3. 增加 delete→GET 404/410 的已批准负向 policy，或明确的产品审批入口。
4. 增加 operation relevance 规则/评估，避免把 health/readiness 当作 CRUD 必测项。
5. 把开发入口的取证、预算和 fixture 生命周期固化为可重复的开发工具，不进入生产路径。
6. 只有在新的明确授权下才扩大模型预算；本轮 12 次上限不再自动重试。
