# M2/F1 确定性端到端验收（2026-11-09）

本轮基线为 `2027e8e`（本地 == `origin/master`，工作树干净）。只在 master 提交与普通 push，
`WRITE_AUTHORITY` 保持 `NOT_GRANTED`；**没有真实模型调用、没有生产写入、没有执行冻结的 v4 M2/F1
付费位置**，全部 HTTP 走进程内 `httpx.MockTransport`，目标是一个有状态、隔离的本地 CRUD 模拟器。

## 1. 已发现缺陷：用 204 响应充当数据来源

`tests/test_plan_instance_dependencies.py` 的最小 CRUD 反例里，删除后回读写成
`read_after.from_step="d"`。DELETE 按计划返回 **204 且无响应体**，于是：

- 数据依赖被错误地挂到删除步骤上，运行时根本取不到 `$.id`（真实执行会
  `DEPENDENCY_RESOLUTION_FAILED`，而旧测试只构造不执行，掩盖了这一点）；
- "回读在删除之后"这一**顺序**事实被伪装成了**数据**事实，
  即使依赖图"看起来"正确，也证明不了构造层真的理解用户规定的先后关系。

修复：`read_after` 的路径参数绑定回 `from_step="c"`（CREATE 返回的资源 ID），
顺序只由 `request_4` 的实例级 `before` 产生。测试现在同时断言两条边及其**成因**：

| 边 | 修复前 | 修复后 |
| --- | --- | --- |
| `read_after ← c` | 不存在 | `MODEL_REFERENCE`（复用创建返回的 ID） |
| `read_after ← d` | 由错误的 `from_step` 伪装成数据边 | `TEXT_ORDER`（章节里写明的"删除后再读取"） |

并新增对照用例：只去掉 `request_4` 的顺序要求，`read_after ← d` 立即消失，
`read_after ← c` 仍在——证明顺序来自需求实例，而不是绑定副作用。
`construct_plan` 的 `stats` 因此新增 `edge_reasons`（`每边一条记录`），
`construct_design` 在切分执行上下文时把它与 `node_dependencies` 一并移除，历史构造记录不变。

同类扫描：全仓库 `from_step` / `source_operation` 的取值只指向 create/login/producer/read 这类
**有响应体**的步骤，没有第二处"为造边而错绑 204 删除响应"。

## 2. 新增确定性端到端链路

`tests/test_instance_delivery_e2e.py`（18 条）用真实的 `build_generic_project` 生成 OpenAPI 契约、
目标配置与策略注册表，然后依次经过：

1. 实例绑定（`requirement_source_id` / `requirement_action_index`）与设计门
   `validate_plan_requirements`；
2. `construct_plan`（实例集合 → 依赖边 → Kahn 定序）；
3. `GenericPlannerDraft.model_validate(built).scenario()` 得到 DSL 场景；
4. `Executor.validate`（DSL 校验 + 策略解析 + 授权）；
5. `Executor.run`：动态参数解析 `_binding`、真实 `httpx` 调用、
   响应验证 `_verify`、生命周期日志 `LifecycleJournal`、`ReportStore` 持久化。

没有直接调用 mock handler 绕过执行器，也没有任何模型调用。

### M2：POST 201 → GET 200 → DELETE 204 → GET 404

| 顺序 | 步骤 | 请求 | 状态 | 断言 |
| --- | --- | --- | --- | --- |
| 1 | `c` | `POST /items` | 201 | 状态码（创建返回 `{"id":"item_1"}`） |
| 2 | `read_before` | `GET /items/item_1` | 200 | 状态码 + `$.name == "first"` |
| 3 | `d` | `DELETE /items/item_1` | 204 | 状态码（无响应体） |
| 4 | `read_after` | `GET /items/item_1` | 404 | 状态码（预期不存在，PASS） |

依赖边：`read_before ← c`、`d ← {c, read_before}`、`read_after ← {c, d}`；
`read_after ← c` 是数据边，`read_after ← d` 是顺序边。报告 `PASS`，
`cleanup = 本轮资源已清理。`，生命周期 `CLEANED / PASS`，模拟器内资源最终为空。

### F1：POST 201 → GET 200 → PATCH 200 → PUT 200 → DELETE 204 → GET 404

| 顺序 | 步骤 | 请求 | 状态 | 断言 |
| --- | --- | --- | --- | --- |
| 1 | `c` | `POST /items` | 201 | 状态码 |
| 2 | `r1` | `GET /items/item_1` | 200 | 状态码 + `$.name == "first"` |
| 3 | `p` | `PATCH /items/item_1` | 200 | 状态码 + `$.name == "renamed"` |
| 4 | `u` | `PUT /items/item_1` | 200 | 状态码 + `$.name == "replaced"` |
| 5 | `d` | `DELETE /items/item_1` | 204 | 状态码 |
| 6 | `r2` | `GET /items/item_1` | 404 | 状态码（预期不存在） |

依赖边：`r1 ← c`、`p ← {c, r1}`、`u ← {c, p}`、`d ← {c, u}`、`r2 ← {c, d}`。
六次请求全部命中同一个 `item_1`；PATCH/PUT 的请求体与断言选择器都被逐条核对。

### 乱序提案

M2 与 F1 各用 4 种数组排列（正序、逆序、两种乱序）构造：执行序、依赖边、
每条边的成因完全一致；另有一条用例把乱序提案真的执行了一遍，HTTP 序列与正序完全相同。
`CONSTRUCTION_DEPENDENCY_CYCLE` 从未出现（该输入本来就是合法的）。

