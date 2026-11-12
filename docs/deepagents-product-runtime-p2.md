# P2：把 Deep Agents 变成产品唯一的自主 Agent 运行时

- 日期：2026-11-12
- 仓库：`https://github.com/mofengyan595/agenttest`
- 实现基线（父提交）：`02a306a66ebf467cee4b6dca51fb906f36927e49`（P1 harness 评估与 delete-404 前置条件修复）
- 本报告的提交：与 P2 实现同一提交（`git log -1`）
- 环境：Windows 11 / PowerShell 5.1 / Python 3.11.9 / `.venv`
- 真实模型：`ecnu-max`（`https://chat.ecnu.edu.cn/open/api/v1`，`chatecnu` 协议，模型指纹 `28c7f772f8547602eab430f3e8a8de58737462c179197afffd75d56c616d0c0a`）

## 0. 结论摘要

1. **运行路径已切换**：Web / CLI / Goal / Suite 四个入口都经过 `ProductTasks.run`，默认
   `loop_engine="AUTO"` 在装好 Deep Agents 且配置了模型时解析为 `DEEP_AGENTS`；产品里不再有第二个
   自主 Agent Loop。旧的 `RoundSupervisor` 只在 `loop_engine=LEGACY_SUPERVISOR` 下作为回归/降级路径
   保留，`DETERMINISTIC` 路径保留给基础安装与离线测试。
2. **任务完成判据是确定性的**：新增 `task_completion.py`（`agenttest.task-completion.v1`），按
   功能测试 / 修改回归 / 上线前测试 / 故障调查四类给出逐条判据，把「Agent 终止」「证据产生」
   「义务覆盖」「任务完成」「异常发现」「待办遗留」拆成六个独立标志。模型最终答复只作参考，不参与
   判定；`checks_passed`（已执行检查是否全过）与 `complete`（本轮承诺范围是否真的覆盖）是两个问题。
3. **真实 ecnu-max 验证完成了工程目标，但没有达到「严格完成」的产品目标**。四个目标共 13 次独立真实
   运行（含 5 次修正后的复跑），严格完成 **0/4**。原因不是运行时壳子，而是三处真实的规划/设计层
   缺陷（见 §8）：多操作调查会被规范化 IR 的实例覆盖校验拒绝（`PLANNING_INSTANCE_COVERAGE`、
   `PLANNING_INSTANCE_UNBOUND`）；设计模型在监督轮里连续返回不可映射的方案
   （`MODEL_INVALID_RESPONSE`）；删除后缺失断言在真实模型下始终没有被设计出来。
4. **安全性没有被这些失败破坏**：13 次真实运行里 0 次未授权工具执行、0 次未知结果写重放、0 次
   删除-404 假阳性、0 次把缺失前置条件当成业务缺陷；删除后读回违例（实验室「删除返回 204 但不删」）
   由确定性链路检出为 `ASSERTION_FAILED`（§6.4，独立复跑，真实 HTTP，退出码 0）。
5. **成本高于 P1 两种方案**：P2 每目标 8–15 次模型调用、约 5.5–11.7 万 token（含 focus/设计/监督），
   是 P1 A 方案的 3–5 倍、B 方案的 1.2–3 倍；换来的是确定性判据、结构化反馈与不重放保证。
6. 全量离线回归 **2744 passed, 15 skipped, 0 failed**（§7）；`ruff check src tests scripts` 全绿；
   基础安装（无 `deepagents`）仍可导入、`AUTO` 降级为 `DETERMINISTIC`、显式要求 `DEEP_AGENTS` 时
   失败关闭（§2.3）。

## 1. 依赖与安装口径

```text
deepagents==0.7.35        langchain==1.4.10        langchain-core==1.7.7
langgraph==1.2.20         langgraph-checkpoint==4.3.1
langchain-protocol==0.0.21  wcmatch==12.0.1  bracex==3.2.2
```

- 新 extra：`pyproject.toml` 的 `deepagents`，并新增 `requirements-deepagents.lock` 锁定同一组版本。
- 安装用 `pip install --no-index --find-links .eduwork/wheels --no-deps -r requirements-deepagents.lock`。
  原因写在 lock 文件头：`deepagents` 还声明 Anthropic / Google GenAI 两个 provider，产品只用
  `EcnuxChatModel`（OpenAI 兼容），而离线 wheelhouse 里没有 `google-auth` 链；用 `--no-deps` 加显式
  版本锁定，避免"装得上但依赖漂移"。
