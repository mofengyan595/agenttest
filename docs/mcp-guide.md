# 阶段 3B：从 MCP 客户端调用 AgentTest

MCP 负责让客户端发现工具、按 Schema 传参数、取得结果。AgentTest 仍负责决定哪些测试被批准，以及怎样安全地执行。这里的 Server 没有接入 LLM，也不会执行模型生成的 Python 或命令。

本模块使用官方 **MCP Python SDK 2.3.0**，核对日期 2026-10-11。v2 的入口为 `MCPServer`，客户端为 `Client`。依据：[工具和 Schema](https://py.sdk.modelcontextprotocol.io/servers/tools/)、[stdio 与内存客户端](https://py.sdk.modelcontextprotocol.io/client/transports/)、[中间件](https://py.sdk.modelcontextprotocol.io/advanced/middleware/)、[错误处理](https://py.sdk.modelcontextprotocol.io/servers/handling-errors/)。

## 安装与启动

在 AgentTest 源码根目录，沿用已有 Python 3.11 `.venv`，仅增加可选 MCP 依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-mcp.lock
.\.venv\Scripts\python.exe -m pip install --no-build-isolation --no-deps -e '.[dev,mcp]'
.\.venv\Scripts\python.exe -m agenttest.mcp_server
```

Linux 对应解释器为 `.venv/bin/python`。首次安装需要包源或 wheel 缓存；安装后本模块无需 Key、Docker、外网或 Java。MCP 也可以与 agent extra 同时安装：`.[dev,agent,mcp]`。

启动后没有网页或监听端口，进程等待 MCP 客户端从 stdin 发来协议消息，stdout 只用于协议。直接在终端运行而没有输出是正常现象；用下文测试客户端验证，不要往 Server 的 stdout 添加 `print()`。

典型客户端的本地 stdio 配置如下，路径按实际安装位置调整：

```json
{
  "mcpServers": {
    "agenttest": {
      "command": "D:\\Desktop\\project\\agenttest\\.venv\\Scripts\\python.exe",
      "args": ["-m", "agenttest.mcp_server", "--max-runs", "3"]
    }
  }
}
```

这个配置不需要 `env`、API Key 或 JWT。不同客户端配置格式可能不同，但 command/args 启动的是同一个 stdio 进程。当前实现按源码目录加载已审核的 demo fixture，尚未做脱离源码树的独立 wheel 分发。

## 五个工具

| 工具 | 参数 | 结果与边界 |
| --- | --- | --- |
| `list_tests` | `{}` | 场景 ID、允许操作、剩余运行预算；明确 CourseInsight 关闭 |
| `get_contract` | `{}` | 筛选后的操作、参数/请求体约束、鉴权要求、角色别名、策略适用范围、DSL Schema |
| `validate_plan` | `plan_json` 字符串 | 只做 JSON → Pydantic DSL → OpenAPI / TargetConfig → Policy Registry / 依赖校验；零业务 HTTP |
| `run_scenario` | `scenario_id`, `invocation_id` | 运行操作员预先批准的固定场景；不能执行客户端提交的 DSL |
| `read_report` | `run_id` | 只读取本 MCP 报告目录内已完成的脱敏报告，不恢复运行 |

`get_contract` 是公共操作视图，不是可直接导入的完整 OpenAPI 文档。它沿用 Planner 的筛选器，去掉服务器地址、示例、自由描述、默认业务数据与账号环境变量；登录请求体由 Vault 管理，不公开其凭据 Schema。附带的 DSL Schema 中可以包含 DSL 自身安全的默认值。

已批准场景只有三个，全部使用独立、一次性、进程内 mini target：

| scenario_id | 预期执行证据 |
| --- | --- |
| `mini_normal` | 8 步 / 9 次请求，PASS |
| `mini_ownership_fault` | 5 步 / 5 次请求，归属权限断言失败 |
| `mini_terminal_fault` | 7 步 / 8 次请求，异步终态断言失败 |

故障场景是现有 mini target 故障注入样本，沿用同一套批准的策略和 DSL。MCP 不开放任意故障配置，也不降低断言来使它们通过。每次运行创建一个合成课程，结束后内存数据丢弃；外部 Java 和真实业务数据不会参与。ASGI 在内存里处理 HTTP 请求，不占用 8080 端口；报告中的回环 origin 是执行器的逻辑地址。

## 实际调用示例

工具调用的 `arguments` 是 JSON 对象。例如：

```json
{"name":"list_tests","arguments":{}}
```

```json
{"name":"run_scenario","arguments":{"scenario_id":"mini_normal","invocation_id":"214b15060b2344f4927cd7f56c690db9"}}
```

```json
{"name":"read_report","arguments":{"run_id":"214b15060b2344f4927cd7f56c690db9"}}
```

上面的 ID 来自本次 stdio 验收，验收临时目录已销毁，示例用于说明调用格式。自己运行时生成新的 `uuid4().hex`，然后使用执行结果返回的 `run_id` 读取报告。报告中的检查值继续按 Verifier 规则脱敏，只有明确批准的公共数值可见。

官方 SDK 客户端可以直接执行下列 Python 示例（在源码根目录运行）：

```python
import asyncio
import sys
from uuid import uuid4

from mcp import Client, StdioServerParameters


async def main():
    process = StdioServerParameters(
        command=sys.executable, args=["-m", "agenttest.mcp_server"]
    )
    async with Client(process, read_timeout_seconds=45) as client:
        print([tool.name for tool in (await client.list_tools()).tools])
        executed = await client.call_tool("run_scenario", {
            "scenario_id": "mini_normal", "invocation_id": uuid4().hex
        })
        if executed.is_error:
            print(executed.structured_content["error_code"])
            return
        report = executed.structured_content["report"]
        print(report["run_id"], report["outcome"])
        saved = await client.call_tool("read_report", {"run_id": report["run_id"]})
        assert saved.structured_content["report"] == report


asyncio.run(main())
```

客户端里的 `print()` 输出给用户，Server 的 stdout 才是协议通道。无需手工实现 JSON-RPC 的初始化、消息 ID 或帧读写，这些由官方 SDK 完成。

## 阅读源码时从哪里开始

1. `src/agenttest/mcp_server.py`：五个普通异步函数成为工具，SDK 提供发现和 stdio 传输。发布的输入 Schema 来自 strict Pydantic 模型；中间件在 SDK 校验前拒绝越权字段，避免 SDK 的错误消息回显输入值。
2. `src/agenttest/mcp_service.py`：授权入口。只加载本项目 demo 契约、目标与可信策略，产生一次性合成账号；忽略 `.env` 和进程中的真实凭据。校验入口不调用 `Executor.run()`，执行入口只接受批准场景 ID。
3. `Executor.run()`：原有 HTTP 引擎，继续执行预检、请求预算、Vault 登录、动态资源引用、请求前审计、Verifier 断言及报告保存。MCP 没有另写 HTTP 请求循环，也没有启用失败调查。
4. `ReportStore`：原有脱敏报告存储。MCP 增加专用目录和执行前占用记录，使重发同一工具调用不能重发写请求。

可以把流程读成：客户端选择场景 → MCP 校验工具参数 → 服务端查询批准目录 → Executor 再做原有安全预检 → Vault 管理身份 → HTTP 执行和确定性断言 → 脱敏报告。工具注解里的 readOnly/idempotent 是给客户端的提示，实际安全限制由服务端强制执行。

## 授权、预算和不可重放

工具参数严格禁止额外字段，不接受任意 URL、HTTP 方法、Header、Key、JWT、策略正文、断言、业务预期或预算。通过 `validate_plan` 只说明计划符合现有规则，不授予执行该计划的能力。CourseInsight 场景不存在于目录，CLI 也没有打开 Java 写入的选项。

操作员可在启动进程时设置 `--runs-dir`、`--max-runs`（0–20，默认 3）、`--max-tool-calls`（1–1000，默认 100）。这是本机启动配置，MCP 参数不能修改。每次 run 固定最多 8 步、10 次 HTTP、30 秒；保留现有请求/响应大小和逐请求超时。包括拒绝请求在内的 tools/call 消耗工具预算；发现协议消息不消耗业务请求预算。

默认报告在 `runs/mcp/`，不能读取其它入口的 Java/LLM 报告。对 `invocation_id` 要求 32 位小写十六进制 UUID。服务端在任何业务请求前，用原子目录创建占用该 ID，并写入场景 ID；Executor 仍在每次 HTTP 发送前记录 attempt ID。

- 同 ID、同场景、已完成：返回原报告，`cached=true`，零 HTTP。
- 同 ID、更换场景：`MCP_INVOCATION_CONFLICT`。
- 同 ID、中断或报告缺失/未完成：`MCP_RUN_NOT_RESUMABLE`，不得自动恢复、重发创建或重新登录。
- 新 ID：代表新的显式运行，占用新的运行预算。客户端不应把失败调用自动换 ID 重试。

进程重启后会话预算重新计算，但原占用目录仍阻止旧 ID 执行；当前没有跨进程累计额度服务。运行中只有内存保存认证状态，结束时由 Executor 关闭 Vault。占用记录只有场景 ID，报告不保存请求/响应正文、密码、JWT 或认证头。原业务结果不可更改，调查和自动修复都没有开启。

协议工具错误使用 `is_error=true` 和固定错误码。成功执行故障场景则是 `is_error=false`，报告 `outcome=ASSERTION_FAILED`：这是业务测试发现故障，不是 MCP 调用故障。未知策略保留 NEEDS_REVIEW，绝不虚报 PASS。

## 如何验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_mcp_service.py tests/test_mcp_server.py -q
.\.venv\Scripts\python.exe scripts/smoke_mcp_stdio.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pip check
```

默认 pytest 禁止外网和 DNS；SDK 测试使用内存 Client，缺少可选 SDK 时明确跳过。stdio 验收另行启动真正子进程，覆盖发现、Schema、校验、三个场景、报告、同 ID 重发及越权拒绝，临时报告目录自动删除。

四组 CI 保持 Windows/Linux × base/agent；agent 组安装 MCP 并运行 stdio 验收，base 组不要求 MCP。CI 不发送学校 API 调用或真实 Java 写入。原有 452 项测试、两轮真实模型结果和 Java 3A 记录继续保留。

本轮没有客户端提交计划的执行授权流程、远程 MCP HTTP 部署、Java MCP 能力、真实 LLM 调查、恢复运行、自动清理外部资源或独立 wheel 分发；扩大这些能力需要单独审核。

本轮全量本机结果为 **508 passed、1 skipped**，跳过原因是本机符号链接权限。对应验收提交 `0d93152` 的 Windows/Linux agent CI 均 **509 passed**，base 均 **424 passed、6 skipped、1 warning**；Ruff、pip check、stdio 与回环检查通过。[四组 CI](https://github.com/mofengyan595/agenttest/actions/runs/38144292792) 已核对日志。
