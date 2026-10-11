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

### 离线模拟链路

- 前一模块提交：`b93dcc6`（HTTP Executor）。
- 完成独立 FastAPI mini target、固定 JSON 场景、角色环境配置及示例策略说明。
- 正常场景 8 步、9 次请求：双账号登录、创建、查询、跨用户拒绝、CSV、两次轮询、结果条数。
- `ownership` 与 `terminal` 两个独立故障均被对应断言检出；重复运行互不污染。
- 针对性 pytest：**4 passed**；Ruff 首轮两处超长行，修复后复查。
- 仅在 `.venv` 增加 python-multipart 0.0.32，用于 mini target 的 CSV 接收。
- 下一模块：CLI、安全文档导入及报告查询 API、启动验证、完整文档与冻结依赖。

### CLI / API

- 前一模块提交：`e21d721`（模拟链路及故障样本）。
- CLI 支持离线 demo、显式配置目标运行人工计划、查询/导出脱敏 JSON 报告。
- API 保留 /health 与原 JSON 包装格式，增加原始 JSON/YAML 和读取时大小限制。
- 提供只读 `/api/runs/{run_id}`，无任意目标执行 API；错误不会回显不可信文档。
- 包含原有接口的针对性 pytest：**7 passed**；Ruff：**All checks passed!**。
- 下一步：安全边界补充回归、真实本地 HTTP 验证、README 与安装脚本、全量质量门禁。

### 安全边界回归与真实 HTTP

- 前一模块提交：`80e9ce7`（CLI / API）。
- 加入标准 OpenAPI 文档校验，先展开并限制本地引用，再验证；不交给验证器远程引用。
- 补齐原始示例/测试文档的响应及路径参数定义，原有行为断言保持不变。
- 加入超时、请求体上限、轮询耗尽、Cookie 隔离、未知策略停止、数值密码脱敏回归。
- 全量测试：**74 passed, 1 warning**；Ruff：**All checks passed!**。
- 默认测试禁止 DNS 和网络连接；Windows 标准库内部 socket pair 除外。
- 断网夹具首轮误拦截 Windows asyncio 内部通信，已精确放行并全量重测通过。
- 显式真实 loopback 验证：8 步 / 9 请求 PASS，health/spec_import/report_query 均为 200。
- 实际 HTTP 验证 run：`737dba777bc649fd860e8aa4e98b15b8`，临时服务均已关闭。
- 下一步：安装脚本与 README、冻结依赖、交付版本记录；阶段 2 不在本次实现范围。

### 引用解析最终检查

- 前一模块提交：`0e17f4d`（安全回归及真实 HTTP 检查）。
- 增加引用展开后的字节预算；拒绝动态引用和会改变引用语义的约束兄弟节点。
- 实际 CLI run 经真实 loopback 读取 JSON 契约、人工计划与授权配置：8 步 / 9 请求 PASS。
- 新增 4 个引用边界测试后：**78 passed, 1 warning**；Ruff 通过；pip check 无损坏依赖。
- 正常 CLI demo 退出码 0，两个故障 CLI demo 退出码 1，分别停止在第 5 / 7 步。
