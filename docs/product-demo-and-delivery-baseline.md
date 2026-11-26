# AgentTest 产品演示与交付基线

2026-11-24 · 起始提交 `bec965fba0d7f5c039812cf94d7567b765747af3`

本轮验证四段产品展示。Web 使用现有无密钥 Demo，跨进程故障注入使用独立的本机 HTTP 靶场和脚本化模型。场景 A 的接入和双角色检查在 Web 工单 Demo 完成，资源创建及清理证明在包裹靶场完成。两段共用现有契约、策略、执行器与证据机制，目标和运行记录各自独立。

P3-B2.8 仍为 **0/5、NO-GO**，五轮安全审计通过，原发布门槛 4/5。历史第三次冻结为 3087 passed、15 skipped、1 xfailed、0 failed，对应运行时提交 CI 7/7。产品展示验收和高级可靠性研究各有独立里程碑。历史评分、失败、验收归档和架构复盘均保留。[架构复盘](architecture-and-roadmap-review.md) · [真实验收与冻结](deepagents-model-reliability-p3b28.md)

## 启动与接入

使用已安装的项目 `.venv` 和 Web 依赖。首次安装见 README 的快速开始。以下命令从仓库根目录执行。用新数据目录重复演示可保留已有工作台历史。[快速开始](../README.md#快速开始)

```powershell
Push-Location web
npm run build
Pop-Location
.\scripts\start_web.ps1 -Demo -Port 18012 -Data runs/product-demo-local
```

Linux 可使用 `.venv/bin/python scripts/serve_web_workspace.py --demo --port 18012 --data runs/product-demo-local`。在浏览器打开 [本地工作台](http://127.0.0.1:18012/)，确认配置状态为 `SIMULATED`。如需保存本轮故障注入证据，可在另一个终端创建新的输出父目录。

```powershell
$demoEvidence = Join-Path runs ('product-demo-local-' + (Get-Date -Format yyyyMMdd-HHmmss))
New-Item -ItemType Directory -Path $demoEvidence | Out-Null
```

在项目页填写名称并选择工单 OpenAPI，选现有工单服务端授权配置。展开环境、身份及业务范围，核对 `owner`、`visitor` 与创建、关闭、自己的工单可读、其他用户读取拒绝等已批准预期，勾选确认并保存。Demo 使用合成身份与进程内目标。一般项目仍要先确认实际隔离部署、OpenAPI 覆盖、身份、资源绑定、预期和写入授权。[工单 OpenAPI](../examples/trusted-testing/projects/tickets/openapi.json) · [Web 接入指南](web-intake-guide.md)

## 四个展示场景

### A · 正常 API 测试与资源清理

1. 在新建工单项目点击「开始一轮测试」，输入 `创建工单，关闭工单，查询工单，其他用户不能读取工单`，点击「开始测试」。
2. 结果中打开「查看业务、模型与规则证据」。实测 `FINISHED`、`task_satisfied=true`、`PASS`。四次内存接口调用依次为创建 201、关闭 200、同角色读 200、访客读 403，17 条断言全部通过。回归包 `scenario.json` 中，后面三步的 `ticket_key` 均绑定创建步骤 `fixture_1` 的 `$.payload.ticket_key`；前两次后续动作使用 `owner`，访客读使用 `visitor`。这次运行调用了一次本地模拟规划器，真实模型调用为 0。关闭工单是业务状态变更，物理清理通过下方独立靶场证明。[浏览器对账](../runs/product-demo-baseline-20261124/browser/result.json) · [正常报告](../runs/product-demo-baseline-20261124/web-browser/reports/d7caba83324a411daf456bf7b9dc769c/report.json) · [桌面截图](../runs/product-demo-baseline-20261124/browser/normal-desktop.png)
3. 执行场景 D 中的 `PRODUCT_RECOVERY`。实测在 POST 201 可信回执写入后中断，原任务恢复只执行同一 ID 的 DELETE 204 和 GET 404。三个计划步骤均有证据，最终 `PASS`，靶场库存为空。包裹测试自身也从批准的契约及写入配置建立项目；用例固定 `$.id` 的前序绑定。展示时将工作台的角色与断言链、隔离靶场的同资源清理各自标明。[生命周期正常报告](../runs/product-demo-baseline-20261124/lifecycle-local/test_process_exit_at_execution0/reports/b035a367b606b60367326279f41894ba/report.json) · [故障注入测试](../tests/test_step_recovery.py)

### B · 假删除的断言失败

执行下列选定测试。故障注入在仅监听 `127.0.0.1` 的临时靶场运行，DELETE 返回 204 且保留资源。测试随后从可信 DELETE 回执恢复，用创建步骤的 ID 读回。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest -q -rA --basetemp="$demoEvidence/fault" `
  'tests/test_step_recovery.py::test_process_exit_at_execution_boundary[RESPONSE_RECORDED-DELETE-True-True-2]'
```

| 证据维度 | 本次实际结果 |
| --- | --- |
| 执行闭环 | 报告 `workflow_completed=true`，计划 3 步均有实际记录。用例执行预设场景，`task_satisfied` 未评定。 |
| 业务断言 | `ASSERTION_FAILED`。末步可信预期为 404，实际为 200。pytest 通过表示成功检出了注入故障。 |
| HTTP | `POST 201 → DELETE 204 → GET 200`，后两步使用创建资源的同一 ID。 |
| 未覆盖 | 该计划未执行步骤 0/3；涵盖范围限于此生命周期。工作流结束和断言通过分别显示。 |
| 清理 | 同资源回读仍存在，靶场库存有残留，无法确认清理。DELETE 204 保留为请求事实。 |

打开输出目录下 `reports/<run_id>/report.json`，核对最后一步的 `attempts`、`checks` 和 `plan_structure`。也可在单独的 Demo 数据目录把整个该 run 报告目录复制到 `reports/`，启动后打开 `/runs/<run_id>` 查看 Web 证据页。复制只用于呈现原有报告，不触发测试。生产报告页根据场景工件显示 HTTP 及断言，工单 Demo 和底层报告自身没有审批任务所拥有的完整清理账本。[假删除报告](../runs/product-demo-baseline-20261124/lifecycle-local/test_process_exit_at_execution1/reports/ffffffffffffffffffffffffffffffff/report.json) · [检查步骤与库存的断言](../tests/test_step_recovery.py#L386)

### C · 固定 pytest 回归

在 A 的通过结果点击「导出 pytest 回归包」，再到「已保存测试」点击「再次运行固定测试」。浏览器实际下载了 ZIP。固定任务使用同一 `scenario_sha256`，四步接口及每步通过的断言数均与来源一致，17 条断言通过。固定任务 `model_source=DISABLED`、`model_calls=0`、`task_satisfied=null`。包内固定步骤仍指向 `fixture_1` 的 `$.payload.ticket_key`；目标、策略及步骤策略由运行时按 `manifest.json` 核对。界面将固定执行显示为「固定回归检查通过」。[ZIP](../runs/product-demo-baseline-20261124/browser/ticket-regression.zip) · [固定报告](../runs/product-demo-baseline-20261124/web-browser/reports/5e40229130c44e8b84f808c16d6b9651/report.json) · [窄屏截图](../runs/product-demo-baseline-20261124/browser/fixed-mobile.png)

独立命令运行仓库已有三个固定工单包和配对故障。实测正常 3 passed、故障 3 failed（验收脚本预期故障退出码 1 并核对每个失败的原检查），总验收通过。另有现有定向测试对两种新登记项目分别导出、将模型入口替换为失败探针，再以原策略执行固定资产，含 CLI 成功可导出和失败拒绝导出，共 3 passed。[配对结果](../runs/product-demo-baseline-20261124/fixed-paired/results.json) · [定向 pytest 回执](../runs/product-demo-baseline-20261124/intake-local.xml)

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/check_regression_exports.py `
  --output "$demoEvidence/fixed-paired"
```

刚下载的 ZIP 在解包后逐文件与服务器导出件核对，使用 Demo 配置副本和一次性 `agenttest_regression_runtime` fixture 执行包内 pytest，实测 **1 passed**。报告为 `operator_plan`，规划调用 0 次，4 步、17 条断言通过，场景摘要与来源一致。已有本轮夹具和解包文件时，可从仓库根目录运行下方命令。重新生成 ZIP 时，需用新运行对应的批准配置和 TaskContract 提供 fixture。当前导出范围是普通 JSON，CSV 上传、轮询和 `read_retries` 尚未支持。[刚下载的包执行回执](../runs/product-demo-baseline-20261124/downloaded-pytest/result.xml) · [本轮 fixture](../runs/product-demo-baseline-20261124/downloaded-pytest/conftest.py) · [重放报告](../runs/product-demo-baseline-20261124/downloaded-pytest/execution/test_agenttest_regression0/reports/4778b6ac9c0741dda2233670ba40add2/report.json) · [导出条件](regression-export.md)

```powershell
$unpacked = 'runs/product-demo-baseline-20261124/downloaded-pytest/demo-copy/reports/exports'
.\.venv\Scripts\python.exe -X utf8 -m pytest -q --basetemp="$demoEvidence/downloaded" $unpacked
```

### D · 审批与恢复证据

使用现有跨进程测试，脚本化工具调用经过原 Deep Agents、SQLite Checkpoint、任务服务和 Executor。PowerShell 需要允许本机回环连接；测试正常的禁网 fixture 仍在。运行下列用例会建立独立靶场和任务，不读取网关配置。

```powershell
$recoveryCases = @(
  'PRODUCT_RECOVERY-POST-False-True-1',
  'HITL_APPROVE-POST-False-True-0',
  'HITL_REJECT-POST-False-False-0',
  'SERVER_EFFECT-POST-False-False-1',
  'HITL_UNKNOWN_DELETE-DELETE-True-True-0'
) | ForEach-Object { "tests/test_step_recovery.py::test_process_exit_at_execution_boundary[$_]" }
.\.venv\Scripts\python.exe -X utf8 -m pytest -q -rA --basetemp="$demoEvidence/recovery" `
  @recoveryCases tests/test_human_review.py
```

| 用例 | 本次实际结果 |
| --- | --- |
| `HITL_APPROVE` | 原计划预检后暂停，批准前 0 HTTP。决定重复提交保持同一结果；决定后进程退出，再次启动按同计划派发 3 次，`201 → 204 → 404`，最终 `PASS` 和库存为空。 |
| `HITL_REJECT` | 拒绝后 `NEEDS_REVIEW`，任务未完成，业务 HTTP 为 0。 |
| `PRODUCT_RECOVERY` | POST 可信回执后中断，恢复只发 DELETE 和 GET，整个任务账本总计 3 次，未重新请求 Design。 |
| `SERVER_EFFECT` | POST 已造成一次靶场写入，可信响应未保存。账本保持 `DISPATCHED`，续跑返回 `DURABLE_WRITE_OUTCOME_REQUIRES_REVIEW`，没有再次发 POST。 |
| `HITL_UNKNOWN_DELETE` | DELETE 后响应未知；获准核实只新增一次同身份 GET，返回 200，记为 `RESOURCE_STILL_PRESENT`。原写结果为 `UNKNOWN_OUTCOME`，恢复资格为 false，未重放 DELETE。 |
| 审批接口 | 三个现有测试通过，覆盖所有者、CSRF、决定类型、版本与竞争转换。 |

正式定向命令还包含 B 的假删除用例，共 **9 passed**。测试 SQLite、报告及通过记录保存在本轮目录。常规 Demo 不启用持久化 HITL，D 用现有故障注入展示后端能力。[逐项终态](../runs/product-demo-baseline-20261124/lifecycle-local.xml) · [步骤和审批实现](../tests/test_step_recovery.py) · [审批接口用例](../tests/test_human_review.py)

## Web 现状与本轮修正

| 使用时要知道的问题 | 核查结果 |
| --- | --- |
| 当前状态与完成范围 | 工作台有状态、阶段、已执行请求、未执行步骤和结果分项；报告中可分别查看 workflow、任务满足、预期和实测。部分细项在折叠区，使用时由任务结果进入报告。任务停机、失败和未覆盖能同时保留。 |
| 业务失败与证据 | 场景可链接到报告的请求和断言。报告脱敏，仅保存允许公开的状态与字段证据。原资源 ID 值和凭据不作为普通页面资料展示，绑定从场景配置及回执关系核对。 |
| 固定回归 | 合格结果可导出，已保存测试可再次运行。下载包的 fixture 仍需操作员提供，页面按现有门禁显示导出资格及原因。浏览器实测证明入口可达。 |
| 中断与复核 | 旧页面只给通用中断提示。新增只读调用已有 `GET /api/tasks/{id}/recovery`，显示后端给出的恢复资格、等待所有者审批、未知写入待复核、用量待核对或配置漂移等结果。请求失败显示无法核对，未推定可恢复。审批暂停点需重建时仍提示保留审批。 |
| 仍缺的 Web 能力 | 无持久化审批列表、计划摘要与批准/拒绝/未知写归档操作，也没有原任务恢复按钮。旧的 `WAITING_REVIEW` 是另一种策略审阅入口。待审详情和恢复交互留给独立 Web 切片，复用现有 API 和新验收协议。D 的真实后端链路使用隔离测试，恢复提示通过前端定向用例覆盖。 |

只修改 Web 任务客户端、结果页和相应定向用例。`GoalTasks.test.tsx` 共 17 passed，其中 5 项覆盖恢复状态显示；TypeScript 与 Vite 构建通过。真实 Edge 对 Demo 完成接入、查看报告、下载、固定运行和 390px 检查，浏览器页面异常为 0。Ruff 与 `git diff --check` 通过。初次受限运行的回环连接被 Windows 拒绝，ASGI 定向首次还因 `--basetemp` 父目录缺失而在夹具初始化前退出。保留这些运行与正式通过证据的区分，完成后者时使用可用的本地回环环境和新输出路径。[Web 改动](../web/src/pages/GoalTasks.tsx) · [Web 定向用例](../web/src/pages/GoalTasks.test.tsx)

无真实模型可演示契约接入、采用合成模型或固定资产的执行、报告、可信断言、固定回归、故障注入下的审批和续跑。面对任意新自然语言任务，正常配置的产品可能调用 Focus、Design 和 Deep Agents，仍受模型配置、可信策略、授权、预算与运行状态约束。单次 Demo、固定回归或仿真模型输出没有验证开放任务的实时规划质量。复杂发布保留为实验能力，当前无稳定发布承诺。

## 下一阶段 C1 · 确定性 RequestedCheck 影子编译

C1 先只从**显式选定或现有可信规则唯一证明的范围**和冻结批准目录生成候选检查及来源见证。模型输出、任务范围、正式派发与评分在影子验证期间继续使用原路径。本轮只设计接点。

| 入口 | 当前行为和建议切口 |
| --- | --- |
| `product_checks.py` | `check_index` 组织可信负向目录，`missing_requested_checks` 在封闭整项目分支能列出缺失的操作、检查和来源。优先将这段纯候选推导提取为可独立测试的输入输出，复用 `validate_requested_checks`、`partition_requested_checks`。后两者分别验证意图/来源与精确值的额外授权。[检查模型及目录](../src/agenttest/product_checks.py#L29) · [缺项与校验](../src/agenttest/product_checks.py#L172) |
| `product_focus.py`、`product_focus_wire.py` | Focus 根据选中的 operation bundle 解码 `check_ref` 为批准策略 ID，`source_ref` 从当前上下文解析原文并做范围校验。影子比较可以在解码边界后进行，由相同冻结上下文推导预期映射。两份结果各保留，合法已有映射不因只接受一种等价 `source_ref` 被误判。[Focus 解码](../src/agenttest/product_focus.py#L531) · [wire 解码](../src/agenttest/product_focus_wire.py#L132) · [来源还原](../src/agenttest/product_sources.py#L306) |
| `task_completion.py` | `project_variant_scope_sources` 只识别有唯一锚点的整项目变体范围，保留普通/未来与开放表达的差异。先用此规则或明确的结构化范围做正例，不依据任务最近的异常文字扩大范围。[范围识别](../src/agenttest/task_completion.py#L192) |
| `product_tasks.py`、Planning IR | Focus 结果进入 `request_planning_ir`，并将 requested checks 划为可执行与待审，后续分组、覆盖与 Design 依据同一批批准项。影子结果在此边界旁路记录，仅对照原链的义务、前置实例和分母。C1 不改 IR、编译器或后续消费口径，编译正式接管需另行验收。[任务 IR 入口](../src/agenttest/product_tasks.py#L58) · [划分授权值](../src/agenttest/product_tasks.py#L850) · [IR 构造](../src/agenttest/planning_ir.py#L457) |

1. **可编译字段。** 由批准目录及范围交集确定 `policy_id`、对应 `operation_id`、`check_ref`、项目快照和身份授权线索。索引内唯一可验证来源可生成 `source_ref`、`source_quote` 的绑定。仅泛指按批准负向规则时，`exact_value`、`exact_length` 保持缺省含义，实际变体由批准策略提供。现有 `RequestedCheck` 需要真原文；对于只存在 UI 结构化选择、没有可验证原文的情形，影子结果保留选择记录 ID、项目版本与操作员确认的来源类型，暂时不合成 `source_quote` 或送进正式 `RequestedCheck` 路径。
2. **继续交给模型或操作员的部分。** 开放语言的范围、同名接口、角色指代、跨轮撤回、未来动作、未批准业务预期和多个可行方案的取舍。选中一个接口也要有明确的变体范围，未指定负向时不推导负向覆盖授权。出现冲突时产出待审原因，偏离已确认范围的模型提案继续拒绝。
3. **权限与精确值。** 与冻结项目版本、目标操作白名单、批准策略、角色就绪和 pending checks 取交集，检查策略和引用唯一。原文精确字面量/长度需能定位到唯一来源、操作和槽位，并通过原解析与额外授权检查。无法唯一确定就待审，避免用默认负向值替换用户明确输入。前置生产者或清理依赖由现有 IR/Policy 验证，源校验通过仍需权限、预算、预检及 HITL。
4. **影子等价。** 使用归档 Focus 输入、Mock 和新夹具，双路输出同一批准目录下的 `(operation_id, policy_id, role, source, exact_value, exact_length)` 及未决原因，按来源经验证后可等价的记录做归一化。再核对可执行/待审分区、冻结义务归属和请求级分母。保留已知缺映射的历史模型输出，标明影子与历史差异；以独立冻结预期评判影子，不把历史评分重算为通过。记录预计省去的模型位置及请求体规模，正式调用及写入为零。
5. **留出与反硬编码。** 使用第二种契约的工单域或一份新的合成只读契约，冻结不同的 operation ID、角色、路径参数、批准负向种类和检查数量。以项目 A 的样本开发，在留出上检验显式子范围、未来限定、同名身份、来源冲突、字面量、不可用角色、零负向规则。将相同语义的候选改名、重排、增删批准策略，输出须随目录按唯一规则改变，且无固定 31 或 24、parcels 操作名、历史 Policy ID 和单条测试文本依赖。建议放行原型的最低条件为已登记留出正例全等价、负例全保守拒绝或待审，并且至少一种封闭范围能省去模型逐条转写。

## 本轮停点

产品演示已取得浏览器与本机隔离证据。本轮真实模型、外部业务 HTTP、新一轮五轮验收和全量冻结回归均为 0。无执行、预算、安全、审批、恢复或评分协议变更，无提交推送。C1 正式接管、P3-B2.9 与完整 P3-B3 均未启动。
