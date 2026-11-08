# F4 复验证据包（2026-11-08）

本目录是 12 个真实首位（3 条新需求 × 2 个版本 × 2 轮）的**原始证据**，由
`scripts/f4_recheck/` 在冻结后一次跑完，未重跑、未重试、未改评分。入口文件：

| 文件 | 内容 |
| --- | --- |
| `freeze.json` + `freeze.sha256` | 冻结清单：代码 HEAD、两版源码 SHA、端口、预算、任务、评分协议、评估器哈希 |
| `schedule-claim.json` / `schedule-terminal.json` | 起跑声明与 12 条终态记录（含 stop 字段，必须为 null） |
| `results.json` / `positions.csv` | 独立评分汇总与逐位位置指标 |
| `positions/<id>/` | 每个位置的 `claim`、`intake`、`submission`、`terminal`、`audit`、`observations`、`score` |
| `baseline-observations/` `candidate-observations/` | 模型原始请求/响应/执行计划（`raw_text`、`decoded`、`decode` 状态） |
| `target-audit.jsonl` | 夹具侧 12 个位置的真实 HTTP 审计（身份、状态码、状态哈希） |
| `preflight.json` | 22 项身份/响应/intake 预检（0 次模型调用） |
| `*-execution-preflight-*.json` | 两臂各 2 条真实审批流的执行预检（含独立评分） |
| `*-scorer-preflight.json` | 两臂各 8 个确定性负控，逐条断言必须被检测到 |
| `evaluation-tests.xml` | 评估侧测试的 JUnit 记录（冻结时计入哈希） |
| `sealed.json` | 155 个文件的 SHA-256 清单 |
| `evidence.zip` / `evidence.sha256` | 上述内容的打包与校验值 |

复核方式：`python scripts/f4_recheck/collect.py summary runs/f4-recheck-20261108`
会重新计算每个位置的分数，并与存档 `score.json` 逐字节比较；任何差异都会报
`COLD_RECOMPUTATION_DIFF`。本轮结果为 `cold_recomputation=MATCHED`。