- 运行环境由 Python 3.11 的 `.venv` 直接承载（P1 时 Deep Agents 在独立 `.venv-deepagents`；P2 起它是
  产品的一部分，不再需要第二个解释器）。`.venv-deepagents` 仍保留作对照，不参与运行。

### 2.3 基础安装（不装 Deep Agents）的实测

在真实 `.venv` 里让 `importlib.util.find_spec("deepagents")` 返回 `None`，模拟未安装 extra 的部署：

```text
import_without_deepagents True
harness_available False
auto ('DETERMINISTIC', 'AGENT_RUNTIME_MODEL_NOT_CONFIGURED')
explicit_without_harness AgentRuntimeUnavailable AGENT_RUNTIME_MODEL_NOT_CONFIGURED
```

`agenttest.product_tasks`、`agenttest.goal_server` 都能导入；`AUTO` 安静降级，显式指定
`DEEP_AGENTS` 时**失败关闭**，不会静默跑一个残缺的自主循环。该行为有单测
`test_an_explicit_engine_fails_closed_instead_of_silently_degrading`。

## 2. 现在的实际主路径

```text
Web workspace / CLI / Goal server / Suite
        └── TaskService.run_task
              └── ProductTasks.run(scope)                     ← 唯一自主入口
                    ├── focus: TestFocusModel → FocusSelection
                    ├── PlanningIR + normalize → design_groups → summary["groups"]
                    └── engine == DEEP_AGENTS
                          └── ProductTasks.run_deep_agent
                                └── product_agent.run_agent
                                      ├── EcnuxChatModel（既有适配器，on_call 记账）
                                      ├── create_deep_agent(model, tools, ...)
                                      ├── AgentSession（8 个工具 + 预算门 + 事实投影）
                                      ├── verify_enforcement / verify_catalogue
                                      └── ModelCallLimit / ToolCallLimit middleware
                                            └── ProductTasks.run_group / supervise_design
                                                  └── propose_design → validate_design_case
                                                        └── Executor（唯一 HTTP 出口）
                                                              └── ReportStore（可信证据）
                    └── evaluate() → agenttest.task-completion.v1 → summary/transition
```

- 引擎解析：`product_agent.resolve_engine(project, service)`；`project.registration.budget.loop_engine`
  决定 `AUTO / DEEP_AGENTS / LEGACY_SUPERVISOR / DETERMINISTIC`。`AUTO` 仅在
  `_harness_available()` 且模型已配置时为 `DEEP_AGENTS`（单测
  `test_auto_uses_deep_agents_only_when_the_deployment_configured_it`）。
- 旧监督循环：`ProductTasks.supervise()` 只在非 `DEEP_AGENTS` 分支被调用；`product_supervisor.py`
  本身未删（P1 冻结证据、回归测试、降级路径都要用），但产品默认不再走它。
- 业务任务仍由 `ProductTasks` 执行：`run_group` 走 `design → validate → executor → report`，
  `supervise_design`/`supervise_read` 复用了 P1 的候选动作与 `ProductSession` 语义；没有新增第二个
  Agent 循环、第二个覆盖引擎或第二套断言格式。
- 预算：`ProjectBudget.agent_max_turns=16`、`agent_max_tool_calls=24`、`agent_max_model_calls=0`
  （0 = 用任务剩余额度）；`run_agent` 把
  `min(ledger 剩余, 请求值)` 同时传给 `ModelCallLimitMiddleware` 与会话门，两层都不能被绕过。
- 迭代安全阀与产品预算分开：`recursion_limit = max(64, 40 * max_turns)`。第一次真实运行把
  LangGraph 的节点步数当成"轮次"，在第 5 次模型调用就被误判 `TURN_LIMIT`；这条已修（§8.1）。

## 3. 工具调用链与目录校验

8 个工具，全部是产品语义，没有一个是通用文件/终端工具：

