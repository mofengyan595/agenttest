# 开发进度

- [x] 独立 Python 项目骨架
- [x] 独立 .venv 创建脚本（需在本机运行）
- [x] FastAPI 健康检查
- [x] OpenAPI 3.x JSON 最小接口摘要
- [x] 三个基础测试用例
- [ ] 阶段 1：安全契约解析、DSL、Mock target、Executor、Verifier、报告（进行中）
- [ ] 阶段 2：LangGraph Agent
- [ ] 阶段 3：MCP 与 Java 后端真实测试
- [ ] 阶段 4：效果评测
- [ ] 阶段 5：产品化

本文件由 Codex 在每个阶段完成后根据实际测试结果更新，禁止提前标记任务完成。

## 2026-10-11 阶段 1 检查点

- 项目本地 Python 3.11.15 和 `.venv` 已建立，依赖仅安装在 `.venv`。
- 原有测试：`python -m pytest -q -p no:cacheprovider` → **3 passed**。
- 已完成安全契约摘要：OpenAPI 3.0/3.1 JSON/YAML、局部引用、参数、请求体、响应、安全要求。
- 拒绝远程/文件引用、循环引用、重复键、超大输入、过深嵌套与别名循环。
- 模块验证：`python -m pytest -q tests/test_contracts.py tests/test_parser.py` → **15 passed**；`python -m ruff check src tests` → **All checks passed!**
- 已知提醒：当前 Starlette TestClient 对 httpx 发出弃用提醒，测试通过。
- 下一模块：目标隔离策略与人工编写的测试计划 DSL；尚未实现执行器或完整闭环。
- 本检查点对应本地提交 `feat: implement OpenAPI parser`；SHA 在下一检查点登记（避免提交内容自引用）。

### 本地提交记录

- `71e25e5`：契约解析及原始骨架。
- 原始 OpenAPI JSON fixture 随目标隔离与 DSL 模块加入版本管理。

### 目标隔离与 DSL

- 完成固定 IP 目标、localhost 固定到 127.0.0.1、操作白名单、隔离写入授权和预算配置。
- 第一阶段不支持 DNS 主机名，防止校验后 DNS 重绑定；拒绝公网、元数据、未授权私网。
- 完成 Scenario / Step / ArgumentBinding / AssertionPolicy / ExpectedStatus，支持前序步骤绑定。
- 预检未知操作、循环/前向依赖、角色、环境凭据、路径参数、媒体类型及危险表达式。
- pytest：**39 passed**；Ruff 首轮发现一处超长行，已修复，提交前复查。
- 下一模块：Verifier 与可脱敏、可审计的报告。

### Verifier 与报告

- 前一模块提交：`87a5c3d`（目标隔离与 DSL）。
- 完成人工策略来源与 reviewed 门禁、状态/结构字段/条数断言，未知预期为 NEEDS_REVIEW。
- 报告保存契约/计划/策略摘要、步骤运行 ID、结果分类；不保存原始请求体、响应体或认证头。
- 7 个针对性测试通过；Ruff 首轮一处超长行，修复后复查通过。
- 下一模块：HTTP Executor，包含超时、只读重试、轮询、CSV 与请求证据。

### HTTP Executor

- 前一模块提交：`c94ce7e`（断言及报告）。
- 完成 HTTPX 执行、连接/读取/整次运行超时、请求预算和间隔、只读重试、JSON/CSV multipart。
- 所有重定向直接阻断；关闭环境代理、清空跨角色 Cookie、路径值仅允许安全标识符。
- 每个 run 独立状态；发送请求前落盘步骤运行 ID，不提供自动恢复或写入重放。
- 保存每次请求状态码、响应长度/摘要、耗时及安全错误码；异常文本不落盘。
- 针对性 pytest：**7 passed**；Ruff：**All checks passed!**。
- 下一模块：FastAPI mini target、登录/创建/查询/上传/轮询链路与两个故障样本。
