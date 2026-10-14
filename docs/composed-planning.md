# 组合任务规划评测入口

组合功能之后，本阶段补充的是模型规划评测运行器、预算、失败归档和独立审核准备。原执行引擎及正式 Eval v2 不改。默认 Fake／Mock；真实调用必须另外满足额度授权、新任务独立审核、审核锁、干净已提交代码和准确准备方案。

## 默认离线复现

使用项目已有 `.venv` 及 `agent` 可选依赖，不安装新依赖、不读取 `.env`。下列三条仅用显式固定响应验证机制，支持任务两条、拒绝任务一条；输出目录及文件均拒绝覆盖。

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_composed.py fixtures --suite examples/composed-development.json --case P001 --case P005 --case P009 --source mock_transport --output runs/planning-demo/fixtures.json
.\.venv\Scripts\python.exe scripts/evaluate_composed.py run --suite examples/composed-development.json --case P001 --case P005 --case P009 --source mock_transport --responses runs/planning-demo/fixtures.json --max-calls 3 --max-total-tokens 72288 --output runs/planning-demo/mock
.\.venv\Scripts\python.exe scripts/evaluate_composed.py verify --run-dir runs/planning-demo/mock
```

Linux 使用 `.venv/bin/python`。要验证 Fake，把生成和运行的 `--source` 同时换成 `fake`，选择新的响应文件和输出目录。

`fixtures` 是单独、显式读取标签的固定基线生成器。`run` 不调用基线生成器，不按标签补计划，不用固定响应兜底真实模型；Fake／Mock 必须明确提供所选全部题目的响应文件。Mock 通过原结构化模型适配器和 HTTPX MockTransport；业务执行始终使用进程内 mini target。所有任务看到相同目录，只有任务文本改变。

离线成功只能说明适配、约束、归档和评估机制符合这些固定响应，不能证明真实模型规划效果。Mock 的 usage 明确标为 `SIMULATED`；Fake 不伪造 Token。`real_model_calls` 独立记录，不能把 Mock 的 HTTP 调用次数当作收费服务调用。

## 预算与失败证据

默认最多八次规划尝试；每次最多 20,000 输入 Token、4,096 输出 Token，按 24,096 保留，合计最多 192,768。输入上界沿用原适配器的 UTF-8 字节加边距估计。保留额只用于阻止超额新调用，不是实际计费 Token 或金额；真实消耗以服务返回的 usage 为证据。没有查价或估计人民币成本。

| 情况 | 处理 |
| --- | --- |
| 调用数或保留 Token 不足 | 不再规划；后续题保持 NOT_ATTEMPTED_BUDGET，仍在总分母内 |
| 服务错误、格式错误或未知 usage | 保存失败报告和诊断，按停止规则保留未执行题；不重试、不返还未知消耗 |
| 合法 DSL 不满足目标 | 原 Policy／Intent 拒绝或独立评分不通过，不修补计划或断言 |
| 显式空计划／模型 refusal | 只有题目确应拒绝、零业务请求且 Workflow 正常结束，才算模型主动拒绝 |
| 策略或意图门禁阻止错误计划 | 单独计安全门禁拒绝，不给模型主动拒绝记分 |
| 源码、任务、策略、响应文件或冻结记录改变 | 规划前及业务执行前阻断；不能沿用旧准备方案继续调用 |
| 取消 | 保留调用前预算日志及 Workflow 取消报告，尽力写 abort.json；不生成成功汇总或封印 |

每个题目在调用前写入保留日志；失败、未执行和成功均有逐项记录。汇总保留请求、尝试、支持题、拒绝题等完整分母，并区分 DSL／意图证据、需求满足、模型主动拒绝、门禁拒绝和来源。每次只规划一次，没有自动修复、重规划或写入重放。

`run` 全部符合标签且未停止时退出 0；存在失败或预算停止时退出 1，仍保留有效评测归档；配置或归档错误退出 2。不能仅凭文件生成或命令执行结束宣称模型任务成功。

## 独立任务和审核

已有 P001–P012 是已用开发草案，继续保持 UNREVIEWED，不升级为留出集。T／B／C 的任务和成绩也不改。真实试验需要未参与开发的审阅者提供或确认新任务、标签、原始来源及采样依据；机器只能核对记录和摘要，不能认证真实身份或语义独立性。

用户可以确认业务预期，Codex 可以检查任务、标签、代码与策略是否一致；这属于开发验收。参与实现的用户或 Codex 均不能据此填写“未参与开发”或“已确认独立性”。本轮按用户选择先完成静态复核和离线验收，保留 UNREVIEWED／DEVELOPMENT_ONLY，不生成独立人工审核记录，也不放宽真实执行入口。

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_composed.py review-template --output runs/planning-review/enrollment.json
# 审阅者提供新任务及初步标签后，可预填摘要；仍不填写人名、时间、独立性或批准。
.\.venv\Scripts\python.exe scripts/evaluate_composed.py review-template --suite runs/planning-review/new-tasks.json --output runs/planning-review/packet.json
```

