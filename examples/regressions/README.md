# 无模型固定回归样例

三个包对应 G701 主题一致性、G702 权限隔离、G703 关闭状态。
来源是本轮固定 Fake 工作流验证的完整 Scenario 和通过报告，**不是原始真实模型回复**；
旧归档只有参数哈希，不能从报告反推出丢弃的原始计划；也没有新增的来源配置摘要。

包由可信导出器生成，Python 模板固定，测试参数独立保存在 JSON。
本目录 conftest.py 是操作员编写的运行环境，限定三个全新的进程内工单实例；
凭据动态生成，不访问现有服务、Java、真实数据库或模型。

```powershell
.\.venv\Scripts\python.exe -m pytest examples/regressions -q
```

应为 3 passed。显式注入三种已知故障：

```powershell
.\.venv\Scripts\python.exe -m pytest examples/regressions -q --agenttest-ticket-faults
```

应为 3 failed、退出码 1，原断言分别检出错误主题、越权泄漏、关闭无效。
这不是失败后自动修复、反转断言或把故障目标标为 PASS。
CI 默认只执行正常目标；故障分支另有检查实际退出码和报告的验收。

运行其他导出包时，应提供自己的 agenttest_regression_runtime fixture，选择
已批准的契约、目标、策略及 TaskContract。包内的哈希不授予执行许可；
真实 Java 写入、隔离环境初始化等操作仍遵守原有授权要求。
