# 合法计划复用Goal：交付核对

工程交付、真实质量与用户验证分别判断。本文件汇总可核对的来源，不替代原始报告。

| 范围 | 来源 | 核对结果 |
|---|---|---|
| 起点与授权 | [Goal](plan-reuse-goal.md)、[阶段1](plan-reuse-stage1.md) | 固定86d6822；允许必要提交/配置模型调用，未建branch/worktree |
| 成熟工具与依赖 | [阶段1](plan-reuse-stage1.md)、[最终共同子集](plan-reuse-value-report.md) | 独立4.32.4环境、完整锁文件；产品不新增Schemathesis依赖；原生探索未开启 |
| 根因与派生 | [阶段2](plan-reuse-stage2.md)、plan_reuse.py | 保存的旧提案/诊断为依据；合法结构不可变、任务/配置绑定，拒绝基础计划不修复 |
| 三层入口及资源 | [阶段3](plan-reuse-stage3.md)、test_plan_reuse.py | 每个变体重新校验/取资源，父账本共享，状态/角色结构分离；补齐后38项针对性测试通过 |
| 导出及工作台 | [阶段3](plan-reuse-stage3.md)、[体验包](plan-reuse-user-trial.md) | 正常/负例均为实际校验Scenario；两份无模型回归、37前端测试、类型检查/构建及浏览器交互通过 |
| 参考覆盖补齐 | [冻结协议](plan-reuse-validation-protocol.md) | v1模拟/源码/参考保留；v2六原文字节不变，仅加强项目正常边界；负例105分母、原断言/诊断/预算不变 |
| 冻结门禁 | 同上、[Mock公开收据](demo-evidence/plan-reuse-paired-mock-v2.json) | edf3d3f产品准确六CI成功；12组编码最高18230/20000；36位置Mock候选15/15、项目6/6、负例105/105、安全0 |
| 调度安全 | 同上、真实归档parent-runner.json | 总控11e4697独立冻结；只允许调度脚本差异，评分安全事件亦全局停止；排期单次认领，不重启付费任务 |
| 本地全量与复核 | runs/plan-reuse-full-final-v2.xml、runs/plan-reuse-four-stable.xml | 本地1798通过/15跳过/4失败；均为期间文件变动触发冻结，停止编辑后原4例通过/165.09s；不将原全量称通过 |
| 准确源码CI | edf3d3f、11e4697对应Actions | 两提交均六CI成功；最终交付提交另按实际结果核对 |
| 首次真实结果 | [完整结果](plan-reuse-final-results.md) | 36位置全部首次尝试且冷核对；候选9/18、足额9/15、项目3/6、负例85/105；三项目标未达到，安全0，无追加 |
| 共同子集边界 | [价值与限制](plan-reuse-value-report.md) | 阶段1三组9请求严格模拟；阶段4工具两组8/6/9强制同预算，Agent为64请求全任务的描述性子集，不伪称四组独立同预算实验 |
| 已用样本隔离 | goal-used-task-identities.json、baseline-exclusions.json | 六条身份追加到未来独立留出排除表；起点登记原字节及SHA另存，未改原参考/分数 |
| 使用者与材料 | [反馈表](plan-reuse-user-feedback.md)、[简历/面试](plan-reuse-resume-interview.md) | 入口可用，参与者/独立人审/人工计时未完成；不代签、不报节时 |

历史真实归档再次冷核对通过，新模型调用0，12/36、12/30、0/6、82/144保持原样。
原v1预算失败、v2参考拼写与校正、TV_NEW01业务成功后的包装失败均保留，不以本轮结果覆盖。

本轮未修改CourseInsight，未新增真实Java/PocketBase或生产写入，没有RAG、多Agent、模型代码执行、
计划修复、断言放宽、请求恢复或写重放。执行对象均为专用ASGI/内存目标，平台元数据写入属于授权范围。
