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
