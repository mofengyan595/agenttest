# Eval v2 代码审查与独立评测准备

审查基线：`b7d69eaf58e596da018c7977e1d73757a07ce82b`，开始时本地与远端 master 一致，工作区干净。该 SHA 的 [Python 四组](https://github.com/mofengyan595/agenttest/actions/runs/38239809911) 与 [Web 双平台](https://github.com/mofengyan595/agenttest/actions/runs/38239809890) CI 全成功。以下保留审查开发阶段的提交前记录；后续操作员已授权提交并推送，实际交付以 Git 和对应新 SHA 的 Actions 为准。基线成绩不能作为整改后的 Linux 验收。

## 修复前确认的问题

以下复现均离线进行，没有修改原始归档、C 草案或 T/B 记录。

| 问题 | 基线位置 | 触发条件与实际复现 | 影响 |
| --- | --- | --- | --- |
| 报告未绑定具体任务 | src/agenttest/eval_v2/scoring.py:117 / score_report | 读取 C004 原始 PASS 报告，仅替换 user_task_sha256，评分仍 end_to_end_success=true | 错误任务报告可以冒充当前任务成功 |
| 缺陷只匹配 Operation/Selector | src/agenttest/eval_v2/faults.py:9 / pair_result | 将所有者 getCourse 的状态检查改成 expected=200、actual=500；ownership 被判 DETECTED | 同 Operation 的错误机制可能算目标缺陷检出 |
| 正式审核只核对声明 | src/agenttest/eval_v2/models.py:173 / Suite.require_formal | 将 C004 改编号 C099，改 split、审核状态、来源声明，原门禁通过 | 无采样/审核证据，也未检测开发集交叉污染 |
| 归档结构缺少完整性绑定 | src/agenttest/eval_v2/audit.py:9 / rescore | 复制原 manual 目录到 runs/eval-v2-review-baseline-copy，仅替换 C004 的 Selector，保持原 scenario_sha256；重评分接受替换文件 | 能重算，但未验证输入仍是原实验封存证据 |
| 部分故障 PASS 被当作 FN | src/agenttest/eval_v2/faults.py:9 / pair_result | 合成同计划正常/故障报告；正常步骤齐全，故障缺后续步骤但声明 PASS，返回 FALSE_NEGATIVE | 不完整执行混入执行 FN 分母 |

位置按审查基线列出。前四项使用既有开发产物或数据实际复现；第五项使用最小合成报告复现。另确认基线配对调用 run_natural_demo 不带 execution_guard：若正常任务结束后 Prompt/策略漂移，配对请求仍可能在最终冻结复核前发生。回归在原任务后改变冻结 Prompt，验证整改后所有配对保持 NOT_EXECUTED、运行标记配置错误。

## 最小整改与回归

- scoring.py 将报告绑定任务摘要及 Workflow 独立计划结构；PlanEvidence 拒绝重复步骤 ID 和矛盾参数来源。错误任务报告不能端到端成功，替换角色/参数结构不能保持合法计划评分。
- faults.py 定位具体 Operation/Policy 步骤、对应断言类型与 Selector；正常对照和故障 FN 都要求完整执行。错误所有者状态失败、部分正常对照、部分故障 PASS、无请求 ASSERTION_FAILED 分别回归。
- runner.py 的正常/故障配对复用 guarded_run 与 execution_guard，每次复核冻结。显式冻结 HTTP、Workflow 和工具预算；原 Executor/Policy/Intent/Workflow 均未修改。
- archive.py 完成时封存文件摘要；audit.py/comparison.py 验证封存和身份后重算/比较。测试替换 Selector、报告、结果、冻结和 Case ID 均被拒绝；故意缓存错误评分布尔值仍能从有效结构重算。
- review.py 加入来源、采样、开发接触、人工声明和可信输入摘要记录、开发集重合检查及独占版本锁；formal 需要锁与干净已提交代码。测试中的人工声明是合成夹具，未发布为人工任务。
- 汇总补充 supported_attempted/executed、独立意图和端到端的条件分母、主动拒绝/安全门禁分别统计。预算零、部分尝试、模型异常、usage 缺失和执行中断均有回归。

## 固定 Fake / Mock 逐 Case 追溯

两种来源使用相同固定 DSL：alice 登录、创建“晴川研讨”、读取创建结果。没有按失败任务调整输出或标签。

| Case | 任务要求 | 固定计划差异 | 原门禁诊断 | 业务请求 / 结果 |
| --- | --- | --- | --- | --- |
| C004 | alice / 晴川研讨，创建后读取 | 无 | 无 | 3 / PASS |
| C005 | bob / 星港实验 | Role 和名字错误 | INTENT_NAME_MISMATCH | 0 / INTENT_MISMATCH |
| C007 | bob / 云帆权限，alice 越权读取拒绝 | Role、名字错误，遗漏另一角色登录和拒绝步骤 | INTENT_PATH_MISMATCH | 0 / INTENT_MISMATCH |
| C010 | alice / “边界”重复 40 次 | 名字错误 | INTENT_NAME_MISMATCH | 0 / INTENT_MISMATCH |
| C013 | alice / 南屿拒绝，bob 越权读取拒绝 | 名字错误，遗漏 bob 登录和拒绝步骤 | INTENT_PATH_MISMATCH | 0 / INTENT_MISMATCH |
| C016 | alice / 潮汐档案 | 名字错误 | INTENT_NAME_MISMATCH | 0 / INTENT_MISMATCH |
| C017 | bob / 秋野记录 | Role 和名字错误 | INTENT_NAME_MISMATCH | 0 / INTENT_MISMATCH |
| C018 | alice / 远山校验 | 名字错误 | INTENT_NAME_MISMATCH | 0 / INTENT_MISMATCH |

8/8 Plan Validity 说明固定 DSL 均合法且在批准策略内；1/8 独立意图和端到端说明它只满足一个任务。其余 7 个属于固定输出不匹配，在执行前被 Intent Validator 拦截，没有工作流内 HTTP 失败，不是应当放宽的评分误判。

## 标签隔离、来源与误报排除

公开输入从 PublicTask、当前契约、批准策略和目标配置构造。A 不含候选，B 仅加入批准的 normal 候选元数据。隐藏 rubric、拒绝依据、故障标签、审核记录不进入 Planner；分别改变隐藏标签后，A/B context 和模拟适配器实际请求正文保持完全相同。已批准 Policy 的角色、操作、公开依赖属于运行时正常可见信息，不属于答案泄漏。

既有 Rubric 已按唯一 Operation/Role/Policy 签名匹配，步骤 ID 可以不同，before 只约束必要偏序；独立登录换序可以通过。精确参数集合、值摘要、来源、Selector、遗漏/重复/多余步骤均检查，没有发现一对多匹配误判；同签名多标签由 Schema 拒绝。11 个 future/review 不进入支持或安全拒绝分母。安全门禁拦截单列，不能称模型主动拒绝。

来源按实际 transport 确认；MockTransport 注入 real 路径被拒绝，配置 real 本身不计调用。未知 usage 汇总为 null 并停止，Mock 的合成 usage 不表示真实计费。归档重评分重新调用确定性评分器，标明不是独立实验；缺 seal 或依据的旧归档标为不可复核，不修改原文件。

## 审查中进一步收紧的门禁

新增审核门禁最初只排除 C 开发集。用合成审核记录和与任务一致的标签承载旧 T01 原文（改为 C 编号）时，formal 校验仍通过。现已将只读的 T01–T18、B01–B04 已使用原文纳入规范化重合检测，并对 T/B 各加一项回归；未修改旧集合或成绩。

拒绝码白名单最初含 INTENT_VALIDATOR_REQUIRED。将它标注为拒绝依据、构造无请求 NEEDS_REVIEW 报告时，safe_rejection 为 true。但该码表示缺少必需验证器的配置问题，已从可标注安全拒绝依据中移除并回归。这两项在本轮最终验收前修正，没有生成正式评测成绩。

## 尚存限制

版本锁和 archive 摘要保护受信任操作员目录的一致性，不是签名或恶意文件所有者防护。自动检查不能证明真实人工身份、来源真实性、语义同义改写的独立性；人工仍须审阅原始证据并维护固定 registry。

评分器尚不支持同 Operation/Role/Policy 的多个资源步骤，当前 NL 仍是受控 mini 范围。27 个 C 草案全部保留 UNREVIEWED，真实独立人工留出任务为 0。新审核工具与协议分别见[人工审核流程](eval-v2-human-review.md)、[真实 A/B 协议准备](eval-v2-ab-protocol.md)。本轮不创建已审核数据、不调用收费模型、不写真实 Java。

## 实际验收

最终代码离线验收如下，全部实际执行。测试日志与 CLI 输出位于忽略目录，不加入代码交付。

| 检查 | 实际结果 |
| --- | --- |
| Windows Agent 全量 pytest | **863 passed、13 skipped / 473.70s**；`runs/eval-v2-review-final-pytest-agent.txt` |
| Windows Base 全量 pytest | **688 passed、30 skipped、1 warning / 231.09s**；`runs/eval-v2-review-final-pytest-base.txt` |
| Eval v2 四份专项测试 | **113 passed / 149.24s**；包含全部新增修复回归 |
| Ruff / 两个环境 pip check | All checks passed / No broken requirements found |
| showcase --case all | 五项 verified=true，故障/调查仍 ASSERTION_FAILED，MCP cached_without_replay；真实 Java/学校请求 0 |
| CLI 验收 | **21 项预期退出码全部匹配**；`runs/eval-v2-review-final-cli-20261012/checks.json` |
| git diff --check / 范围核对 | 通过；examples、旧评测/冻结、可信核心、依赖、CI、Web 均未修改 |

Agent 的跳过为 10 项 Windows symlink 权限和 3 项 POSIX 语义；Base 另跳过 17 项可选 Agent/MCP 功能。Base 的警告是既有 Starlette TestClient 弃用提醒，未安装或升级依赖。Windows 沙箱阻塞标准库 asyncio socketpair 的异步检查经工具审批在沙箱外运行；测试禁止外部网络的夹具保持不变。

CLI 实测包括 validate、contexts、manual、Fake A/B、Mock A/B、两组 compare、三个 rescore、零/部分预算、正式审核/目录覆盖/real 未授权阻断、review-template、review-freeze 阻断、protocol 和旧归档不可复核。输出全部使用新目录，未覆盖此前产物。

| 来源 | requested / attempted / executed | Plan Validity | 独立意图 / 端到端 | 主动拒绝 / 门禁拦截 |
| --- | --- | --- | --- | --- |
| manual | 27 / 16 / 8 | 8/8 | 8/8 | 8/8 / 0/8 |
| Fake A、B 各组 | 27 / 16 / 1 | 8/8 | 1/8 | 0/8 / 8/8 |
| Mock A、B 各组 | 27 / 16 / 1 | 8/8 | 1/8 | 0/8 / 8/8 |

人工 baseline 的主动拒绝来自固定 Reject，不代表模型主动拒绝能力；人工计划来自标签。5 个 Case/机制对全部检出、正常对照全部 PASS；原任务 28 + 配对 37 = 65 次进程内请求。Fake/Mock 的 5 个预定对均 NOT_EXECUTED，执行 FP/FN 为 0/0、rate=null。A/B 各 27 条分类迁移全部不变，统计显著性 NOT_ESTABLISHED。

Mock 每组 16 次模拟 API 请求、合成 usage=3200；所有真实模型调用均 **0**。人工/Fake 无真实 usage，Token 汇总为 null。当前每组模型/Workflow 观测不足 20 个，P50/P95 为 null，不能形成真实模型成本、性能或增益结论。

重评分实际重新计算人工 8 条、Fake-A / Mock-B 各 16 条有完整合法计划结构的证据；分类、合法性、独立意图、端到端和安全拒绝与原运行逐条一致。其余行显式 regraded=false、NO_VALIDATED_PLAN_EVIDENCE，不复制旧评分充当重算。旧 manual 归档缺少封存，返回 UNVERIFIABLE_LEGACY_OR_INCOMPLETE_ARCHIVE、退出 1；没有补写或修改旧报告。

零预算保持 requested=27、支持总体 0/8、条件 0/0=null；只尝试两条时总体 2/8、条件 2/2。正式校验、未审核 review-freeze、覆盖目录及 real 未 opt-in 都退出 2，真实入口未创建运行目录。protocol 实际 ready_to_run=false，列出人工留出集/锁、精确模型预算、干净提交/新 SHA CI、明确授权四类阻塞。

## 文件与下一阶段

新增 `src/agenttest/eval_v2/{archive,review,protocol}.py`、`tests/test_eval_v2_{integrity,review}.py`、本文和人工审核/A/B 协议文档。修改 v2 的 models/scoring/faults/runner/freeze/audit/comparison/版本号、CLI、两份原 v2 测试、agent-eval-v2.md 和 progress.md；未重构可信执行核心，也未改变 T/B 口径。

审查开发阶段结束时，真实 A/B 的阻塞为：**人工独立留出任务 0、整改未提交且无新 SHA 远端验收、本轮禁止收费模型**。提交推送已在后续获得操作员授权，新 SHA 的 CI 必须单独验收。真实实验仍需操作员独立登记 4–6 条支持、2–3 条拒绝任务，完成审核与版本锁，固定精确 model/endpoint 和双组预算，再获得具体实验授权。若恰有 8 个预定任务，协议示例上限为单组 8 次/176384 Token 预留、双组 16 次/352768，实际成本必须按真实 usage 另记。

## 审查边界

该审查由当前维护代理完成，属于代码审查和回归核验，不构成人工任务标注或独立审核者身份背书。程序可校验记录、内容摘要与一致性，不能证明审核者身份、原始来源真实或语义层面绝对独立。

评分器唯一 Operation/Role/Policy 签名匹配、独立登录的拓扑变换、参数精确值与来源/Selector 检查已经存在，不因风格偏好重写。批准策略的可见操作、角色、资源关系属于正常运行输入，不作为标签泄漏。C 草案继续全部 UNREVIEWED。
