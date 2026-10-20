# NE_NEW01 首次新需求的固定回归

原文：先创建工单“星岚交接”，再权限检查、列表、关闭、查询。
首次真实CLI：1次模型 / 3358 Token / 5步，PASS且task_satisfied=true。
四份导出文件与首次归档逐字节一致；conftest为操作员固定fixture，门禁从原文和项目配置派生。
配套原文/配置JSON语义与首次归档一致，按仓库规范保存为LF换行。

在仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe -m pytest examples/regressions/NE_NEW01 -q
```

无需Key、Docker或网络。fixture禁止规划/反馈模型和真实HTTP，使用全新ASGI资源；
报告为operator_plan / task_satisfied=null / model_call=null，不是第二次LLM成功。
首次归档的模型禁用回归1 passed，源报告和导出摘要见
[首次证据](../../../docs/demo-evidence/nl-entry-new-demand.json)。
DEVELOPMENT_ONLY / UNREVIEWED，独立人工审核0；合成用例不代表真实Java或生产业务验证。
