# 公开静态证据边界

此目录只保留经核对的脱敏展示副本，不是私有运行目录的镜像。
已有两份副本的来源字节与摘要不变；新增 PocketBase 汇总使用固定 LF，不重写原归档或历史成绩。

| 文件 | 来源与用途 | 限制 |
| --- | --- | --- |
| [batch-smoke.json](batch-smoke.json) | `0e684b2` 的单次真实模型 mini batch smoke；按原归档文件／报告摘要选取任务、用量、计划结构及失败证据 | DEVELOPMENT_ONLY / UNREVIEWED；原模型正文未保留，无完整 Eval v2 意图分数，不是 Java 结果 |
| [candidate-ab-summary.json](candidate-ab-summary.json) | 已有开发 A/B comparison 的摘要；保留原来源 SHA-256、两组统计、配对结果和预算 ledger | 两个已使用样本，没有独立留出或显著性；不包含私有任务全文、模型请求包或原始上下文 |
| [pocketbase-live-summary.json](pocketbase-live-summary.json) | 两个官方 PocketBase 隔离实例，固定六题和十八个真实模型位置；保留 16/18、两次格式拒绝、68,800 Token、归档摘要与只读数据库数量核对 | DEVELOPMENT_ONLY／UNREVIEWED；无生产数据、凭据、原始响应或独立评测成绩，不并入旧样本成功率 |

公开副本不包含真实密码、Key、JWT、Cookie 或原始 HTTP 正文；步骤 ID、合成需求、数字统计和摘要用于追溯。文档获准公开不等于业务标签独立审核，也不能凭报告、历史案例或模型自报的 reviewed 字段取得执行许可。

原始 `runs/`、私有 Suite、完整报告／上下文、预检、claim、候选压缩原型和备份继续私有保存；本次只读核对，不改写原成绩。RAG 的 [12 条开发问题与冻结结果](../../experiments/rag_necessity/results.json)独立归档，不与这里的 Candidate A/B 成绩合并。
