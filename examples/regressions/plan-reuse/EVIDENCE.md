# 派生正常/负例的固定回归

来源为本Goal单接口模拟演示，1次模拟规划/10请求，真实模型0。
两个场景均从合法基线按批准规则派生，原导出模板逐字节保留；不是两次新的模型规划成功。

运行：`.\.venv\Scripts\python.exe -m pytest examples/regressions/plan-reuse -q`。
fixture禁止真实模型、语义模型、诊断模型、原生工具模型、模拟规划器和真实HTTP。
每次独立创建内存工单目标，报告operator_plan/task_satisfied=null，不复用旧ID或执行结果。
没有独立人审、真实用户计时或人工节时证据。