| 工具 | 作用 | 是否产生业务请求 |
|---|---|---|
| `get_contract` | 已批准接口、参数、就绪状态 | 否 |
| `get_approved_policies` | 已批准断言/负向策略（含操作者新增的 404 缺失策略） | 否 |
| `get_test_scope` | 4 类判据、主体、分组、候选、预算、完成预览（含 `next_steps`） | 否 |
| `get_test_coverage` | 义务：已满足/未执行/失败/缺前置/未授权/已排除 | 否 |
| `run_test_group` | 设计并执行一个已批准分组（每轮最多 3 次、每组最多 2 次） | 是 |
| `run_open_obligation` | 执行一个服务端决定的候选动作（`design:`/`probe:`） | 视候选 |
| `read_test_report` | 读本轮报告（只读，绝不重发写请求） | 否 |
| `investigate_failure` | 只读复核失败证据、确认/未确认原因 | 否（只读探测） |

- 目录窄化每次运行都重新证明：`verify_catalogue` 要求"缺失/多余"为零；执行器里仍注册着被拒绝的
  默认工具（`ls/read_file/write_file/edit_file/glob/grep/task/execute`），这部分作为
  `executor_only` 上报，不算违规；`verify_enforcement` 用一次真实调用诱捕 `write_file`，实测返回
  `write_file is not available`、`files_written=[]`、`boundary_rejected=true`（真实运行
  functional-02 的 `agent_runtime.enforcement` 就是这么记的）。
- 工具结果统一走 `_bounded_view` + 6 KB 字节上限（按 UTF-8 字节，不是字符）。中文一字三字节，
  P1 的 B1 就是在大结果上把请求撑爆；functional-02 的 `MODEL_TOKEN_BUDGET` 也是这个原因，改成字节
  上限后 functional-03/04 都没有再触发。

## 4. 结构化工具结果（模型看到的是可行动的错误，不是异常）

工具层统一返回 `{ok:false, error:{code, message, retryable, hint, ...}, ...}`，并写进
`tool_calls` 审计。下表是**本次运行里实际出现过的 code**，不是设想：

| 场景 | code（实测来源） | 模型拿到的关键字段 |
|---|---|---|
| 缺前置条件 | `DESIGN_ABSENCE_PRECONDITION_MISSING`（`run_test_group` 的 `design.reason`） | `hint`：先删除同一身份；`retryable=true` |
| 规划 IR 覆盖/绑定失败 | `PLANNING_INSTANCE_COVERAGE`、`PLANNING_INSTANCE_UNBOUND` | `fields`（`instance.ai_3`、`plan.steps.5`） |
| 设计层不可映射 | `MODEL_INVALID_RESPONSE` | 同上；已带 `design_note` 的重试为 `retryable=false` |
| 设计输入预算 | `MODEL_TOKEN_BUDGET` | `retryable=false`，不重试同一请求 |
| 重复无价值测试 | `GROUP_ALREADY_MEASURED` | 需要 `design_note` 才能再跑；每组最多 2 次 |
| 分组次数上限 | `GROUP_RUN_LIMIT` | `retryable=false` |
| 不支持的分组 | `GROUP_UNSUPPORTED` | `retryable=false` |
| 未知分组 | `UNKNOWN_GROUP_ID` | `valid_group_ids` |
| 未知候选 | `UNKNOWN_CANDIDATE_ID` | `valid_candidate_ids`（含 `design:`/`probe:` 前缀） |
| 未知 run_id / 报告不可读 | `UNKNOWN_RUN_ID`、`REPORT_UNAVAILABLE` | `run_ids` |
| 权限/授权变化 | `AUTHORITY_CHANGED` | `retryable=false`，后续动作 `RUNTIME_STOPPED` |
| 取消/截止 | `TASK_CANCELLED`、`TASK_DEADLINE` | `retryable=false` |
| 预算 | `REQUEST_BUDGET_EXHAUSTED`、`MODEL_BUDGET_EXHAUSTED` | `budget` 剩余量 |
| 工具内部异常 | `TOOL_ERROR` | 异常类名 + 200 字，不含 URL/凭据 |
| 已批准断言失败 | **不是错误封套**：`ok=true` + `cases[].outcome=ASSERTION_FAILED` | `cause_confirmed=false`、`next_action` |

