# Eval v2 独立人工留出集审核

当前没有已审核的独立留出任务。`c-drafts.json` 的 27 条仍是 `UNREVIEWED` 开发草案，不应改编号、同义改写或修改审核声明后转为留出集。本文和工具准备审核流程，不代替人工采样、标注或身份确认。

## 登记与采样

先生成空白登记包，不调用 Planner 或 HTTP：

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_v2.py review-template --output runs/review-enrollment-001
```

`enrollment.json` 是待填写表，不是可运行 Suite。初批建议独立采样 4–6 个当前可支持任务、2–3 个安全拒绝任务。操作员逐条保存原始需求和来源证据；源文件可存放在受控审核目录，通过引用和 SHA256 关联，不把敏感材料送入 Planner。

| 登记内容 | 要求 |
| --- | --- |
| 原始来源 | source_ref、原始材料内容摘要、带时区采样时间、采样方式；保留可追溯原文 |
| 开发接触 | 是否参与实现、调参或开发评测；已经使用的任务不得进入独立留出集 |
| 目标和版本 | PublicTask 中的目标、契约、Policy Registry、fixture；记录实际可信输入摘要 |
| 支持判断 | execute / reject / review；当前 formal 不接受尚无法确定的 review |
| 授权和目标 | 人工核对 Role、Operation、Policy、参数值、资源依赖、Selector 和全部子目标 |
| 合理拓扑 | 用 Rubric.before 表达必要偏序；独立步骤可以交换，不要求唯一 DSL |
| 禁止内容 | 标明多余步骤、额外参数、越权、重试等；评分器按精确步骤签名和参数集合检查 |
| 安全拒绝 | rejection_basis 使用可信拒绝码；rejection_evidence 记录授权/契约或不确定性依据 |
| 审核结论 | reviewer_ref、带时区审核时间、rationale、来源独立性人工确认；保留外部审核记录 |
| 冻结 | dataset_version、任务/标签/审核记录和可信输入摘要、版本锁位置 |

AI 可以帮助整理未审核草案，不能填写虚假的 `HUMAN_REVIEWED`、`independent_human`、审核者或采样事实。登记模板初始来源字段只是草案声明；正式来源必须由操作员核实后填写。

## 当前可评分的范围

仅使用既有 mini fixture、批准策略与受控自然语言能力，不为凑齐样本增加真实写入。可执行标签必须通过现有 Executor 的静态契约/策略校验和 Intent Validator；这一步不执行计划，也不以 Intent Validator 代替独立 Rubric。异步自然语言规划、通用非法参数执行、多资源歧义标签继续留在开发/future 范围。

同一个 Operation/Role/Policy 签名不能对应多个 Rubric 步骤。参数必须区分字面值与来源步骤，来源必须指定 Selector；before 和 required_goals 必须一致且无环。当前评分器无法可靠表示的标签应回到 review，不能勉强进入正式集。

审核记录包含 `task_sha256=fingerprint(task)`、`labels_sha256=fingerprint(labels)`，采用项目的规范 JSON 摘要；不是文件字节摘要。契约、策略和目标摘要采用 `contexts` 输出的可信 inputs。原始来源材料的 `source_content_sha256` 为其字节 SHA256。修改审核来源、任务或标签后重新核对所有摘要，不能用模型输出修正答案。

## 正式门禁与版本锁

人工确认后另建 Suite，使用新 dataset_version、`split=independent_holdout`，逐条附 ReviewRecord。运行工具只检查记录与一致性，不自行赋予人工审核事实。

```powershell
# 仅针对操作员确实审核完毕的新文件。默认 C 草案在这里被拒绝。
.\.venv\Scripts\python.exe scripts/evaluate_v2.py review-freeze `
  --suite <operator-reviewed-suite.json> --output <operator-review-registry>

.\.venv\Scripts\python.exe scripts/evaluate_v2.py validate --formal `
  --suite <operator-reviewed-suite.json> --review-lock <registry/version.json>
```

门禁核对全部人工审核声明、记录完整性、时间先后、未参与开发的声明、任务/标签/fixture 摘要、可信拒绝依据和静态可评分性。任务重合检测覆盖 C 开发集与已使用的 T/B 原文，忽略 Case ID，规范化大小写、Unicode、空白与标点；改编号或格式不会绕过。相同 Suite 内重复任务也拒绝。缺少 Intent Validator 等配置错误不能标注为安全拒绝。

版本锁在操作员固定的 registry 内按 dataset_version 独占创建。同一版本不能覆盖或重新冻结；任务、标签、审核记录变更必须使用新版本和新锁。正式运行还要求干净且已提交的工作区，并把锁摘要纳入运行冻结。降低 HTTP/Workflow 预算需在实验协议中明确，两组一致；预算不扩大原批准执行权限。

这些检查不能证明审核者身份、来源真实性、同义改写后的语义独立性，也不能阻止拥有目录权限的人另建 registry 或重写证据。操作员必须保存原始材料、固定 registry 和版本发布记录，人工核查语义重复与独立性；正式实验前还需确认对应提交的跨平台 CI。不能把程序通过等同于独立人工审核完成。
