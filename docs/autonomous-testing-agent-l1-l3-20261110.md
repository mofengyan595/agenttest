# 自主 API 测试同事：L1–L3 真实模型开发记录

日期：2026-11-10  
开发起点：`912834e`（本次改动前的 HEAD）  
模型：`ecnu-max`（真实调用；凭据仅在 `.env`）  
范围：仅本机回环测试目标 `parcels`；未访问生产、外部业务或 CourseInsight。

## 1. 这次验证要回答的问题

不是“能否按预先写好的 HTTP 步骤执行”，而是：

1. 用户只给一句自然语言，Agent 能否自己选范围、判断哪些子句是请求、哪些是断言；
2. 能否自己发现创建→读回→修改→确认→删除→删除后 404 的依赖和顺序；
3. 边界值、批准负向检查、故障发现能否走同一条产品链；
4. 失败时是否保留证据、给出原因，而不是把断言改弱或重放到 PASS。

本轮明确取消聚合调用/token 上限；保留单任务预算、单请求超时、目标并发和隔离边界。
因此下面的调用量是**实际发生量**，不是预算上限。

## 2. 最终可复现的真实结果

| 场景 | 自然语言请求（摘要） | 首次尝试 | 迭代后 | 最终证据 | 模型调用 / tokens | 业务 HTTP | 结果 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| L1 完整 CRUD 闭环 | 创建包裹，按 ID 读回，把名称改成另一个名字，再确认，最后删除并确认删除成功 | 0/1 | 1/1（第 8 次运行 `l1-a8`） | `runs/dev-20261110-next/l1-a8` | 2 / 24,354 | 5 | PASS |
| L2 功能范围选择 | 只测试包裹功能：创建、按 ID 读回、确认名称和数量字段都有；不要调用可用性接口 | 0/1 | 1/1（第 3 次运行 `l2-b3`） | `runs/dev-20261110-next/l2-b3` | 2 / 20,337 | 2 | PASS |
| L3 删除后回读 404 | 创建，按 ID 读回后删除；删除后再次读取同一 ID，确认返回 404 | 0/1 | 1/1（第 3 次运行 `l3-c3`） | `runs/dev-20261110-next/l3-c3` | 2 / 21,499 | 4 | PASS |
| L3 批准负向边界 | 测试创建包裹数量下界：数量 0 应被拒绝并返回 422 | 1/1 | 1/1（`l3-c4`） | `runs/dev-20261110-next/l3-c4` | 2 / 17,648 | 1（POST→422） | PASS（LEGACY 规划） |
| L3 正常边界 | 创建数量为 3 的包裹，确认成功，然后删除 | 1/1 | 1/1（`l3-c5`） | `runs/dev-20261110-next/l3-c5` | 2 / 19,295 | 2（201、204） | PASS |
| L3 故障发现 | 检查包裹可用性接口是否正常，预期返回 200 | 1/1（检测成功） | 无需修复 | `runs/dev-20261110-next/l3-c6` | 2 / 16,522 | 1（GET→503） | `ASSERTION_FAILED`，符合目标 |

### 首次成功率与迭代后成功率必须分开看

- 三个核心自然语言任务（L1、L2、L3 删除后 404）的**首次尝试成功率都是 0/1**；
  分别在 7、2、2 轮确定性修复/重跑后达到 1/1。
- 三个独立补充任务（负向 0、正常 3、故障发现）**首次尝试即得到可用结果**，为 3/3。
- 因此，按“六个独立任务”统计：
  - 首次尝试得到可用结果：**3/6**；
  - 迭代后任务完成：**6/6**；
  - 其中业务断言真正 PASS：**5/6**；第 6 个（`l3-c6`）按设计正确地报
    `ASSERTION_FAILED`，不是产品失败。
- 不能把 L1 的 `1/1` 说成首次成功率；它是同一任务第 8 次运行的迭代后成功率。

### 调用与 HTTP 汇总

涉及 17 个真实运行目录（`l1-a1`…`l1-a8`、`l2-b1`…`l2-b3`、`l3-c1`…`l3-c6`）：

