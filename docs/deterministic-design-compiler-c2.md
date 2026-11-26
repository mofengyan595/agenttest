# C2 单义务确定性 Design 接管

## 适用范围

Deep Agents 仍选择目标、已批准检查及下一步。C2 只接管已请求且单独成组的 `required_missing` 请求体负向 POST。可信 Planning IR 须有一个绑定至批准基线策略的实例。该实例的身份明确，每个基线字段都能从经 Focus 验证的原文或 IR 中的批准单值规则确定。项目须启用原生持久化与 HITL，且批准了使用同身份、同关联键的 POST 前后只读观察。不存在外部资源输入、路径绑定、额外前置步骤或托管登录。

设置 `AGENTTEST_C2_REQUIRED_MISSING_PROJECT` 为一个项目 ID 可启用候选，默认关闭。任务提交时固定协议 `required_missing_observed_post_v1`，随后切换环境变量时原任务仍沿用已固定协议。旧任务没有该标记时走原路径。此次未调整正式运行配置。

## 工具调用与回退

1. `run_test_group` 继续接收 `group_id`。`run_open_obligation` 继续接收 `candidate_id`，已有获批负向候选转交所属组。
2. 原工具先核对候选归属、已有覆盖和预算。`ProductTasks.run_group` 读取可信上下文，候选适用时通过原 `construct_plan` 生成 `TestDesign`，直接进入原预检。应用候选的路径连 Design client 都无需构造。
3. 预检按原规则加上观察、运行 Validator 并检查清理依赖。HITL 固定批准摘要和预检计划摘要，恢复复核二者后才派发。原 Workflow、Executor 和 HTTP 步骤账本记录业务结果。无副作用证明使用前后同键零计数回执。
4. 开放字段、歧义来源、资源依赖、额外用户约束或策略不足时，沿原路径处理开放槽、Design 或人工待审。C2 对明确不匹配的角色和检测到的凭据泄露直接拒绝。接管后预检失败显示原诊断，不以另一个可执行候选替代。

`design_records` 区分 `APPLIED`、`FALLBACK` 和 `REJECTED`，同时记录协议、策略、采用或回退原因、Design 调用数及适用时的预检结果。工具沿用分组、用例、请求数、失败断言和清理字段，有接管记录时投影至 `design.compilation`。等待审批仍为原生中断，工具异常继续走 `TOOL_ERROR`。申请、派发和断言通过均以原各层证据判定，覆盖和完成条件不变。

## 两种契约的实际入口

真实 `TaskService` 提交任务，脚本化 Deep Agents 选择工具。原路径用冻结替身返回合法 Design，经实际编码和解码。C2 以已验证的 Planning IR 和批准策略构造同一类方案。两组测试项目均单独固定合法基线的单值规则来证明唯一性，包裹使用既有接入脚手架，资产登记复用冻结 OpenAPI 3.1 契约。这是产品入口的定向配对检验。[原三臂 PoC](contract-tool-reuse-poc.md)

| 契约与切片 | 工具 | 内层 Design 次数 | Design 请求字节 | HTTP 派发次序 | 结果 |
| --- | --- | --- | --- | --- | --- |
| OpenAPI 3.0.3 包裹，新增请求缺 `quantity` | `run_test_group` | 1 → 0 | 26,651 → 0 | 每臂 GET → POST → GET | 每臂 PASS |
| OpenAPI 3.1 资产登记，登记请求缺 `title` | `run_open_obligation` | 1 → 0 | 25,168 → 0 | 每臂 GET → POST → GET | 每臂 PASS |

核对两臂的计划名、步骤、策略 ID、角色、请求字段和实际负向变化。参数按槽位比较，预检为不同任务各自生成关联键。两臂的场景指纹一致，每次执行的两次观察和业务请求使用本次任务的同一个关联键。实际执行策略、断言结果、覆盖和任务完成状态一致。两条候选路径各省一次内层 Design，每条仍按原规则保留 Focus 和自主工具调用。

