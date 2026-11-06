# 需求见证真实对照归档

`evidence.zip` 保存冻结输入和预期、逐位置登记及任务结果、实际运行报告、陌生项目接入收据。`manifest.json` 保存归档、每个成员、评测器和结果文件的 SHA-256。`results.json` 是基于实际 HTTP 和检查证据的逐位置判定。

从仓库根目录在新目录解封并重新判分。这个过程不发网络请求。

```powershell
Expand-Archive -LiteralPath docs/evidence/requirement-witness-20261106/evidence.zip `
  -DestinationPath runs/requirement-witness-cold-check
$env:PYTHONUTF8 = '1'
.\.venv\Scripts\python.exe scripts/audit_requirement_witness_comparison.py `
  runs/requirement-witness-cold-check
```

如需另开实际对照，使用 `freeze.json` 和 `freeze.sha256` 作为只读输入，启动两版独立工作台并复建冻结文件所指向的本机隔离实例。准备 Gitea v1.26.3 的 `archivist` 与 `reviewer` 只读身份，`archivist` 名下有 `field-notes` 公开仓库和 `sealed-register` 私有仓库。Petclinic 使用交付报告中的固定版本与原始 H2 种子。服务端提供真实模型及身份配置，工作台端口按冻结文件设置。运行 `node scripts/run_requirement_witness_comparison.mjs <新验收目录>` 会使用实际模型。历史位置及其原始成绩保留。

[实验范围和交付说明](../../requirement-witness-delivery.md)
