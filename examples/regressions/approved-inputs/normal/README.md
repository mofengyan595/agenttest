# AgentTest 固定回归包

使用项目 dev 依赖执行 `python -m pytest <本目录> -q`。
操作员必须提供 agenttest_regression_runtime fixture，返回
agenttest.regression.RegressionRuntime：契约、目标、策略、TaskContract、
凭据环境、显式 HTTPX transport 和报告存储。缺少 fixture 会报错，
没有默认网络目标，也不会调用模型。

scenario.json 含明文测试参数，请使用合成数据。包内不包含凭据、目标地址、
策略定义或 HTTP 响应。每次运行重新校验操作员配置，并创建全新的 Executor run；
报告仍为 operator_plan / task_satisfied=null，不冒充新的自然语言规划。
原断言失败会让 pytest 失败，不会恢复原 HTTP 会话或自动重试写入。
哈希用于发现变更，不能认证不可信来源，也不替代人工审核。