`design_id` / `plan_id` 的来源在 P1 里含糊，P2 用三处收紧：`get_test_scope` 返回的
`groups[].id`、`candidates[].id`（`design:<policy>` / `probe:<policy>`）是唯一合法 id 来源；
工具描述写明"必须原样使用 scope 里的 id"；`read_test_report` 只接受本轮 `run_ids` 里的 run_id，
否则 `REPORT_UNAVAILABLE`。实测：模型凭记忆编 `group-99` 时拿到
`UNKNOWN_GROUP_ID + valid_group_ids`（单测 `test_an_invented_identifier_becomes_a_structured_tool_error`）。

**尚未做到的**（见 §8.2 第 6 条）：pydantic 参数校验失败仍由 LangChain 以工具错误文本回传，
没有进入上面这个封套；资源绑定错误目前表现为设计层的 `MODEL_INVALID_RESPONSE` 或预检诊断，
也没有独立 code。这两项是已知缺口，没有在本报告里假装已实现。

## 5. 严格完成判据

`agenttest.task-completion.v1`，四类判据各有自己的义务清单（全部决定性、可回放）：

| 任务类型 | 判据（每条都有 MET / UNMET / EXEMPT + 证据） |
|---|---|
| 功能测试 | `EVIDENCE_PRODUCED`、`RESOURCES_RESOLVED`、`ANOMALIES_EVIDENCED`、`SCOPE_OBLIGATIONS_MEASURED`、`SCOPE_OPERATIONS_EXERCISED`、`WRITE_LIFECYCLE_EXERCISED`、`UNRELATED_SCOPE_DECLARED`、`NO_OPEN_QUESTIONS`、`NEGATIVES_OUT_OF_SCOPE` |
| 修改回归 | `EVIDENCE_PRODUCED`、`SUBJECT_RESOLVED`、`SUBJECT_OBLIGATIONS_MEASURED`、`SUBJECT_DELETE_OBSERVED`、`POST_DELETE_ABSENCE_VERIFIED`、`UNRELATED_SCOPE_DECLARED`、`NO_OPEN_QUESTIONS` |
| 上线前测试 | `ALL_OBLIGATIONS_MEASURED`、`ALL_GROUPS_DECIDED`、`NO_OPEN_QUESTIONS`、`NEGATIVES_OUT_OF_SCOPE` |
| 故障调查 | `EVIDENCE_PRODUCED`、`SUBJECT_REPRODUCED`、`OBSERVATIONS_RECORDED`、`CONSEQUENCE_STATED`、`INVESTIGATION_BOUNDARY` |

- `flags` 把六件事分开：`agent_terminated`、`agent_stop_reason`、`evidence_produced`、
  `obligations_covered`、`task_complete`、`anomalies_found`、`pending_items`。
- `checks_passed` 只回答"已执行的检查是否都通过"；`complete` 回答"本轮承诺的范围内义务是否都被
  真实测量"。已有断言失败但测试确实做完的一轮，可以是
  `complete=true, checks_passed=false`（`test_a_measured_assertion_failure_is_complete_testing_and_a_reported_anomaly`）。
- 缺失断言（404/410）只认"同一身份、先删除成功、再读回"的证据；`product_preconditions.py` 在预检
  和运行后各判一次，不成立的缺失断言既不算通过、也不被改写成业务缺陷。
- 主体识别：原文里的 `operation_id` 拉丁词元优先；中文用 2–4 字 n-gram 匹配已批准
  `summary`/`path`，最长优先，并把命中的词元原样记进判据证据（`subject.basis`）。没有匹配到主体时
  回归/调查类直接 UNMET，功能/上线类退化为"全部就绪接口"而不是静默跳过。
- 负向义务：当 focus 收窄为 `selected` 时，已批准的异常取值检查被明确排除并单独列一条
  `NEGATIVES_OUT_OF_SCOPE`（EXEMPT，附 id 列表）；默认整轮（`all_ready`）里它们仍是必测义务。
  这条是 P2 修正：之前"收窄轮不测负向、却按全部义务判完成"会让完成率永远为假。
- 缺失断言策略的纳入：删除后 404 策略若由本轮的生产者产生，会进入 `approved_scope` 与判据
  （`POST_DELETE_ABSENCE_VERIFIED`），也会出现在设计上下文的 `required_absence_checks` 里。