- **27 次真实模型调用**，**314,764 tokens**，全部已知，无 UNKNOWN；
- **15 次业务 HTTP**（不含目标健康检查）；
- 成功/检测的 6 个最终运行合计 **12 次调用 / 119,655 tokens / 15 次业务 HTTP**；
- 每个最终运行都是 2 次调用（Focus + Design），没有自动重规划、没有重放未知写入；
- 失败尝试大多数在 Focus/IR/Design 阶段停下，业务 HTTP 为 0；没有残留资源。

## 3. 失败分类与根因

失败不是“模型偶尔不行”一句话带过；每次失败都定位到具体层和字段，再做独立重测。

### 3.1 Focus 层：自然语言证据识别

| 运行 | 诊断 | 根因 | 修复 |
| --- | --- | --- | --- |
| `l1-a2` | `FOCUS_REQUIREMENT_WITNESS_MISSING` at `requirement_map.request_6.actions` | “确认删除成功”是写操作后的断言，模型给了空 actions；旧校验要求每个 required 子句都有可执行 witness | `evidence_fragment()` 接受无状态/字段谓词的确认句为不可执行证据；不再强迫它制造 GET |
| `l2-b1` | `FOCUS_REQUIREMENT_WITNESS_MISSING` at `requirement_map.request_3.actions` | “确认名称和数量字段都有”被当成新的独立请求，但模型没有给它 actions；前一个读操作已经存在 | `bind_response_continuation()` 把裸确认的 response_checks 移到前一个读实例，确认子句降为 constraint |
| `l3-c1` | `FOCUS_REQUIREMENT_WITNESS_MISSING` at `requirement_map.request_4.actions` | “确认返回 404”中的裸 `404` 没有被状态解析器识别，无法成为前一个读的断言 | 扩展 `_STATUS`，识别“返回/响应 + 4xx/5xx”，同时拒绝把“返回200条记录”误判为状态码 |
| `l3-c2` | `FOCUS_REQUIREMENT_ORDER_INSTANCE_INVALID` at `requirement_map.request_2.later` | 同一子句同时包含 read 和 delete，模型把两个调用的 `later_index` 都写成 0；旧逻辑只按数组下标验证 | `order_instances()` 在命名操作在该子句只出现一次时，用操作身份纠正重复的 0 下标；重复同一操作仍要求显式下标 |

### 3.2 Planning IR 层：子句角色和实例身份

| 运行 | 诊断 | 根因 | 修复 |
| --- | --- | --- | --- |
| `l1-a1` | `UNCLASSIFIED_CLAUSE`（`request_3`）+ 多条 dangling order | “把名称改成…”缺少“改成/改为/变更”请求动词；消失的确认子句仍带着 order 边 | 增加变更/读回动词；确认子句降为 constraint；跳过指向非请求子句的顺序边 |
| `l1-a3` | `MISSING_INSTANCE_IDENTITY`（`request_5.before`） | “再确认”被模型映射成读，但 IR 视为确认而丢弃，留下指向它的顺序要求 | `_clause_role` 区分“读回确认”和“写成功确认”；读回确认保留为 REQUEST，写成功确认才降为 CONSTRAINT |
| `l2-b2` | `UNCLASSIFIED_CLAUSE`（`request_2`）+ `MISSING_INSTANCE_IDENTITY` | “按 ID 读回”缺少读回动词，整段被当成无法归类的片段 | 增加 `读回/取回/查回` 到请求和读动词；重新运行 `l2-b3` 后 2 实例、0 歧义 |

### 3.3 Design 层：实例绑定与覆盖

| 运行 | 诊断 | 根因 | 修复 |
| --- | --- | --- | --- |
| `l1-a4` | `PLANNING_INSTANCE_UNBOUND` at `plan.steps.3` | IR 没有“再确认”的实例，Design 却又添加了一个读步骤 | 把“再确认”作为读回请求保留在 IR；Design 和 IR 对同一文本采用同一角色 |
| `l1-a5` | `PLANNING_INSTANCE_UNBOUND` at `plan.steps.5` | “确认删除成功”之后，Design 自行添加未获批的删除后 GET | Focus/Design prompt 明确：只有用户明确要求读回且存在批准的 absence 实例/policy 时才增加；否则不得添加 |
| `l1-a6` | `PLANNING_INSTANCE_COVERAGE`（`ai_2, ai_4, ai_6`） | 一个由批准 404 policy 派生的实例没有被 Design 覆盖 | 保留显式实例驱动；后续 prompt 要求只规划真实实例，不凭空补步骤 |
| `l1-a7` | `PLANNING_INSTANCE_CONFLICT` at `plan.steps.5.requirement_source_id` | Design 对同一 source 的删除后读复用了 index 0，和删除实例冲突 | 编译器在 index 已消费时按 `operation_id` 绑定剩余实例；不跨操作猜 |

