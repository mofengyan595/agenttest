# AgentTest — Codex starter

独立 Python 工程骨架，用来启动 AgentTest 的阶段性开发。**当前可运行的是健康检查与 OpenAPI 3.x JSON 基础摘要接口，不是完整测试平台。** 完整规划请看 `CODEX_TASK.md`，Codex 工作准则看 `AGENTS.md`。

## 环境：独立 `.venv`

建议安装 Python **3.11**（3.12 也可以）；项目不需要也不应使用全局 Python 环境。

### Windows PowerShell

```powershell
cd agenttest
.\scripts\setup_env.ps1
.\.venv\Scripts\python.exe -m uvicorn agenttest.main:app --reload
```

### macOS / Linux

```bash
cd agenttest
bash scripts/setup_env.sh
.venv/bin/python -m uvicorn agenttest.main:app --reload
```

也可以不使用脚本，手动创建：`python3.11 -m venv .venv`，然后使用 `.venv` 对应的 Python 运行 `-m pip install -e '.[dev]'`。

- 访问 `http://127.0.0.1:8000/health` -> `{ "status": "ok" }`
- 访问 `http://127.0.0.1:8000/docs` 查看自动生成的 API 文档。
- `python -m pytest -q` / `python -m ruff check .`（请使用 `.venv` 中的 Python）。
- 阶段 2 再安装 Agent 依赖：`pip install -e '.[agent,dev]'`（使用 `.venv` 的 pip）。

本 zip **不携带 `.venv`**：Python 虚拟环境通常依赖本机解释器路径，在你的电脑上运行脚本创建即可。

## 发给 Codex 的简短指令

> 请先阅读 `AGENTS.md` 和 `CODEX_TASK.md`，只实现“阶段 1”。首先创建/核验 Python 3.11 独立 `.venv`，让原有骨架测试通过，再完成安全 OpenAPI 解析、测试计划 DSL、Mock 被测 API、受限 HTTP Executor、Verifier 和 JSON 报告。每一步运行 pytest 和 ruff，修复错误后再继续。不要在第一阶段接大模型/MCP/前端，也不要修改 CourseInsight 仓库。执行结束给我实际运行结果、文件列表与演示命令。

## 已知范围

当前提供 `src/agenttest/contracts.py` 的最简 OpenAPI 操作摘要以及三个基础测试；契约安全解析、执行器、智能规划、MCP、故障注入、跨系统兼容性都尚未开发，需要 Codex 逐步实现。不要将目标架构描述成已经交付。