新 Suite 格式为 `agenttest.composition.planning.v1`，包含 `dataset_version`、`split` 和 `cases`。每题保留 `case_id`、`user_task`、`expected`／`rejection_code`、`review_status`、`label_provenance`、`review_record`；期望字段沿用[组合任务目标](composed-workflows.md)。完整字段及当前契约、Policy、TargetConfig 和 fixture 摘要均在模板中。只有真实审核完成后才由操作员填写 HUMAN_REVIEWED／independent_human；本阶段不生成此类真实记录。

审核要求包括：未参与开发、操作员确认独立性、带时区的采样与审核时间、来源引用及内容摘要、任务和标签摘要、正确的执行／拒绝决定、可接受的偏序及禁止的额外动作，拒绝题还要有明确依据。静态预检验证标签对应的安全路径，不执行 HTTP。与旧开发任务完全相同或仅格式不同的题会拒绝；语义重叠仍须人工判断。

审核确认后，`review-freeze --suite <审核后的文件> --output <审核锁目录>` 按 dataset_version 独占写入 `<版本>.json`，同目录内同版本不能覆盖；执行时也检查锁文件名与版本一致。任务、标签、策略或记录改变都需新版本、新审核，不自动补缺失字段。

## 真实调用前的准备

`prepare` 不接受模型 Key 或 `--real`，不会打开网络连接；生成公开任务上下文、准确请求摘要、源码身份、模型配置和调用上限。输出始终 `operator_authorized=false`、`ready_for_paid_run=false`。

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_composed.py prepare --suite examples/composed-development.json --source real --model ecnu-plus --endpoint https://chat.ecnu.edu.cn/open/api/v1 --max-calls 8 --max-total-tokens 192768 --output runs/planning-proposal
```

这条命令仅演示准备材料；所用开发草案和未提交代码不能放行真实执行。取得新独立审核 Suite／审核锁并提交代码后，重新生成准确准备方案，复核模型、地址、上限和题目，再单独取得额度授权。

受控执行入口是 `run --source real --real`，还必须明确给出 `--suite`、`--review-lock`、`--proposal`、`--model-env`、模型／地址和同样的预算。真实模式拒绝固定响应文件；模型名、协议、地址、源码、任务、审核锁、请求和预算须与准备方案一致。API Key 仅在本地 `.env` 或环境中，由原适配器使用；不写入归档。

所有归档保存在忽略的 `runs/`。封印包含冻结输入、预算日志、脱敏报告、计划证据、逐项结果和汇总；`verify` 使用归档快照重新评分，不重新请求模型或业务服务。来源、Token 状态、任务／报告身份、断言、调用计数及保留额需互相一致。摘要检查不防御有系统写权限的人重写整个归档。

本协议单独输出 `formal_eval_v2_score=null`；不迁移或放宽旧正式 Eval v2 的 upload／poll 限制，也不把开发成绩并入历史真实模型成功率。运行器交付时真实模型、Java 和真实数据库调用为零。随后用户另行授权的[开发诊断补验](real-model-coverage-20261014.md)对 P001–P012 各真实调用一次，正常任务仅 2/7 完成；其 UNREVIEWED 成绩不具备独立评测资格，该受控入口的门禁保持不变。

后续[可信偏序与任务前置检查](composed-order-20261014.md)将组合语法中明确不支持的需求放到规划之前。运行器单独记录 `task_preflight_rejections`，完整的前置拒绝不调用模型、不写保留日志，也不计模型主动拒绝。`attempted/not_attempted` 继续指规划是否尝试，含前置拒绝的汇总新增 `unprocessed` 区分尚未处理题；旧无前置报告的归档汇总结构保持兼容。额度不足仍允许本地前置判断，服务异常或冻结变化停止后不继续处理。
