# 首批自然语言评测任务（已人工确认）

全部使用一次性 mini target、合成课程名。用户于 2026-10-11 明确批准 T01–T18 的任务、角色、课程名、预期及路径。

<a id="human-review-2026-10-11"></a>
人工审核范围仅为独立 mini target；不得自动扩展到 CourseInsight。
courseId 必须来自创建响应，查询同时比较 ID 和名称。模型不可读取此任务集的 Golden Labels。
批准任务不代表模型已经通过测试；指标以实际执行为准。

所有任务要求：创建者登录，创建返回 201，创建者查询返回 200，ID/名称一致。
归属任务额外要求另一角色登录并查询同一创建资源，返回 403。不得用常量资源 ID。

| ID | 类别 | 创建者 | 需求 |
| --- | --- | --- | --- |
| T01 | consistency | alice | 用户 A（alice）创建名为“线性代数”的课程，再查询新课程，核对 ID 和名称一致。 |
| T02 | consistency | bob | 请用用户 B（bob）创建“概率论”，随后由 B 读取它，检查返回的课程编号和名称与创建操作一致。 |
| T03 | consistency | alice | 验证 alice 新建“数据结构”课程后，能够用创建返回的 ID 查询到相同课程名称。 |
| T04 | consistency | bob | 以 bob 身份测试课程“数据库”的创建和查询，要求读回的 ID、名称正确。 |
| T05 | consistency | alice | 帮用户 A（alice）测试“算法设计”课程从创建到查询的数据一致性。 |
| T06 | consistency | alice | 新建一门“计算机网络”课程，创建者使用 alice。再以 alice 查询并比较名称与课程 ID。 |
| T07 | consistency | bob | 用户 B（bob）添加“操作系统”后立即查询刚创建的课程，确认没有读到其他课程或错误名称。 |
| T08 | consistency | alice | 为 alice 规划“软件工程”课程创建及读取测试，使用真实创建响应的编号，核对课程数据。 |
| T09 | consistency | bob | 验证 bob 可以创建“人工智能”课程，并能准确读回同一编号和名称。 |
| T10 | ownership | alice | 用户 A（alice）创建“离散数学”，A 查询确认一致，再让用户 B（bob）查询同一课程，应受到归属权限限制。 |
| T11 | ownership | alice | alice 新建“编译原理”并正常读取后，bob 登录访问这个课程，验证跨用户读取被拒绝。 |
| T12 | ownership | bob | 由 bob 创建“机器学习”并核对自己的读取结果，再验证 alice 读取同一个课程时被拒绝。 |
| T13 | ownership | alice | 检查“数值分析”的所有权：alice 创建且自己能正确查询，bob 对该新课程的查询应被禁止。 |
| T14 | ownership | bob | 让用户 B（bob）创建“图论”并读取验证一致性；随后用户 A（alice）访问同一 ID，验证归属检查生效。 |
| T15 | ownership | alice | 测试用户隔离：alice 创建“信息安全”后读回正确数据，bob 即使已登录也不能读此课程。 |
| T16 | ownership | alice | 以 alice 创建“分布式系统”，先验证创建者查询一致，再检验 bob 对相同课程编号的访问被拒绝。 |
| T17 | ownership | bob | bob 创建“自然语言处理”并查询确认，alice 登录后尝试查询它，应该受到权限限制。 |
| T18 | ownership | alice | 为课程“计算机图形学”规划归属测试：创建者 alice 查询成功且数据一致，另一用户 bob 查询同一资源被拒绝。 |

这 18 条只覆盖两个 mini target 意图及其措辞/角色/数据变体，不代表开放接口泛化能力。
人工基线读取每条任务的预标注结果；固定 FakeLLM 每次返回同一个计划；真实模型只接收 user_task 和公共契约/策略，不接收标注。
计划有效率、策略选择正确率、端到端成功率分别统计，PASS 不等于满足自然语言需求。
