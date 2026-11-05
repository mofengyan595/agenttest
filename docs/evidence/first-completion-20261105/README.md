# 冻结48位置证据

只用于评测侧，不参与产品规划。真实结果包含失败、未知范围、错误验收配置和环境限流，不能当作成功演示集。

- `evidence.zip`：冻结全文/配置、48份认领与首次任务结果、实际Run报告、封存后资源审计。没有模型密钥或凭据值。
- `manifest.json`：原冻结SHA、归档与225成员的SHA、两个源提交及配置错误裁定。
- `results.json`：逐要求对账、失败原因、用量、实际请求见证。正确缺口与首次完成分开，未将错误配置裁定为业务缺陷。
- `audit.py`：标准库冷核对；验证归档、成员、原文/配置SHA及48位置后重算，不访问网络或调用模型。

从仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe docs/evidence/first-completion-20261105/audit.py
```

输出位于`runs/first-completion-cold-audit/results.json`。除`--output`可指定结果目录外，没有业务执行入口。
原始规划失败不被修补，评价内容错误列名不被改写，全部分母保留。
汇总口径、逐题说明和最终交付修复的版本边界见[交付报告](../../first-completion-delivery.md)。
