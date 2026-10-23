# 可信测试 Goal 交付核对

依据[原始目标](trusted-testing-goal.md)核对本轮五阶段。工程和冻结排期已完成，真实质量目标未达到；
不将工程交付称为规划效果达标。最终提交的准确SHA、六项CI与本地检查结果随交付回复提供，
本表中的早期验证记录不能替代最终提交的CI。

| 范围 | 已交付内容与证据 | 边界/实际结果 |
|---|---|---|
| 现状及历史 | 保留原15/36、追加1/7、NE_NEW01与54份原报告；[旧失败审计](trusted-testing-stage2.md) | 原54份报告中15份含额外操作、1份无批准计划，另3个项目位置存在预算排除；依据保存计划，不猜内部推理 |
| 阶段1：批准负例 | [实现与验证](trusted-testing-stage1.md)、`examples/regressions/approved-inputs` | 必填缺失、类型、枚举、边界；单字段body/query，合法基线后确定性变异、精确违例集合；无全局跳过校验 |
| 阶段1：错误与保护 | 专用ASGI目标覆盖正确拒绝、错误接受、错误正文、未批准变异阻断 | 正常输入、批准拒绝、ASSERTION_FAILED、基础设施/工具失败、未知待审分开；正常/负例固定模板禁用模型 |
| 阶段2：分组与预算 | [分组预算](trusted-testing-stage2.md)、[可复用配置](trusted-testing-approved-projects.md) | 当前组公开目录、批准关系、共享ledger与预留额度；不自动补步骤/替换策略/重排；候选到断言的分母均保留 |
| 阶段2：足额/不足 | 原两域项目演示和冻结排期；[最终逐位置收据](demo-evidence/trusted-testing-amended-real.json) | 不足正确退出3/3；足额项目真实完整0/6；部分PASS不能冒充完整项目 |
| 阶段3：表达与草案 | [表达机制](trusted-testing-stage3.md) | 保留受控语法与格式归一化；GoalSpec引用注册目标、保存source span/未解释要求，semantic_verified=false；真实未知草案正确保留0/3，始终待审、零HTTP |
| 阶段3：第三项目 | [实验室接入](trusted-testing-laboratory.md) | 双独立生产者、装载、查询、角色拒绝；六配置+fixture+冻结记录；原88c9核心只加配置验证三路，后续通用提示精简另立4707c07核心，不隐瞒迁移过程 |
| 阶段3：成本 | [接入成本](trusted-testing-approved-projects.md)、[最终结果](trusted-testing-final-results.md) | 新需求手写步骤/偏序/逐题TaskContract为0；项目接入、策略审核与隔离评分矩阵仍需人工；没有计时，不报节时 |
| 阶段4：证据归属 | [证据与工作台](trusted-testing-stage4.md)、[失败明细](demo-evidence/trusted-testing-failure-analysis.json) | 原业务、模型提案、规则结论、主动弃权、引擎拦截分别保存；证据不足安全退出3/3，模型正确主动弃权1/3、不当提案拦截2 |
| 阶段4：UI | 现有工作台补负例、预算、未执行、草案待审；`docs/screenshots/trusted-testing` | 原36前端测试、类型检查、构建、5项浏览器交互通过；演示fixture属于Mock，不能代替真实成绩 |
| 阶段5：协议及校正 | [v2冻结](trusted-testing-validation-v2.md)、[参考校正](trusted-testing-reference-amendment.md) | 12×3、6单/3流程/3项目，三域；足额分母30、项目6在运行前固定；只修隔离参考两处接口拼写，不改产品/任务/业务断言/预算/评分算法；旧错误分数与归档保留 |
| 阶段5：门禁及实测 | [最终结果](trusted-testing-final-results.md)与四份公开收据 | Mock36全尝试、足额30/30/项目6/6后，真实36全首次尝试；完整12/36、足额12/30（40%）、项目0/6，80%/三分之二均未达到；无追加 |
| 阶段5：负例/安全/成本 | 同上，逐位置/子报告/诊断可追溯 | 批准负例82/144实际发送并正确拒绝、62未执行；未授权执行/错误证据采信/预算违规0；234真实调用、334946Token、268 ASGI请求，用量未知0 |
| 首次新需求 | [TV_NEW01](../examples/regressions/TV_NEW01/EVIDENCE.md)、[首次收据](demo-evidence/trusted-testing-first-demand.json) | 未Fake预执行，一次真实PASS、1调用/2979Token/5请求；原CLI仅归档封印失败保留，修元数据未重跑；固定回归是operator_plan，不是第二次NL成功 |
| 演示与材料 | [三层可复制演示](trusted-testing-delivery.md)、[简历/面试](trusted-testing-resume-interview.md) | 本轮质量未达标、旧结果不可跨协议计算提升、原生Function Calling只有Mock、自建三域不能推出广泛泛化 |

全部真实调用合计235、337925Token、273 ASGI请求；真实业务HTTP/数据库写入0。
没有CourseInsight变更、新Java/PocketBase写入、RAG、多Agent、模型代码执行、修断言、恢复或写重放。
自建目标、开发者执行，DEVELOPMENT_ONLY / UNREVIEWED；独立人审仍需真实参与者，人工节时=null。

本次交付只完成已授权工程与有界评测，不追加付费追分。下一准确里程碑是用新的开发案例改进负例合法基线提案，
再另行冻结新评估；本轮已用题目不得继续用于调优或替代首次成绩。