## 6. 真实 ecnu-max 验证

### 6.1 环境与口径

- 隔离实验室：`scripts/serve_colleague_targets.py`（`parcels` / `reservations` / `directory`，进程内
  loopback，事件文件只记 method/path/query，未记 header）。
- 每次运行独立目录 `.eduwork/runs/p2/<label>/`（`run.json`、`evidence/`、`lab/`、`target.log`），
  独立端口，独立 SQLite 工作区；不使用 `.eduwork/runs/p1/` 的任何数据。
- 运行器：`scripts/run_development_agent.py`（与产品同一条 `TaskService`/`ProductTasks` 路径），
  统一 `--loop-engine DEEP_AGENTS --max-model-calls 16 --max-requests 30 --max-seconds 300`；
  调参运行不计入验收（下文只引用最后一次修正后的运行，早期运行单列为"发现缺陷的运行"）。

### 6.2 四个目标的结果

| 目标 | 最后一次运行 | 模型调用 | token | 真实 HTTP | 终止 | 严格完成 | 关键事实 |
|---|---|---|---|---|---|---|---|
| 帮我测一下包裹管理功能。 | `functional-04` | 14 | 103,802 | 6 | `TURN_LIMIT`（误判，已修） | **false** | 5 个用例；1 个删除-404 计划被预检拒；readAvailability 实测 503 → `ASSERTION_FAILED`；7 项义务测了 4 项 |
| 删除功能刚改过，帮我回归一下。 | `regression-06` | 15 | 116,937 | 2 | `ANSWER` | **false** | create→delete PASS；缺失断言候选被尝试 3 次，全部 `MODEL_INVALID_RESPONSE`；主体 5 项义务测了 2 项 |
| 这个服务准备上线了，帮我测一轮。 | `prerelease-01` | 14 | 102,411 | 14 | `MODEL_ERROR` | **false** | 分组设计先 `MODEL_TOKEN_BUDGET`；监督轮产出 10 个"只建不删"的重复 create 用例，全部 PASS 但 `ALL_GROUPS_DECIDED` 未满足 |
| 包裹接口偶尔报错，帮我看看。 | `investigation-02` | 13 | 99,327 | 0 | `ANSWER` | **false** | 0 请求：两个失败码 `PLANNING_INSTANCE_COVERAGE`（instance.ai_3/ai_4 未覆盖）、`PLANNING_INSTANCE_UNBOUND`（plan.steps.5）；如实报"没有证据" |

四个目标里没有一个是"模型说完成"就算数的：`functional-04` 的最终答复列举了通过项，但判据给出 3 项
未测；`regression-06` 的模型答复明确写了"没测删除后缺失"，这与判据一致，所以没有被记成缺陷。

### 6.3 反馈确实改变了选择

- `functional-02`：模型第一次设计的"删除后再读"计划被预检拒绝
  （`DESIGN_ABSENCE_PRECONDITION_MISSING`，工具结果带 `retryable=true` 与"先删除同一身份"的
  `hint`）。模型随后改用 create→delete 的正常生命周期，拿到 PASS —— 拒绝没有变成假缺陷，也没有被
  重试成重复写。
- `regression-06`：`get_test_coverage.next_steps` 指出
  `POST_DELETE_ABSENCE_VERIFIED → run_open_obligation(design:web_rule_4_absence404)`，模型确实按这个
  候选去执行了三次（不再像 `regression-03` 那样直接收尾）。失败原因是设计层的
  `MODEL_INVALID_RESPONSE`，属于 §8.2。
- `prerelease-01`：`get_test_coverage` 显示 `ALL_GROUPS_DECIDED` 未满足后，模型继续尝试设计分组，
  而不是直接总结；预算耗尽才停。
- 模型不能调工具 / 不调工具时：`test_a_model_that_never_calls_a_tool_produces_no_evidence_and_completes_nothing`
  证明"只说话"的一轮不会产生任何请求、`EVIDENCE_PRODUCED` 为 UNMET、完成判据为 false。

### 6.4 缺失断言与删除后读回违例（真实 HTTP，独立复跑）

