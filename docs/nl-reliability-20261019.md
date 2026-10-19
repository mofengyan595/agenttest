# 自然语言可靠性增量：定位与边界

本轮基线为 `ecc362cb19af79ea4e4102c3aac1ed7014afefb7`，开始时工作区干净，
远程 master 同 SHA。基线两个 workflow 已 success，Python 四个系统/依赖组合均 success。
新 Goal 授权覆盖本轮增量；不重跑旧付费题、不改历史封印或成绩。

## 阶段 1：可复查的失败分类

离线命令：`scripts/audit_e2e_failures.py --archive runs/evidence-e2e-real --output runs/<new-audit>.json`。
它先核对旧封印及原评分，再读取冻结 TaskContract、实际模型计划和公开上下文。
不构建新目标，不调用模型。原 EE 6/6 与 EV 108/108 归档冷核对通过。

| 位置 | 实际证据 | 分类与可确认边界 |
| --- | --- | --- |
| EE01 | `position-001.json`；报告 `5ad17cb88a5b439fa8d59b9576118c88` | 唯一意图错误为 `ORDER_MISMATCH:decoy:create`；原文明确先创建前置资源。模型先创建/读主资源后创建前置资源，属于顺序选择错误。 |
| EE02 | `position-002.json`；报告 `16dee877b695407583158ed3a34e63ea` | 模型确实 close 后 read，却选 `ticket_read_v1`。批准策略检查 open；正确 closed 策略存在。角色/策略不匹配使后续顺序诊断连带失败，不能误报为模型真的把 read 放在 close 前。 |
| EE03 | 报告 `76cb902efae740469466106e583ae8e0` 的 proposal 和 investigations | 有效所有者证据已取得，最终 ACCESS 提案引用受限角色调查；证据角色选择错误，原引擎拒绝。 |
| EE04 | 报告 `7b0e78d20bc441a28f903fab8a42ac3a` | 原业务故障检出、模型结论与引用受支持；保留成功。 |
| EE05/06 | 原 position-005/006 和报告中的 proposal/实际检查 | GET 不可用或瞬时字段异常，无满足规则的重复证据；模型仍建议持续回读症状，属于证据不足时不当断言。 |

所有位置都没有内部推理记录，不能推测其心理原因。未保存的输出不重建。
上述分类为开发证据，`DEVELOPMENT_ONLY / UNREVIEWED`，不等于独立人审。

源码核对：`planning_context` 已公开认证前提、binding_rules、source_policy_id、selector、
role_relation；缺少可直接使用的资源依赖摘要。通用 Schema 按 operation 约束参数槽位/类型，
但 operation、role、policy 尚未完整联合约束；全局 selector 枚举不能表示某槽位的合法来源。
跨步骤引用合法性仍需生成后检查。TaskContract 不传模型，意图门禁与原文所要求的先后关系一致。

可信配置表达缺口：冻结 EE 上下文的全部策略均没有 purpose；open/closed read 的公开目录
除 ID 外没有清楚区分检查的状态。批准断言本身正确，没有证据表明应放宽契约或断言。
TaskContract 使用逐操作/角色/策略唯一匹配，因此重复同签名步骤仍不支持；本轮不伪装支持。

## 最小实施方案

1. 从公开 Registry/Contract/认证配置派生生产者、消费者、角色/引用约束和合法步骤组合。
   增加通用规划检查清单，要求模型自行遵循原文偏序，不传 TaskContract 标准步骤/评分标签。
2. 公开策略已有检查的状态用途；同一新对照两组使用完全相同的可信策略配置，
   候选独有的派生摘要和 Schema 联合约束单独记录。不静默调整任何生成后的计划。
3. 原业务 Outcome、模型行动/建议、规则症状/证据、拒绝原因分层；规则从实际观察计算，
   不靠模型猜测提供最终可信症状，不读取评分答案。保留模型主动正确/弃权的独立评价。
4. 先 Fake/Mock 与安全回归，再冻结不超过六条开发题；开发结果不进验证。
   随后十二条全新题、两组各三轮，共七十二位置；独立目标/会话，全部 ASGI。
   候选冻结后禁止利用验证结果调参或补跑。