### 3.4 规划引擎回退与目标断言

| 运行 | 现象 | 解释 |
| --- | --- | --- |
| `l3-c4` | `planning_engine=LEGACY`，1 条 ambiguity `RESPONSE_STATUS_NOT_APPROVED` | 批准负向策略（quantity=0→422）没有进入规范化 IR 的正向实例 policy 选择；LEGACY 仍然正确执行了 1 次 POST 并验证 422。这是**能力缺口**，不是伪造 PASS |
| `l3-c6` | `case_outcomes[0].outcome=ASSERTION_FAILED`；actual 503，expected 200；`$.state` actual `unavailable` | 目标 `/availability` 是故意固定的 503 故障样本；Agent 没有把 503 改写成通过，失败解释为“响应不符合已确认预期”，并明确 `cause_confirmed=false`，下一步只允许只读复核 |

`l3-c6` 的解释证据（来自 report）：

- mismatch：`readAvailability` 的响应状态实际为 503，预期为 `[200]`；
- additional check：同一只读探测再次得到 503；
- 结论：观察已确认，但“内部原因”未证实；没有直接把 503 认定为业务缺陷；
- 没有自动修复、没有自动重试写操作、没有修改断言。

## 4. 代码改动（按机制）

1. **单任务模型预算**
   - `ProjectBudget.max_model_calls` 上限从 4 提升到 20；
   - `scripts/run_development_agent.py` 真正使用 `max_model_calls`，不再硬编码 2；
   - 默认单任务 8 次、24 请求、300 秒；
   - 超时原因改为动态的 `本轮达到 N 秒时限`；
   - 这仍是单任务循环/安全预算，不是全局实验总额度。

2. **Planning IR 子句语义**
   - 新增变更动词（`改成/改为/变更`）、读回动词（`读回/取回/查回`）、读写动词集合；
   - 确认子句按“读回确认”和“写成功确认”分流；
   - 指向非请求子句的顺序边不再产生歧义；
   - 读回确认可生成真实读实例，删除成功确认只作为约束。

3. **Response continuation 与证据片段**
   - 裸“确认字段/条数”把检查移到前一个读，不新增请求；
   - “确认删除成功”作为不可执行证据；
   - 但“检查健康”“检查包裹功能”等普通检查请求仍是可执行请求，不被误吞。

4. **实例身份**
   - `order_instances()` 支持同子句多操作时纠正被重复写成 0 的下标；
   - 编译器在同一 source 的 index 已消费时，按 `operation_id` 找剩余实例；绝不跨操作猜。

5. **Prompt 与范围**
   - Focus 只选用户命名功能内的操作，不主动加入 availability/health/readiness；
   - Design 不凭“确认成功”添加读，不添加无请求实例的步骤；
   - 明确“确认删除成功是 DELETE 的 reviewed status，不是新 GET”。

6. **隔离目标的可验证 absence 语义**
   - `scripts/prepare_colleague_workspace.py` 为 `readParcel` 增加
     `IntakeExpectation(statuses=[404], rule="删除同一包裹后再次读取应返回404……")`；
   - `scripts/serve_colleague_targets.py` 的文档补充 GET/PUT/PATCH/DELETE 的 404；
   - 这是操作者编写的 fixture 期望，不是模型发明的业务规则，也不是产品级自动推断。

## 5. 清理与安全边界

- L1 `l1-a8`：目标事件为 POST `/parcels`、GET、PATCH、GET、DELETE；生命周期
  `CLEANED / cleanup_result=PASS`。
- L3 `l3-c3`：POST、GET、DELETE、GET→404；生命周期 `CLEANED / PASS`。
- L3 `l3-c5`：POST 201、DELETE 204；生命周期 `CLEANED / PASS`。
- L2 `l2-b3` 只要求创建和读回，不要求删除；没有把“未要求删除”伪装成清理验证。
  它运行在一次性回环目标进程中，进程结束即销毁；报告中明确 cleanup 权限缺失。