`experiments/deepagents_p1/regression.py`（P1 场景，用的是与 P2 运行时相同的
`ProductSession` / `product_preconditions` / `Executor` 语义）在本次实现后重跑，真实 HTTP，退出码 0：

| 场景 | 预检 | 结果 | 含义 |
|---|---|---|---|
| S1 断言不存在但没有删除 | REFUSED `DESIGN_ABSENCE_PRECONDITION_MISSING` | 无请求 | 缺前置不产生假缺陷 |
| S1' 删除后仍期望可读 | REFUSED 同上 | 无请求 | 反向臆造也被拒 |
| S2 删除失败后的回读 | ACCEPTED | `ASSERTION_FAILED` | 缺失去向未成立，不算业务的错 |
| S3 删除成功后的回读 | ACCEPTED | `PASS` | 缺失断言成立路径 |
| **S4 删除声称成功但记录仍在** | ACCEPTED | **`ASSERTION_FAILED`** | **删除后读回违例被检出** |
| S5 字面 id 缺失探针 | ACCEPTED | `NEEDS_REVIEW` | 没有产生者就不给业务结论 |

同一能力在有真实模型的那一轮里只到"发现候选、预检拒绝"这一步：`regression-06` 三次
`run_open_obligation(design:web_rule_4_absence404)` 都停在设计层，因此**"模型自己写出删除→读回用例"
这件事没有被证明**，只证明了"运行时与确定性链路能检出删除后读回违例"。这条按未完成项写进 §8.2，
没有用脚本结果冒充模型结果。

### 6.5 其余对抗场景（离线可复现）

| 场景 | 证据 | 结果 |
|---|---|---|
| 同一失败方案不重复 | `test_the_same_group_is_not_re_executed_without_a_new_design_note`；真实运行里每组最多 2 次 | 第二次要么带 `design_note`，要么被 `GROUP_ALREADY_MEASURED` 拒绝；真实运行没有出现第 3 次 |
| 无价值的重复写 | `prerelease-01` 的 10 个 create 用例是**同一个分组内的重复形状**，`run_test_group` 每组最多 3 次、总请求 14 次，未超过预算，但被记为覆盖质量问题（不是安全违规） | 记为 §8.3 |
| 用户取消 | `test_a_cancelled_task_sends_nothing_and_says_so` | `TASK_CANCELLED`、0 请求、`CANCELLED` 终止 |
| 任务中授权变化 | `test_a_changed_project_identity_stops_the_runtime` | `AUTHORITY_CHANGED`，后续动作全部 `RUNTIME_STOPPED` |
| 模型不会调工具 | §6.3 | 无证据、不完成 |
| 模型预算到达 | `test_the_model_call_limit_ends_the_run_with_the_evidence_it_already_has` | `MODEL_CALL_LIMIT`，已有工具结果保留 |
| 部分测完但目标未达成 | `functional-04`、`regression-06`、`prerelease-01` | `complete=false`，列出未测义务 |
| 未授权工具执行 | 每次真实运行的 `enforcement.boundary_rejected=true`、`files_written=[]`；`verify_catalogue` 缺失/多余为 0 | 0 次 |
| 未知结果写重放 | 运行时只有 `Executor` 出口；取消/超时/失败的分支不重发；`prerelease-01` 的 14 个请求全部是设计内一次执行 | 0 次 |

## 7. 回归与静态检查

- 定向：`tests/test_product_agent.py`（19）、`tests/test_task_completion.py`（14）、
  `tests/test_planning_ambiguities.py`（7）全绿。
- 产品面：`pytest tests -k "product or completion or task_service or workspace or cli or goal or suite"`
  → **454 passed, 2295 deselected**（含旧监督路径、`ProductSession`、设计/预检、报告与 Web/CLI 兼容）。
- 全量离线：`pytest tests` → **2744 passed, 15 skipped, 0 failed**（`deepagents` 在场时也走
  `DETERMINISTIC`/脚本模型，不发真实请求、不需要 API key、不需要网络）。
- `ruff check src tests scripts` → All checks passed。
- GitHub Actions 未触发：本次没有 push（仓库约定见 `AGENTS.md`），因此不声称 CI 通过。

## 8. 未解决的问题（如实记录）

