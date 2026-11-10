# 冻结 v4b M2 唯一一次真实验收执行记录（第 7 轮）

日期：2026-11-10。目标提交：`de9a8691880951132f6454e351c8b536c00934bd`。
本轮是正式验收执行，不修改源码、Prompt、schema、测试、冻结配置或评分器。

## 0. 结论摘要

**M2 没有执行，也没有被消费。** 唯一一次执行命令在冻结一致性门禁处被拒绝：

```
FROZEN_ARTIFACT_DRIFT:CASES_CHANGED
```

拒绝发生在 `claim` 之前，因此：零真实模型调用、零业务 HTTP 请求、零消费、无
`positions/M2`；`F1` 仍为 `UNCONSUMED`；`WRITE_AUTHORITY` 仍为 `NOT_GRANTED`。
按本轮停止条件（冻结产物漂移），已立即停止，未重试。

M2 的最终 verdict 为**无**（`NOT_EXECUTED`），不是 `PASS`，也不是任何评分结论。
位置状态仍是 `UNCONSUMED`，因此不存在 attempt ID。

## 1. 执行环境核验（全部通过）

| 检查 | 结果 |
| --- | --- |
| `git rev-parse HEAD` | `de9a8691880951132f6454e351c8b536c00934bd`（与目标一致） |
| `origin/master` | 同一提交 |
| `git status --porcelain` | 空（工作树干净） |
| 目标 SHA 的 Actions | Keyless `#42756481556` success、Web `#42756481514` success |
| `freeze.json` | 存在，未被修改（工作树干净；冻结 `source.head=d8eeae1`） |
| 冻结题目/rubric/评分器/模型/预算/目标 | 预检九项全部 `READY`；`blocking=[]` |
| 模型身份 | `ecnu-max` / `chatecnu` / `https://chat.ecnu.edu.cn/open/api/v1` / 45 s / 4096 out / 20000 in，凭据 `PRESENT` |
| M1 | `HISTORICAL_CONSUMED` |
| M2 | `UNCONSUMED` |
| F1 | `UNCONSUMED` |
| 历史 M2 证据 | 无 `positions/`、无完成/中断/写失败记录 |

只读状态（执行前）：

```json
{"positions":{"M2":"UNCONSUMED","F1":"UNCONSUMED","M1":"HISTORICAL_CONSUMED"},
 "runnable":["M2","F1"]}
```

预检（本轮新生成，未覆盖旧文件）：

```text
M2 READY  9/9 items（position_unconsumed=READY, model_identity_matches=READY, ...）
F1 READY  9/9 items
{"verdict":"READY","manifest":"execution-manifest-de9a869.json","blocking":[],
 "warnings":[{"reason":"SOURCE_CHANGED","detail":"de9a8691880951132f6454e351c8b536c00934bd"}]}
```

`SOURCE_CHANGED` 是预期告警：冻结记录基于 `d8eeae1`，本轮按授权在 `de9a869` 执行，
manifest 列出 44 个变更文件（26 新增 / 18 修改 / 0 删除），供审计。

## 2. 授权记录

通过项目原生命令创建，记录不可覆盖、无凭据：

```json
{"schema":1,"authorization_id":"01e72f0af6294359a66137e544d49a64",
 "case":"M2","executing_head":"de9a8691880951132f6454e351c8b536c00934bd",
 "frozen_sha256":"31741a2d751b7e9d305f1209d3bcca6b895b6fe01ee6605ad561edc206abea63",
 "operator":"mofengyan","allow_model_calls":true,"allow_loopback_writes":true,
 "budgets":{"max_requests":12,"max_seconds":150,"max_model_calls":2},
 "model_timeout":45,"integrity":"(校验通过)"}
```

- 操作者 `mofengyan` 取自本机仓库的 git 身份（`git config user.name`），与提交作者一致，
  非虚构。
- 用户已明确确认 **150 秒**；旧检查表的 120 秒文案不是实际预算，授权记录中的预算为
  `12 / 150 / 2`。
- 该授权仍未使用（M2 未消费）；`authorize` 对同一 `(case, head, frozen)` 拒绝重复创建，
  因此记录原样保留。
