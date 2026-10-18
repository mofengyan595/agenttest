# 只读调查失败分类与最小改进

2026-10-18，DEVELOPMENT_ONLY / UNREVIEWED，独立人工审核 0。没有新增模型调用。
基准代码为 `c41f3cd`；工作区开始时干净。冷核对 `runs/goal-paired-remaining`
的封印、两段冻结、72 个报告及原固定对照通过，原 v1 12/24 与严格 v2 11/24 均保留。
健康 27/30、固定 pytest 30/30、有效证据 20/21 与上一轮一致。

## 可复核分类

[逐位置脱敏审计](demo-evidence/evidence-failure-audit.json)列出全部 24 个故障位置、
原报告路径和摘要、检查记录、决策原因及调用诊断。标签允许重叠，不能相加为失败总数。
`record` 均相对于 `runs/goal-paired-remaining`；没有覆盖历史归档。

| 分类 | 位置数 | 具体证据示例 |
| --- | ---: | --- |
| 支持结论正确 | 9 | 第一轮 PV1616，`reports/e2f2314b1c924c9e93285ee9946a8d80/report.json` |
| 没有取得支持证据 | 1 | 第一轮 PV1615，`reports/d07a9684fc4d4d3199c562eaf0027c6f/report.json`，非所有者读取后不支持断言 |
| 有支持证据但明确错误弃权 | 9 | 三轮 PV1619、PV1620、PV1622 均为字段回读；第一轮 PV1619：`reports/fc3544b7a14b41c1b8876a752beeda3f/report.json` |
| 有证据但结论失败，具体输出未知 | 2 | 第二轮 PV1613 结构拒绝、第二轮 PV1615 支持检查拒绝 |
| 选过不直接相关的角色/操作 | 5 | 第一轮 PV1613、PV1615；第二轮 PV1615；第三轮 PV1613、PV1615 |
| 动作结构或结束处理错误 | 1 | 第二轮 PV1613，`reports/cf71630fdaab477ab735ac90b29a471c/report.json`，MODEL_SCHEMA_VALIDATOR_REJECTED |
| 证据确实不足且主动正确弃权 | 2 | 第一、三轮 PV1623，PATCH 失败不能按当前同操作 GET 规则证明重复回读症状 |
| 证据不足但引擎拒绝猜测，非主动正确弃权 | 1 | 第二轮 PV1623，`reports/c88f59dda1f84c62add6a9023f628388/report.json` |
| 选择正确、仅引用不完整或错误 | 未知 | 被拒绝的 finding / refs 未保存，不能从 NEEDS_REVIEW 默认值猜测模型文本 |

## 模型输入与根因假设

`feedback_runtime.evidence_summary` 只提供 operation、role、outcome、检查类型、selector、
passed 以及 HTTP 状态的 expected/actual。字段值不发送，凭据、响应正文不发送。
`choices` 只有 capability_id、role、随机 resource_ref；没有 source 关系、所有者关系、
工具对应操作和策略用途。模型难以确认调查证据是否与原失败来自同一资源，以及读取
是否符合需要的角色。旧提示已写明同角色重复字段失败规则；9 次错误弃权说明仅存在
该文字没有保证模型执行规则。缺少语义是可检验假设，并非从未保存推理得出的结论。

旧 Schema 分别枚举 capability_id、role、resource_ref，允许交叉组成未批准的三元组；
action、selection、finding、refs 的组合仅在 Pydantic 后验拒绝。第二轮 PV1613 的
已知用量结构失败证明这不是纯理论问题，但具体违规字段未留档，不能断定是哪种组合。
最后模型位置已保留给 finish，预算没有因成绩差而扩张。

旧 v1 评分把引擎默认 EVIDENCE_INSUFFICIENT 当作模型主动弃权；现有 v2 已要求明确
finish 与 FEEDBACK_EVIDENCE_INSUFFICIENT，本轮固定此归因并增加拒绝诊断。
两种支持结论的证据充分条件已经是确定性规则；因此必须加入零模型对照，不能把
引擎规则能完成的结论全部归为模型价值。

## 选择的最小方案

1. 从当前批准绑定和实际观察导出同来源资源、同操作、同角色、所有者关系、重复失败
   检查及 HTTP 检查关系；不发送评分标签、缺陷开关、预期答案或“应选结论”。
2. 给选择附单独的工具说明表，保留三元组作为唯一动作；用完整动作分支约束 Schema。
3. 澄清何时调查、何时停止以及引用双份证据；仍由原 supported_finding 最终判断。
4. 新报告只保存枚举结论、合法证据 ID、授权选择摘要和拒绝码，不保存模型原文。
5. 在原 Executor / Policy / LangGraph 上比较旧模型、候选模型、固定调查加确定性规则。
   新任务包含多个生产者、阶段前缀、角色对调、异操作/无绑定失败，避免只换名称。

冷复核入口：`.venv/Scripts/python.exe scripts/audit_evidence_failures.py --archive
runs/goal-paired-remaining --output runs/<fresh-audit>.json`。输出须是新文件。
真实独立评测与人工效率测量均未完成。