### 8.1 真实运行发现并已修的实现缺陷

| 缺陷 | 表现 | 修法 |
|---|---|---|
| 事件阶段/字段用错 | `TaskStore.event(..., engine=...)` 抛 `TypeError`，任务直接 `INTERRUPTED` | 复用既有阶段 `SUPERVISING`，运行细节只写 summary |
| focus 被拒时崩溃 | `FocusSelection` 缺必填字段 → `ValidationError`，调查目标 0 请求 | 补齐 `question/pending_questions/not_covered`，降级为 `all_ready` 并记 `focus_fallback` |
| 迭代上限当成轮次 | 5 次模型调用就 `TURN_LIMIT` | `recursion_limit = max(64, 40*max_turns)`，真正预算交给 middleware/会话门 |
| IR 歧义过度拒跑 | "帮我回归一下"被规范化 IR 标成 `UNCLASSIFIED_CLAUSE` 等 3 条歧义 → `NEEDS_REVIEW`，回归目标根本没进运行时 | 区分**阻塞性歧义**与设计可解决的歧义（已批准生产者、已批准写操作顺序），后者记 `planning_advisories` 后继续 |
| 缺失断言不在义务里 | 删除后 404 策略不进 `approved_scope`，判据与覆盖互相矛盾 | 由本轮生产者产生的 404/410 策略纳入 `approved_scope` |
| 设计上下文缺缺失断言 | 设计模型看得到 `confirmed_rules` 里 404，却没有"这是一条必须覆盖的普通期望"的说明 | 新增 `required_absence_checks` + 条件性 `required_absence_instruction`（无缺失策略时请求字节数不变） |

### 8.2 未解决（阻塞"严格完成"）

1. **多实例方案的编译器覆盖校验过严**：`investigation-02` 的 5 个实例要求每个都在方案里有对应步骤，
   模型给出的 4 步流程被判 `PLANNING_INSTANCE_COVERAGE`（`instance.ai_3/ai_4`）与
   `PLANNING_INSTANCE_UNBOUND`（`plan.steps.5`）。这不是运行时能修的：要么让编译器接受"一个 flow 用例
   按顺序覆盖多个实例"，要么让设计上下文按实例逐条约束。当前选择是**保持拒绝**（不漏测、不臆造）。
2. **设计模型在监督轮连续返回不可映射方案**：`regression-06` 三次、`investigation-02` 四次
   `MODEL_INVALID_RESPONSE`，每次都是真实模型调用（有 token 记录），没有一次进入执行。需要针对
   "只读缺失断言"这种窄目标单独调提示词或给更小的上下文；本轮只加了一条明确指令，未被采用。
3. **删除后缺失断言在真实模型下没有被写出来**：判据、预检、上下文、候选都到位，模型仍不产出
   "删除→读回 404"的用例。deterministic 链路能检出（S4），模型链路没有。这是 P2 最关键的未达项。
4. **上线前一轮出现重复形状用例**：10 个只创建不删除的用例全部 PASS，既没有删除清理，也没有覆盖
   变更/替换。判据正确判 `ALL_GROUPS_DECIDED` 未满足，但设计层没有"形状去重"约束。
5. **成本**：见 §9，仍显著高于 P1，主要花在 focus + 每次设计/监督上的重复上下文。
6. **两类错误还没有进入统一封套**：工具参数的 pydantic 校验失败仍由 LangChain 以工具错误文本回传；
   资源绑定错误目前表现为 `MODEL_INVALID_RESPONSE` 或预检诊断，没有独立 code。两者都在 §4 的
   "尚未做到的"里列出，下一里程碑应补 `INVALID_ARGUMENTS` 与 `RESOURCE_BINDING_ERROR` 两个封套，
   并把环境失败从 `TOOL_ERROR` 里拆出来。

## 9. 成本对比（同四个目标，含 focus/设计/监督的全部模型调用）

| 目标 | P1 A（旧主循环）调用/token | P1 B（P1 自主循环）调用/token | P2（本次）调用/token/HTTP |
|---|---|---|---|
| 测包裹管理 | 4 / 20,561 | 11 / 35,298 | 14 / 103,802 / 6 |
| 删除回归 | 4 / 21,131 | 8 / 21,937 | 15 / 116,937 / 2 |
| 上线前 | 4 / 19,834 | 20 / 93,166 | 14 / 102,411 / 14 |
| 偶发报错排查 | 4 / 20,716 | 16 / 52,138 | 13 / 99,327 / 0 |