- 文件：`runs/rw-acceptance-v4b-20261109/authorizations/authorize-M2-de9a869.json`，
  归档副本见证据目录。

## 3. 唯一一次执行及拒绝

执行命令（仅调用一次，未重试）：

```bash
python scripts/rw_lifecycle/acceptance.py run runs/rw-acceptance-v4b-20261109 \
  --plan plan_v4 --cases M2 --accept-code-change de9a869
```

输出（完整日志见证据 `m2-run-de9a869.log`）：

```text
FROZEN_ARTIFACT_DRIFT:CASES_CHANGED
```

执行后只读状态：

```json
{"positions":{"M2":{"status":"UNCONSUMED","signals":[]},
              "F1":{"status":"UNCONSUMED","signals":[]},
              "M1":{"status":"HISTORICAL_CONSUMED","signals":["frozen-plan"]}},
 "runnable":["M2","F1"]}
```

`runs/rw-acceptance-v4b-20261109/positions` **不存在**；没有 `claim.json`、
`running.json`、`evidence.json`、`completed.json`、`interrupted.json` 或
`write_failure.json`。没有 attempt ID。

## 4. 根因：门禁使用了被 `--cases` 过滤后的视图

`acceptance.run()` 的冻结一致性门禁调用链：

```python
guard(...) -> execution_gate.compare(frozen, current_view(identity, initial))
current_view(...) -> execution_gate.snapshot(..., cases=cases(), ...)
cases() -> [case for case in CASES if case["key"] in SELECTED]
```

`--cases M2` 会把 `SELECTED` 设为 `["M2"]`，于是 `current_view` 只包含 M2；
而冻结 `requirements.digest` 覆盖 M2+F1。离线复现（只读，不启动 fixture）：

| 视图 | case keys | `requirements.digest` | `compare` 结果 |
| --- | --- | --- | --- |
| 冻结记录 | `["M2","F1"]` | `f2ba78ab754c` | — |
| 完整计划 | `["M2","F1"]` | `f2ba78ab754c` | `blocking=[]` |
| `--cases M2` | `["M2"]` | `74984a42bb7b` | `CASES_CHANGED: ["F1"]` |

因此这是驱动器的门禁视图缺陷，不是冻结产物真的漂移：完整视图与冻结摘要完全一致；
单位置执行时，门禁错误地把“未被选择的 F1”当成“被删除/修改的 case”。

### 为什么预检和测试没有发现

- 预检命令没有 `--cases`，`SELECTED` 为全部 case，视图完整，九项 READY。
- 第 5/6 轮的 `run()` 级测试都 monkeypatch 了 `acceptance.guard`（或使用完整记录），
  没有一条用例真正用 `--cases M2` 跑真实 `guard`。
- 这是第 5 轮引入“一次只执行一个位置”时，`cases()`（选择视图）与门禁所需
  “完整冻结视图”共用了同一个函数造成的耦合；前几轮只有预检（无过滤）实际运行过真实门禁。

## 5. 分阶段结论（Focus / Design / Plan Construction / Executor / Lifecycle）

全部为 **NOT_EXECUTED**：门禁在 `service()`、`run_case()` 和 `claim` 之前拒绝，没有任何
模型或业务请求，因而没有可分析的 Focus 输出、Design 输出、依赖图或 HTTP 证据。

| 阶段 | 结论 | 证据 |
| --- | --- | --- |
| Focus | 未执行 | 0 模型调用，无 Focus 证据 |
| Design | 未执行 | 0 模型调用，无 Design 证据 |
| Plan Construction | 未执行 | 未进入构造 |
| Executor | 未执行 | 0 业务 HTTP |
| Lifecycle | 未执行 | 无 CREATE/DELETE/GET |

不能把本轮结果解释为 M2 的规划或执行失败，也不能与 v4a M1 的 `NEEDS_REVIEW` 直接比较。

## 6. HTTP、资源与成本