## 3. 执行器报告与资源最终状态

证据：`runs/20261109-delivery-matrix/matrix.json`（本地，gitignored）。

| 场景 | outcome | cleanup | 请求数 | 资源最终状态 |
| --- | --- | --- | --- | --- |
| M2 | `PASS` | 本轮资源已清理。 | 4 | 空 |
| F1 | `PASS` | 本轮资源已清理。 | 6 | 空 |
| 乱序 M2 | `PASS` | 本轮资源已清理。 | 4 | 空 |
| GET 超时 | `TEST_INFRA_ERROR` | 本轮资源已清理。 | 3（含清理删除） | 空 |
| PATCH 断言失败 | `ASSERTION_FAILED` | 本轮资源已清理。 | 4（含清理删除） | 空 |
| DELETE 500 | `ASSERTION_FAILED` | 清理未完成……不自动重放 | 3 | 仍存在 `item_1` |
| DELETE 结果未知 | `TEST_INFRA_ERROR` | 清理未完成……不自动重放 | 3 | 仍存在 `item_1` |
| 创建后取消 | `TEST_INFRA_ERROR / RUN_CANCELLED` | 清理未完成……不自动重放 | 2 | 仍存在 `item_1` |

报告逐步骤记录：步骤 id、方法、模板路径、实际状态码、断言结果、响应摘要哈希、
尝试次数与错误码；`resource_lifecycle` 记录 `create_step / cleanup_step / status /
resource_id / cleanup_result`；`created_resources` 另记创建凭据与清理结论。
步骤尝试次数之和等于实际请求数（清理请求单独计入 `lifecycle_cleanup`）。

## 4. 失败注入结果

| 用例 | 注入 | 业务结论 | 清理分类 | 是否重放写请求 |
| --- | --- | --- | --- | --- |
| 创建后 GET 超时 | `httpx.ReadTimeout` | `TEST_INFRA_ERROR`，步骤原因 `ReadTimeout` | **已确认清理**（`DELETE` 204 → `CLEANED/PASS`） | 否（仅一次删除） |
| PATCH 断言失败 | 200 + 错误 `name` | `ASSERTION_FAILED`，字段断言 `passed=False` | **已确认清理**（同上） | 否 |
| DELETE 500 | 500 响应 | `ASSERTION_FAILED` | **清理失败**（`CLEANUP_PENDING / ASSERTION_FAILED`） | 否（`DELETE` 只发一次） |
| DELETE 结果未知 | `httpx.ReadError` | `TEST_INFRA_ERROR`，尝试错误码 `ReadError` | **清理结果未知**（`CLEANUP_OUTCOME_UNKNOWN / OUTCOME_UNKNOWN`） | 否 |
| 创建后取消 | 任务 `cancel()` | `RUN_CANCELLED` | **未发送**（`OWNED / NOT_SENT`，资源保留待人工核实） | 否（无第二次写） |
| 已知成功 DELETE 后 GET 404 | —（M2 主用例） | `PASS` | 已确认清理 | 不适用 |

明确区分：清理成功从不覆盖业务结论（超时与断言失败两例的业务结论保持失败），
结果未知的写请求不重试、不补发、不猜测；取消路径不再产生任何隐藏写操作。

## 5. 全量回归与远端 CI

- `python -m ruff check .`：通过（0 错误）。
- `python -m pytest examples/regressions -q`：10 passed。
- 新增端到端 18 passed；实例依赖 11 passed；`test_plan_construction` + `test_request_requirements`
  + `test_resource_lifecycle` + `test_executor`：90 passed；
  mixed planning 与 product design/tasks 四套：101 passed。
- `python scripts/check_full.py --jobs 4`：**2560 collected / 2545 passed / 0 failed / 15 skipped**
  （1041 s，离线，`source_unchanged=true`、`exact_test_id_coverage=true`）。
- 远端 CI：PUSH_CI_PLACEHOLDER

## 6. 是否具备进入真实模型验收的条件

离线层面**具备**：实例绑定、构造、DSL/策略校验、动态参数、执行、响应验证、生命周期与报告
已在同一条生产链路上跑通，M2/F1 的请求序列、断言与清理结论均可复现、可审计，
失败注入把"已确认清理 / 清理失败 / 清理结果未知 / 禁止重放"四类结果钉死。

真实模型验收仍**不具备自动开始**的条件，需要一次新的显式付费授权：
本轮证明的是"当输入正确且已授权时，执行链路稳定"；它不能替代"模型能否稳定产出正确绑定"的证据。
冻结的 v4 `M2`/`F1` 付费位置本轮**未执行**，`CONSUMED_POSITIONS=("M1",)` 不变。

## 7. 尚未解决的阻塞问题

1. 规划侧仍依赖模型正确填写 `requirement_source_id` / `requirement_action_index`；
   离线只有 mock/夹具证据，真实模型填写质量未验证。
2. 取消后不执行已授权的清理（`OWNED / NOT_SENT`）：这是有意的安全选择，
   但需要人工核实资源，尚无自动对账入口。
3. 同一操作两次出现且没有显式实例绑定时会停在 REVIEW（`CONSTRUCTION_ORDER_INSTANCE_UNBOUND`
   或 `CONSTRUCTION_REQUIRED_ORDER_AMBIGUOUS`），模型必须学会绑定才能通过。
4. 跨用例顺序仍受用例序列约束，报告读者需理解"用例内顺序由构造决定"。
5. 真实目标（非模拟器）的 DELETE 语义、404 契约声明与凭据隔离尚未验收。