P2 的单次调用成本更高，原因是每次工具结果都会重发、且 focus/设计仍是独立调用；6 KB 字节上限已经把
"结果撑爆请求"压住，但没有改变"多次调用 × 大上下文"的结构。预算没有被打穿：所有运行都在
`--max-model-calls 16` 内自行停止（`prerelease-01` 是 `MODEL_ERROR`，`regression-06` 是 `ANSWER`）。

## 10. 可审计性与清理

- `run.json` 保留：`agent_runtime`（engine/stop_reason/model totals/tool_calls 含 start/done/error 与
  `phase`、enforcement、attempts）、`task_completion`（判据+证据+flags+coverage）、`design_records`、
  `cases`（逐 step 的 policy/attempts/checks）、`usage`、`timings`、`failure_type/failure_detail`。
- 每次真实运行创建的资源都由已批准删除策略清理（`cases[].cleanup_evidence`），`resources.pending=0`；
  `prerelease-01` 的 10 个 create 用例同样逐个清理。
- 实验室事件文件只记 method/path/query，不含 header/凭据；`.eduwork/` 整体在 `.gitignore` 中，
  不进入提交。

## 11. 命令索引（可复现）

```powershell
# 定向与全量
.\.venv\Scripts\python.exe -m pytest tests/test_product_agent.py tests/test_task_completion.py tests/test_planning_ambiguities.py -q
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m ruff check src tests scripts

# 真实运行（四个目标）
.\.venv\Scripts\python.exe scripts\run_development_agent.py --text "帮我测一下包裹管理功能。" --attempt p2-functional-04 --run-dir .eduwork\runs\p2\functional-04 --project-kind parcels --port 18310 --loop-engine DEEP_AGENTS --max-model-calls 16 --max-seconds 300 --max-requests 30
.\.venv\Scripts\python.exe scripts\run_development_agent.py --text "删除功能刚改过，帮我回归一下。" --attempt p2-regression-06 --run-dir .eduwork\runs\p2\regression-06 --project-kind parcels --port 18338 --loop-engine DEEP_AGENTS --max-model-calls 16 --max-requests 30 --absence-approval --delete-noop
.\.venv\Scripts\python.exe scripts\run_development_agent.py --text "这个服务准备上线了，帮我测一轮。" --attempt p2-prerelease-01 --run-dir .eduwork\runs\p2\prerelease-01 --project-kind parcels --port 18330 --loop-engine DEEP_AGENTS --max-model-calls 16 --max-requests 30
.\.venv\Scripts\python.exe scripts\run_development_agent.py --text "包裹接口偶尔报错，帮我看看。" --attempt p2-investigation-02 --run-dir .eduwork\runs\p2\investigation-02 --project-kind parcels --port 18354 --loop-engine DEEP_AGENTS --max-model-calls 16 --max-requests 30 --delete-fault-status 503

# 缺失断言 S1–S5（真实 HTTP）
.\.venv\Scripts\python.exe experiments\deepagents_p1\regression.py --out .eduwork\runs\p2\regression-s1-s5.json --port 18346
```

## 12. 结论

**工程目标达成**：Deep Agents 是产品唯一的自主 Agent 运行时；四个入口共用同一条确定性链路；完成判据
按任务类型给出、可审计、不采用模型自述；错误以结构化结果反馈给模型并真实改变后续选择；预算、取消、
授权变化、未知结果不重放都有实测或离线证据；基础安装不被新依赖破坏。

**产品目标未达成**：严格完成 0/4，且失败点集中在规划/设计层（§8.2），不是运行时实现。要把 P2 变成
"可交付的产品默认"，下一步应先解决"多实例 flow 覆盖"与"窄目标设计的稳定产出"，再把删除后缺失断言
的真实模型路径跑通；在那之前，保留 `LEGACY_SUPERVISOR` 与 `DETERMINISTIC` 作为降级路径是必要的，
不能对外宣称"任务完成率已提升"。
