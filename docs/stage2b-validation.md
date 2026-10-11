# 阶段 2B 最小调查验收

本次只使用独立 mini target、FakeLLM/FakeAnalyzer 和合成数据，学校 API 调用 0。原两轮评测及 T01–T18 保留，不进行第三轮或反复调优。

## 真实本地 HTTP

命令：`.venv/Scripts/python.exe scripts/smoke_investigation_http.py`。两次运行均使用真实回环 TCP，检查返回报告与磁盘内容一致、工作流完成，临时服务结束后关闭。

| 故障 | 原结果 | 总 HTTP | 调查 HTTP | 创建 | 登录 | run ID |
| --- | --- | --- | --- | --- | --- | --- |
| none | PASS | 5 | 0 | 1 | 2 | c391061aacd14d8eaddcc7c5d9a4a369 |
| ownership | ASSERTION_FAILED | 6 | 1 | 1 | 2 | 4506ffc1ffc747ddbcd1fbfe3488098d |

归属故障原 other_read 的 403 断言仍失败（实际 200），补充调查选择 course_owner_read、复用 alice 认证读取同一创建资源，返回 200、调查 PASS；没有将原结果改成 PASS。

调查 attempt ID：`27181d313db54f1090cba0c6058910fb`，请求耗时 **5.639ms**，响应 **58 字节**，SHA-256 `e2c8301a2e088207423c60acdaa2ce2d1afe2a34a52355db0198745922c82723`。预算消耗为 1 个预留请求、1 轮、1 次 FakeAnalyzer 调用；HTTP 未重试。报告位于本机忽略目录 runs/investigation-http，不公开 HTTP 正文或凭据。

同时验证原 workflow 8 步/9 HTTP PASS（health、spec_import、report_query 均 200），原自然语言 Fake 路径 3/3 与 5/5 HTTP PASS。它们只验证现有执行链路，不代表新增自然语言泛化能力。

## 离线反向测试范围

- 原 PASS、计划预检拒绝、模型弃权、工具预算为零均无调查；创建未通过时不生成可信资源引用。
- 名称/ID 断言失败只读取原创建响应绑定的资源，原断言和失败证据不变。
- 伪策略、任意 URL/Header/原始 ID、断言注入、写操作、未批准 GET、状态推进 getBatch、越权角色及跨 run 句柄均在调查发送前拒绝。
- 独立请求/轮数/模型调用/时间任一为零均停止，不借用原请求预算；发送前保存唯一 attempt ID、未知结果码及预算消耗。
- 原结果、目标、契约、策略及预算在分析前后冻结校验，HTTP 后也检测漂移。
- 超时、运输异常、工具异常、异步取消、认证过期和报告故障不会重新登录、创建或重发未知请求；会话 finally 清除 Vault 和响应绑定。
- 报告节点失败也不覆盖原业务失败；永久报告故障只保证此前成功落盘的证据。恢复测试只读取旧报告，执行器拒绝所有已有请求的 run。

最终 pytest/Ruff/依赖检查、功能提交 SHA 和四组 CI 实际状态记录于 [开发进度](progress.md)。操作系统硬杀进程及物理内存擦除未测试；实现不支持自动恢复、真实 LLM 调查、自动修复或重规划。
