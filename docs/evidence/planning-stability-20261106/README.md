# 规划稳定性真实验收证据

`evidence.zip` 包含 48 个首次位置的冻结需求、配置、预期、任务报告、实际请求和运行器。`manifest.json` 保存文件摘要。`results.json` 为逐位置离线判分。用量例外收据单独保存，原 UNKNOWN 未改写。

`recheck/` 使用相同结构，包含补修的六个真实位置和独立分数。

解封到新目录后可离线复核，无模型和目标请求。

```powershell
Expand-Archive -LiteralPath docs/evidence/planning-stability-20261106/evidence.zip `
  -DestinationPath runs/planning-stability-cold-check
$env:PYTHONUTF8 = '1'
.\.venv\Scripts\python.exe scripts/audit_planning_stability.py runs/planning-stability-cold-check
```

复核补修时，将归档改为 `docs/evidence/planning-stability-20261106/recheck/evidence.zip` 并使用另一个解封目录。

[源码范围、评分结果和边界](../../planning-stability-20261106.md)
