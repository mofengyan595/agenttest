# 第四轮真实模型 A/B 证据（2026-11-08）

基线 8111a373 / 候选 a779ca56，冻结 `freeze.json`（sha256 见 `freeze.sha256`），共 16 个唯一首位（4 篇新需求 × 2 版本 × 2 轮），零重放、零重试、零停止。

| 文件 | 内容 |
| --- | --- |
| `freeze.json` / `freeze.sha256` | 冻结的排程、评分协议、预算与哈希 |
| `frozen-plan.md` | 冻结的四篇需求与独立参考答案 |
| `results.json` / `positions.csv` | 逐位置与分臂汇总分数 |
| `positions/<位置>` | 逐位置原始件：claim、terminal、run-*、audit、observations、score、intake |
| `baseline-observations/` / `candidate-observations/` | 每一次模型请求与响应的原文 |
| `model-usage-<臂>.json` | 产品用量台账：每次调用的 token、HTTP 状态、请求/响应哈希 |
| `target-audit.jsonl` | 目标服务写下的原始 HTTP 审计行 |
| `failure-index.json` | 逐位置未通过义务与诊断码索引 |
| `verification.json` | 冷重算一致性、用量核对、验收条件 |
| `rubric-defect.json` | F4 rubric 过严的披露与补充分析（非冻结分数） |

冷重算：`python recompute.py`（只用本目录证据与冻结评分器，不导入产品代码）。

## 必须先读的一处评估缺陷

F4 的冻结 rubric 多写了一条原文并未要求的公告读取，因此 F4 每个位置必然少两个义务，两版对称。冻结分数原样保留；`rubric-defect.json` 给出补充分析，结论不变。
