# 开发进度

## 审查修复：OpenAPI 请求约束（2026-10-11）

- 前一模块：`8ab94c5`，策略与认证隔离。
- 请求校验覆盖名称、严格基本类型、required、enum、嵌套对象/数组、长度、数值及独占边界、int32/int64 和只读字段。
- 静态值在预检校验，动态绑定与执行器注入的登录值在发送前校验。
- 仅支持明确的 Schema 子集；联合、组合、正则、开放对象、未知 format 等进入 NEEDS_REVIEW，不声称已验证。未声明的字段拒绝。
- 实测：130 passed，1 个已有弃用提醒；Ruff 通过；真实回环 HTTP 8 步 / 9 请求 PASS，三个 API 检查均 200，临时服务关闭。
- 本模块 SHA 在下一检查点记录。剩余 P0：轮询分类及数值/自定义敏感字段报告；阶段 2 尚未开始。

## 审查修复：策略与认证边界（2026-10-11）

- 基于发布提交 `d8a3b5ca8779930b6b3e55071dccde919578f17e` 继续，保留全部历史。
- 不可信 DSL 只引用 policy_id；独立 Registry 校验接口、角色、可信业务场景、数据绑定前提，未知策略在 I/O 前 NEEDS_REVIEW。
- 删除普通参数 credential 和 auth_from；批准的登录操作由执行器注入凭据，Token 留在每次运行的角色 Vault，认证响应禁止跨步骤绑定。
- 加入伪造审核、策略作用域、审批快照、认证响应与秘密别名泄露回归。
- 实测：94 passed，1 个已有 Starlette 弃用提醒；Ruff All checks passed。
- 真实回环 HTTP：8 步、9 请求 PASS，health/spec_import/report_query 均 200；临时服务关闭。
- 本模块提交 SHA 在下一检查点记录。尚待：请求 Schema 校验、轮询分类、全面脱敏；尚未开始阶段 2。

- [x] 独立 Python 项目骨架
- [x] Python 3.11 独立 .venv 与安装脚本（已在本机运行）
- [x] FastAPI 健康检查
- [x] OpenAPI 3.x JSON 最小接口摘要
- [x] 三个基础测试用例
- [x] 阶段 1：安全契约解析、DSL、Mock target、Executor、Verifier、报告
- [ ] 阶段 2：LangGraph Agent
- [ ] 阶段 3：MCP 与 Java 后端真实测试
- [ ] 阶段 4：效果评测
- [ ] 阶段 5：产品化

本文件由 Codex 在每个阶段完成后根据实际测试结果更新，禁止提前标记任务完成。

## GitHub Public 发布检查（2026-10-11）

- 已创建空的 Public 仓库：[mofengyan595/agenttest](https://github.com/mofengyan595/agenttest)。
- 保留原有 9 个提交及全部 SHA；发布前基准为 `08bad70b33abb973469bd08c76395c38238f655f`。
- 已审查原有 39 个已跟踪文件、全部 9 个提交中的 62 个文件版本及提交元数据。
- 扫描候选已核对为测试占位内容；未发现真实 API Key、密码、Token 或业务数据。
- 提交作者邮箱已核对并获得公开授权，未改写任何提交历史。
- `.venv/`、`.python311/`、`.env`、`runs/`、缓存和产物未进入当前或历史版本，忽略规则已验证。
- 发布前重新运行：pytest **78 passed, 1 warning**；Ruff **All checks passed!**；pip check **No broken requirements found.**
- README 增补公开仓库获取步骤，并明确模拟服务也使用项目 `.venv` 启动。
- 本次新增发布文档提交；推送及远程 SHA 的最终核查结果在本次交付回复中提供。
- **阶段 2 暂停在未开始状态；下一步先完成代码审查，等待用户后续开发指示。**

## 阶段 1 最终验收（2026-10-11）

**完成。** 实现提交：`8b79f00f6c2f7c131c792a5652f46e3eba65886a`。
环境/文档交付随后独立提交，不在文件中自引用其 SHA，可使用 `git log -1 --oneline` 查询。

| 实际运行 | 结果 |
| --- | --- |
| `.venv/Scripts/python.exe -m pytest -q` | 78 passed, 1 warning，无跳过 |
| `.venv/Scripts/python.exe -m ruff check .` | All checks passed! |
| `.venv/Scripts/python.exe -m pip check` | No broken requirements found. |
| `scripts/setup_env.ps1` | 实际执行成功；当时 74 tests + Ruff 通过，之后引用回归增加至 78 |
| `.venv/Scripts/python.exe scripts/smoke_local_http.py` | CLI run 真实 HTTP：8 步 / 9 请求 PASS；health/spec_import/report_query 均 200；服务已关闭 |
| `.venv/Scripts/python.exe -m agenttest demo` | PASS，8 步，退出码 0 |
| `.venv/Scripts/python.exe -m agenttest demo --fault ownership` | ASSERTION_FAILED，cross_user，第 5 步，退出码 1 |
| `.venv/Scripts/python.exe -m agenttest demo --fault terminal` | ASSERTION_FAILED，poll_batch，第 7 步，退出码 1 |

实际报告（本地产物，已忽略不提交）：

- 真实 CLI HTTP：`4370658b5fe44d5b8275405cdf4420b8`。
- 离线正常：`66160569edde409b85104c8028ea1c45`。
- 归属故障：`f5aebf44130f4bcbafcf9eeae5e586cd`。
- 终态故障：`5ad9d0c65eff45a1bfa46e221b5a4dc1`。
- 位置均为 `runs/<run_id>/report.json`；报告导出另有 CLI/API 测试覆盖。

本地功能提交：

| SHA | 内容 |
| --- | --- |
| 71e25e5 | OpenAPI 解析与原始骨架保存 |
| 87a5c3d | 目标隔离、角色与 DSL 校验 |
| c94ce7e | 确定性断言与脱敏报告 |
| b93dcc6 | 受限 HTTP Executor |
| e21d721 | 模拟 target、完整链路与两个故障 |
| 80e9ce7 | CLI 与契约/报告 API |
| 0e17f4d | 标准契约校验、安全回归与断网测试 |
| 8b79f00 | 引用展开预算、真实 CLI HTTP 验证 |

环境：Python 3.11.15；精确依赖见 `requirements-dev.lock`。
本机缺少独立 Python 3.11，使用项目内忽略的 `.python311` 运行时支撑新建 `.venv`。
未访问或修改 CourseInsight；未配置远程仓库，未推送。

已知限制：仅 IP/localhost、HTTP bearer、JSON/CSV 和受限 JSONPath；拒绝递归/动态引用。
尚无外部测试数据自动清理、写入幂等恢复或真实服务接入。
Starlette 对 TestClient 使用 httpx 有一条弃用提醒，未抑制，测试通过。
macOS/Linux 安装脚本尚未实际执行；Windows 脚本本次验证的是已有 `.venv` 分支，新建环境已在开发开始时手动完成。
初次依赖安装需要包源或预先准备的 wheel 缓存；运行与默认测试不需要外网。

**下一准确里程碑：阶段 2 首个可验证模块。** 先查询当时官方 LangGraph 文档，
实现 fake LLM 的 `load_contract -> plan -> validate -> setup -> execute -> verify -> analyze_or_finish -> report`，
验证非法 JSON、未知操作、工具故障、预算耗尽和不篡改断言；暂不接真实模型、MCP 或 Java 服务。

以下保留各模块开发检查点，测试数量按当时实际结果记录。

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
