# 待审配置、复用与本地追踪

`prepare-project --contract <OpenAPI> --output <new.json>` 确定性提取接口、参数结构及认证类型，列出角色、授权、业务预期、资源关系和任务要求五类待审项。省略示例、默认值、描述和枚举原文。草案不是 ProjectConfig，不能执行。

已有可信项目通过 check-project 后，使用 `prepare-task --project <project.json> --user-task <目标> --output <new.json>` 复用契约、目标及策略摘要。操作员仍须填写 steps、before、required_goals 并审阅业务含义，产出正式 TaskContract 后再预检。草案不会产生批准策略或执行授权。

两个业务域的实测见 [计数归档](demo-evidence/onboarding-summary.json)。首次接入有五类待审项；后续任务有三类任务约束。两者覆盖范围不同，不能直接声称减少 40% 人工工作。课程、工单各复用 4 份核心输入中的 3 份，任务 JSON 改动 2 个叶值，自动带入 3 个摘要。这里的 2 个叶值属于同一模板更换名称，不代表新业务约束的全部人工工作。两域 Fake/ASGI 执行通过，共 5 次请求；开发夹具填写任务约束，不是独立人工审核。没有人工计时，节省工时未知。

`export-trace --run-id <id> --runs-dir <reports> --output <new.json>` 从已有报告生成本地追踪，关联规划、三层校验、业务执行、调查决策与工具、结论。保留操作/能力/步骤/角色标识、固定错误码、耗时、请求摘要、证据引用及逐调用 Token；不保存正文、字段值、认证、目标 URL 或原始模型文本。外部上传始终 false；未引入 Langfuse 客户端。原始脱敏报告仍是重评分依据。

三份 FE 真实模型报告导出的追踪合计 10 次调用、8,647 Token，与原报告一致；每份均覆盖五阶段，外部上传为 false。追踪自身不推断真实模型来源，来源由封印的实验配置证明。未知用量保持未知。

复核命令：`python scripts/check_onboarding.py --output runs/<new>`，或 `--verify runs/<existing>`。私有归档 `runs/goal-onboarding-two-domains`；真实报告追踪位于 `runs/goal-stage3-traces`。均不属于正式独立评测。

成本对照补充：从空白 TaskContract 新建时有 3 个摘要待填写；复制已有同类任务时，三摘要本来就可以原样保留，手工摘要填写同为 0。草案命令不能据此声称比复制模板额外节省三次填写。3/4 文件复用来自既有项目分层机制，不能全部归因于新增草案工具。旧原始计数归档不改，最终计数将两种起点分别列出。