- 失败尝试全部在业务 HTTP 之前停止（L1 前 7 次、L2 前 2 次、L3 前 2 次），没有残留资源。
- 未修改历史冻结的 M2/F1 验收；未重跑、未改写旧证据。
- 没有模型生成代码执行、自动修复提案、写请求重放或断言放宽；没有外部/生产写入。

## 6. 仍然存在的真实能力缺口

1. **首次稳定性仍低**：三个核心任务首次都是 0/1；目前是“失败→定位→修复→重测”
   的工程闭环可靠，不是“第一次就可靠”。需要新的、未用于调参的自然语言集评估首次成功率。
2. **批准负向边界仍可能回退 LEGACY**：`l3-c4` 的 422 policy 没有进入规范化 IR；
   应把 approved negative check 表示为 IR 实例/断言，而不是让引擎降级。
3. **自然语言变体仍然脆弱**：本轮修复了“改成/读回/返回404/同子句双操作”等具体形态，
   不能据此声称覆盖所有中文表达。
4. **删除后回读的派生策略路径**仍有离线测试，真实 L3 走的是用户明确写出的
   “再次读取同一 ID，确认返回 404”；不能把离线派生实例当成真实模型已经稳定掌握。
5. **没有真实业务目标**：只有 loopback fixture，没有认证、多租户、真实数据约束；
   真实业务接入需要单独环境和授权。
6. **L2 没有清理证明**：自然请求没有要求删除，不能据此证明任意读流程都会清理。

## 7. 下一步准确里程碑

1. 给规范化 IR 增加“批准负向检查实例”，让 `quantity=0→422` 不再降级 LEGACY；
   用同一自然请求真实重测一次，记录首次/迭代后成功率。
2. 冻结一组**未用于调参**的短自然语言请求（含省略读回、口语化删除确认、同子句双操作），
   只评估首次成功率，不按失败样本即时改 prompt。
3. 在获得独立隔离业务环境与明确写授权后，再验证真实认证、真实资源和清理；
   不把本次 loopback 结果外推为生产可靠性。
4. 保持历史 M2/F1 冻结；不再为分数重跑已消费位置。

## 8. 回归检查

- 定向测试：`tests/test_request_requirements.py`、`tests/test_requirement_ledger.py`、
  `tests/test_planning_ir.py`、`tests/test_planning_witness_recovery.py`、
  `tests/test_product_instance_prompt.py` 共 **101 passed**。
- `ruff check` 对本次改动文件通过。
- 全量离线结果见下方（以最终运行日志为准）：

<!-- FULL_SUITE_RESULT -->

## 9. 证据索引

- L1：`runs/dev-20261110-next/l1-a8/run.json`、`evidence/model-outputs.jsonl`、逐案 report
- L2：`runs/dev-20261110-next/l2-b3/run.json`
- L3 删除后 404：`runs/dev-20261110-next/l3-c3/run.json`
- L3 负向 0：`runs/dev-20261110-next/l3-c4/run.json`
- L3 正常 3：`runs/dev-20261110-next/l3-c5/run.json`
- L3 故障发现：`runs/dev-20261110-next/l3-c6/run.json`
- 失败尝试：同目录 `l1-a1`…`l1-a7`、`l2-b1`、`l2-b2`、`l3-c1`、`l3-c2`
- `runs/` 为本地证据目录，不入库；模型输出中的敏感字段由既有报告脱敏逻辑处理。

## 10. 结论

本轮把“按固定 HTTP 步骤执行”推进到：

- 能从一句自然语言自行选功能范围；
- 能区分请求、确认、状态断言和顺序约束；
- 能在同一子句内拆分 read/delete 并建立真实依赖；
- 能真实执行删除后 404 回读和数量边界；
- 能发现并解释 503 断言失败，而不是把失败改成通过。

但它仍是**开发中的自主测试同事**，不是已证明稳定的生产系统：首次成功率低、
负向 IR 仍会回退、自然语言变体覆盖不足、真实业务环境未验证。
这些限制必须保留在交付结论中。