| 控制 | 观察到的结果 | 内存业务派发 |
| --- | --- | --- |
| 未赋值的基线输入 | `OPEN_BASELINE_INPUT_SLOT`，原 Design 一次，通过原审批后执行 | 3 |
| 等待审批或拒绝 | 已编译并预检，审批前与拒绝后保持零派发 | 0 |
| 预算只剩两步 | `BATCH_REQUEST_BUDGET`，无 Design | 0 |
| 相同 HTTP 422，错误业务码 | `ASSERTION_FAILED`，后观察未执行 | 2 |
| 相同关联键的后观察返回非零 | `ASSERTION_FAILED`，保留 `CREATION_UNPROVEN`，原诊断额外做一次同键只读观察 | 4 |
| 已有所有组义务的执行证据 | `GROUP_ALREADY_MEASURED` | 0 次额外派发 |
| POST 派发后回执丢失 | 保留 `DISPATCHED`，恢复返回 `DURABLE_WRITE_OUTCOME_REQUIRES_REVIEW` | 首次 2，重放 0 |
| IR 身份冲突 | `POLICY_SCOPE_MISMATCH`，无审批、无 Design | 0 |
| IR 含不可用的资源来源 | C2 回退，原 Design 拒绝未授权引用，最终待审 | 0 |
| 资产登记 B04，更新依赖已批准生产者 | C2 回退，原 Design 一次。POST 返回资源 ID，PATCH、DELETE、GET 404 使用同一 ID 与身份，清理已证明 | 4 |

C2 入口定向测试 **11 passed**，含四个配对任务。本组 27 次 MockTransport 业务派发，真实模型和真实网络 HTTP 均为零。相关 Design、计划构造与 Agent 工具测试 **73 passed**，C1.1 入口等价与任务协议固定对照 **8 passed**。本切片以同关联键前后观察证明失败 POST 的无副作用，实际创建后的同资源清理由 B04 原路径复验。针对 B04 的结果仅作为 C2 回退控制，不计入原 PoC 的冻结评分。入口数据库和跨任务计数收据位于本地 `.eduwork/c2-delivery/`。

使用现有项目 `.venv` 运行入口、Design、计划构造与 Agent 工具测试。

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q tests/test_product_design_compiler.py tests/test_product_design.py tests/test_plan_construction.py tests/test_product_agent.py
.\.venv\Scripts\ruff.exe check
git diff --check
```

## 后续范围

把 C2 作为默认关闭的独立候选评审。下一切片优先验证可信基线中的单一开放输入槽，先冻结旧路径与候选在同资源绑定、身份、审批摘要和清理回执上的比较口径，再考虑扩展到依赖生产者的负向更新。Schemathesis 继续用于离线对照，选型专项结题。P3-B2.8 维持 **0/5、NO-GO** 和 **4/5** 门槛，C1.2 未完成，C1.1 默认关闭。本轮证据限于离线。

## 下次独立交付的拟提交清单

当前工作区含多轮成果。下列分组以 `bec965f` 为基线，按功能审阅，后续提交应保持文件引用和测试依赖闭合。

| 交付分组 | 文件 | 审阅重点 |
| --- | --- | --- |
| 负向请求上下文修复 | `executor.py`、`negative.py`、`test_approved_negative.py`、`test_negative_values.py`、[修复报告](negative-request-context-fix.md) | 先独立交付。私有测试替身已接受新增的关键字参数，生产变更局限于共享已解析 Header。 |
| C2 单义务候选 | `product_design_compiler.py`、`product_agent.py`、`product_tasks.py` 中 C2 改动、`test_product_design_compiler.py`、本报告 | 依赖 Header 修复。资产用例复用 PoC 脚本及 `assets.json`、`positions.json`、`oracle.json`，需随同交付或在 PoC 分组之后交付。审阅默认开关、计划摘要和失败回退。 |
| C1 影子及 C1.1 候选 | `product_checks.py`、`product_focus.py`、`product_focus_wire.py`、`focus_compiler.py`、`product_tasks.py` 中 Focus 改动、`replay_focus_compiler_c1.py`、两份 Focus fixtures、`test_focus_compiler.py`、`test_focus_takeover.py`、C1/C1.1/C1.2 报告 | 同一任务上下文的差异块同时含 C1.1 与 C2，需要按功能拆分。保留已冻结分母、默认关闭和未完成的真实验证。 |
| 产品演示、历史验收和 Web | `README.md`、`deepagents-model-reliability-p3b28.md`、`product-demo-and-delivery-baseline.md`、`web/src/api/tasks.ts`、`GoalTasks.tsx` 和对应测试 | 历史证据与 Web 中断显示可单验。保留本地归档与仓库内报告的区别，避免把历史 CI 归于当前工作树。 |
| 选型及路线 | `AGENTS.md`、`ROADMAP.md`、`progress.md`、`architecture-and-roadmap-review.md`、`build-vs-buy-and-tooling-review.md`、`contract-tool-reuse-poc.md`、`contract_tool_reuse_poc.py`、`tests/fixtures/contract-tool-reuse-poc/` | `progress.md` 与路线图包含多阶段记录，可随分组分别加入。归档及版本环境留在本地，交付前核对报告引用的证据与可复现范围。 |