- 业务 HTTP 请求：**0**。预期序列 `POST 201 → GET 200 → DELETE 204 → GET 404` 未发生。
- fixture 控制面：门禁前 `preflight.reset("e2e-freeze-0001")` 对 loopback fixture 发送了
  重置请求（fixture 自身控制端点，非 `/items` 业务操作），未创建任何资源，无残留。
- 结果未知的写入：**无**（没有任何业务写请求）。
- 真实模型调用：**0** 次；输入/输出 token 均为 **0**（没有调用，不是 `UNKNOWN`）。
- 模型耗时/任务耗时：不适用（任务未启动）；runner 在门禁处立即退出。
- 预算：未触及（0/12 请求、0/2 模型调用）。

## 7. 首个阻塞阶段与根因

- 首个阻塞阶段：`run()` 的冻结一致性门禁（`claim` 之前）。
- 诊断码：`FROZEN_ARTIFACT_DRIFT:CASES_CHANGED`，`detail=["F1"]`（离线复现）。
- 根因：`current_view` 使用 `cases()` 的 `--cases` 过滤视图参与冻结比对，见第 4 节。

## 8. 残留资源与未知写入

无。没有 `positions/`、没有业务请求、没有 fixture 资源创建；隔离靶场的命名空间已被重置但
未产生条目。M2 未消费，F1 未触碰。

## 9. 与历史 v4a M1 失败原因的对照

| 项 | v4a M1（历史） | 本轮 M2 |
| --- | --- | --- |
| 是否进入模型 | 1 次调用 / 11750 tokens | 0 次 |
| 是否发送业务请求 | 0 | 0 |
| 结果 | `NEEDS_REVIEW`（已消费） | 未执行（未消费） |
| 诊断 | `FOCUS_REQUIREMENT_WITNESS_MISSING`（规划/见证缺失） | `FROZEN_ARTIFACT_DRIFT:CASES_CHANGED`（门禁视图缺陷） |
| 层级 | 产品规划阶段 | 验收驱动器 |

两者没有可比性：M1 是真实的模型输出被判定为不可交付；本轮根本没有到达模型。M1 不能被
重放或重评，本轮也没有产生新的 M2 模型证据。

## 10. F1 状态与下一步

- F1 仍为 `UNCONSUMED`，没有授权、没有执行、没有消费。
- **不建议**现在进入 F1：M2 尚未取得任何真实结果，且同一门禁缺陷会同样阻止 F1 的单位置
  执行。
- 建议的下一步（需要新的代码轮与新的操作者确认，不属于本轮）：
  1. 修复门禁视图：`run()` 的冻结比对使用完整计划 case 集合（`CASES`），而“执行哪个位置”
     继续由 `SELECTED` 决定；补充一条真正以 `--cases M2` 调用真实 `guard` 的集成测试。
  2. 在新 HEAD 上重新预检，确认 M2 READY、组装摘要与冻结一致。
  3. 由于授权绑定 HEAD，新提交需要操作者针对新 HEAD 再次确认一次 M2 授权；旧的
     `authorize-M2-de9a869.json` 保留为审计记录，不复用。
  4. 修复并预检通过后，再请求“唯一一次 M2”执行；仍不得自动重试，F1 继续等待。

## 11. 证据与现场

证据目录：[docs/evidence/rw-acceptance-m2-de9a869/](evidence/rw-acceptance-m2-de9a869/)

- `preflight-de9a869.json`：九项 READY、`blocking=[]`、`SOURCE_CHANGED` 告警；
- `execution-manifest-de9a869.json`：frozen `d8eeae1` → `de9a869`，44 文件，预算 12/150/2；
- `authorize-M2-de9a869.json`：绑定 M2、HEAD、冻结摘要与预算的授权记录；
- `m2-run-de9a869.log`：唯一一次执行的原始输出（拒绝原因）；
- `status-after-de9a869.json`：执行后只读状态（M2/F1 `UNCONSUMED`，M1 历史）。

现场保留在 `runs/rw-acceptance-v4b-20261109/`；未删除、未覆盖任何既有预检/证据文件。
`WRITE_AUTHORITY` 全程保持 `NOT_GRANTED`。