阶段 1 模型预算为零。后续每阶段在真实调用前另存固定排期/源码身份/安全预算，
每 run 最多一规划加三反馈模型、两次只读调查，无隐式重试，未知用量停止后续付费调用。
人工 TaskContract、策略与角色配置成本会记录；没有真实使用者计时，不报告节时。

## 阶段 2：规划前关系和可定位拒绝

`OpenAICompatiblePlanner(..., profile="generic", planning_constraints=True)` 增加
公开 binding_rules 派生的 resource_dependencies、认证前提、合法步骤组合，以及
通用需求完整性检查清单。CLI 的通用自然语言规划使用此开关；默认适配器仍可复现旧行为。
策略公开 purpose 仍由操作员维护，本轮两版本对照会共享相同用途明确的批准配置。

出站 Schema 联合绑定 operation/policy/批准角色集合，保留原参数槽位/类型约束。
它不表达跨步骤资源身份、先后关系或动态角色比较；这些仍由原引用/Policy/DSL/Intent门禁判定。
TaskContract 原拒绝码不变，新增封闭类别和 requirement_index 诊断，不输出参数值或标准答案。
原文中独立资源的先后关系只由模型自行理解；没有把 TaskContract.before 派生为给模型的答案。
结构化输出使用嵌套 anyOf 与闭合对象，核对过[官方格式约束](https://developers.openai.com/api/docs/guides/structured-outputs)。

最终固定源码验证：75 passed / 160.19s，Ruff 全仓通过；EE6/6、EV108/108再次冷核对通过。
首轮 3 failed / 72 passed 保留：一项新测试错误地期待批准角色也被 Schema 拒绝；
两项是测试期间编辑源码触发已有漂移门禁。修正测试边界并保持源码静止后整组通过，未放宽门禁。
该阶段零真实模型调用。下一步先完成规则结论和报告分层的离线验证，再进入新的有界开发排期。

## 阶段 3：可信症状的职责

操作员通过 `InvestigationConfig.trusted_symptoms` 启用规则输出；CLI 的批准反馈路径启用。
`trusted_symptoms.derive_symptom` 只接受原断言失败、已执行的批准 GET、父步骤和已验证资源来源。
访问边界规则要求受限读取意外成功、同资源生产者角色回读通过；持续回读规则要求同角色、
同操作、同批准策略摘要，HTTP 检查通过且同字段检查再次失败。错资源、错父步骤、错角色、
未执行读取或不同状态策略均不能冒充支持。证据不足输出 NEEDS_REVIEW，不猜内部实现原因。

报告分为原 `outcome/steps`、原 `feedback.decisions/proposal`、新增 `trusted_symptom`、
原模型建议采信/拒绝原因。规则不改原业务 Outcome、模型提案、主动弃权记录或任务满足值。
模型错误弃权而规则支持的反例已实际执行；规则结果不能记到模型正确分子。
控制台分别显示可信症状、模型建议、引用和拒绝原因，CLI JSON 同时返回原业务和规则结果。
旧报告新增字段缺省时不序列化，保持旧摘要和封印兼容。

验证：后端关联 106 passed / 2 skipped（Windows 链接权限），最终规则 6 passed；
公开报告/发布 40 passed / 7 skipped（同类平台权限）；Ruff通过。
前端 28 tests、typecheck/build 通过；原有两项E2E与新增分层展示合计3 passed。
补齐展示夹具双引用后单项再验1 passed；桌面/390px截图已检查，无横向溢出。
展示夹具明确是UI模拟，不作为真实模型或规则正确率证据；规则正确性由ASGI测试验证。
首次新增E2E因重复徽标定位失败，已限定业务总览；一次npm转发grep参数丢失未收集测试，
已用现有Playwright直接入口重验。EE6/6、EV108/108再次冷核对通过，未调用真实模型。

下一准确步骤：冻结最多六条全新开发题与预算，双版本从自然语言开始做ASGI/Mock验收；
开发真实排期后选择候选，再冻结十二条新验证题，禁止验证后继续调参。
