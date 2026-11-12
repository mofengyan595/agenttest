# P3-A Deep Agents Skills、上下文与运行时 CI

## 实现和版本

- 起点 `8ff8457544fe190535a2d4c26d9e26028e7950db`。实现提交为 `57b4fd7b96af73129f077fcd0069004a0d48cf14`。
- 验证的运行时组合为 Python 3.11、Deep Agents 0.7.35、LangChain 1.4.10、
  LangChain Core 1.7.7、LangGraph 1.2.20。正式安装入口是
  `python -m pip install -c requirements-deepagents.lock '.[dev,deepagents]'`。
  旧 `agent` extra 锁定另一版 LangGraph。CI 为两种配置使用独立环境。
- 业务入口继续由 `ProductTasks` 调用同一个 Deep Agents 图。Planning IR、Policy、Validator、
  Executor、资源清理记录和 `agenttest.task-completion.v1` 提供可复核结果。Web 和 CLI
  请求及返回接口保持兼容。

## Skills 和权限

随 wheel 发布三份 `skills/<name>/SKILL.md`，每份含 `name`、`description` frontmatter
及操作指南。通过 `create_deep_agent(skills=["/skills"])` 注册。官方中间件从
`StateBackend` 发现元数据，把名称、描述、虚拟路径放进首轮系统消息。模型选择后，用官方
`read_file` 把选中正文作为 ToolMessage 加载。实现以 0.7.35 安装包中的
`graph.py`、`middleware/skills.py`、`middleware/filesystem.py` 为准。
[官方 Skills 文档](https://docs.langchain.com/oss/python/deepagents/skills)

| Skill | 方法 |
| --- | --- |
| `api-functional-testing` | 根据已批准义务选分组。优先完整生命周期，上线前再处理独立分组和批准的参数检查。 |
| `api-regression-testing` | 核对批准的删除与回读策略，要求同一资源的存在、删除、回读和清理证据。 |
| `api-failure-investigation` | 优先报告和只读观测，按 `run_id` 复核。分别记录断言失败、环境故障和规划拒绝。 |

官方 `FilesystemMiddleware(tools=["read_file"])` 只注册原生读取工具。虚拟后端仅初始化
三份随包文件，官方权限规则先匹配精确允许的路径，随后拒绝所有其他读写。关闭 Skills 时
虚拟目录为空，读权限全部拒绝，基本领域工具仍可用。探针每轮用同样的建图配置验证模型视图，
并实际派发越界读取、写入、修改、Shell 和委派调用。工具边界返回错误，随后核对磁盘探针文件。
产品图另外核对实际 dispatcher 只绑定八个领域工具与原生读取器。
[官方权限文档](https://docs.langchain.com/oss/python/deepagents/permissions)

## 上下文与 P2.1 遗留缺口

- 保留 LangChain `ContextEditingMiddleware` 和 `ClearToolUsesEdit`，阈值仍为 8,000
  估算 token，保留最近四条结果与体积小的 Skill 正文。保留 Deep Agents 自带摘要中间件的
  默认配置。本轮有强制触发工具结果清理的集成证据，单独的原生摘要触发以实测轨迹为准。
  [官方 Context Engineering 文档](https://docs.langchain.com/oss/python/deepagents/context-engineering)
- 契约和已批准策略的重复读取跨执行轮返回同一个内容引用，避免重新计算及完整传输。动态
  作用域和覆盖按证据代际更新，引用附当前预算与最近报告 ID。旧上下文被清理时，可用
  `refresh=true` 重取。覆盖工具保留未测义务、失败诊断及资源状态。报告按
  `run_id` 和步骤分页，支持按操作过滤。完整报告与确定性断言继续持久化。
- 工具结果使用紧凑 JSON，长结果优先保留错误和报告引用。原生 LangGraph 状态流用于保留
  运行中已经取得的工具轨迹，即使末尾模型错误。适配器额外记录实际输入里的 Skill 发现、
  正文、工具输出字节数与清理计数。
- Focus 对 `FOCUS_REQUIREMENT_ORDER_INSTANCE_INVALID` 提供一次原校验链路内的纠正。
  结构化反馈带原引用、可用动作及从零开始的索引。首轮拒绝与纠正各自记账。再次不通过时保留
  `NEEDS_REVIEW`。开发实测发现旧的全部就绪接口回退读取了未赋值的变量，已改为在无法
  验证 Focus 时结束本轮。
- 显式重复读取问题涉及两个环节。前置主语使“连续读取两次”未被识别。Legacy 构建器又把
  无依赖的两个相同读取拆成独立用例，第二个命中去重。现在识别带主语或身份前缀的明确次数，
  并把数量与请求身份都匹配的已批准只读观测编成一组。历史用例实际发出
  `GET /build → GET /build → GET /account`，对应 xfail 已移除。无效重复仍受语义指纹约束。
  字面量守卫诊断差异保留原 strict xfail。
- 适配器在发模型请求前重新读共享预算，Focus、Design 与自主图使用同一任务额度。用量未知
  后停止后续调用，保留未知用量和既有证据。

## 验证

- 本地定向回归 `110 passed`。覆盖 Web API、CLI、产品入口、Planning IR、Focus 两种纠正
  结果及可选依赖下的 Deep Agents 运行。
- 专项 GitHub Actions job 安装项目正式 extra，检查依赖一致性与 wheel 中的 Skills。通过
  Fake ChatModel 的原生 `AIMessage.tool_calls` 与真实 `ToolMessage` 验证工具 Schema、
  参数错误、模型与工具预算、取消、拒绝、异常、权限、Skills 和上下文。已有 Linux/Windows
  × base/agent 矩阵保留。独立 Windows 3.11 wheel 安装通过三份 Skill 的包内容检查，
  `pip check` 通过，专项清单 `80 passed`。该环境还实际安装了 LangSmith 0.14.17、
  langchain-anthropic 1.7.10、langchain-google-genai 4.4.3、google-auth 2.62.2、
  certifi 2026.10.23。远端状态以 [GitHub Actions](https://github.com/mofengyan595/agenttest/actions/workflows/ci.yml?query=branch%3Amaster) 为准。
- `python scripts/check_full.py --jobs 4 --output runs/full-checks/p3a` 收集并核对全部
  2,789 个测试终态。2,773 passed、15 skipped、1 strict xfail、0 failed/errors。
  源码指纹 `6508a0444048228ced38cbdb40c7c2357c4c55af14a62aa23542438dc9a5bd48`，运行期间 HEAD 和工作区保持稳定，
  进程级 Git 忽略规则排除了已有的本机 `.venv-lock/`。完整审计在
  `runs/full-checks/p3a/result.json`。
- `python -m ruff check --extend-exclude .venv-lock .` 与 `git diff --check` 通过。

## 真实 ecnu-max

最终批在实现源码冻结后逐字输入四条目标。每条使用新进程、新回环端口和新包裹靶场，串行执行，
各自上限为 16 次模型调用、30 次业务请求、420 秒，单次模型输出上限 8,192 token。
靶场、已批准断言及用户目标沿用 P2.1。P2.1 数据取自最终批四个原始归档。

| 目标 | P2.1 模型调用 / 总 Token | P3-A 模型调用 / 总 Token | 工具调用 P2.1 → P3-A | 再次读取 P2.1 → P3-A | 业务 HTTP P2.1 → P3-A | 已测义务 P2.1 → P3-A | 严格完成 P2.1 → P3-A |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 帮我测一下包裹管理功能。 | 12 / 90,290 | 16 / 119,422 | 11 → 12 | 2 → 1 | 9 → 3 | 7/7 → 2/7 | 是 → 否 |
| 删除功能刚改过，帮我回归一下。 | 8 / 50,322 | 16 / 112,714 | 6 → 13 | 0 → 1 | 4 → 5 | 4/4 → 3/4 | 是 → 否 |
| 包裹接口偶尔报错，帮我看看。 | 1 / 12,061 | 9 / 59,392 | 0 → 8 | 0 → 1 | 0 → 8 | 未进入 → 6/30 | 否 → 是 |
| 这个服务准备上线了，帮我测一轮。 | 19 / 131,741 | 10 / 58,926 | 13 → 10 | 1 → 2 | 10 → 1 | 5/7 → 0/31 | 否 → 否 |
| 合计 | 40 / 284,414 | 51 / 350,454 | 30 → 43 | 3 → 5 | 23 → 17 | 按各轮原分母报告 | **2/4 → 1/4** |

模型调用和 Token 取各轮任务账本，含 Focus、Design 和自主运行时。P2.1 报告表中引用的
55,157、22,084、73,996 是相应运行时子账。P2.1 上线前轮的全任务账本记了 19 次，
高于配置的 16 次，本轮的共享预算检查将最终批各轮限制在 16 次以内。工具调用取真实模型返回
的工具名称，包含被拒调用。再次读取统计契约、策略、作用域、覆盖工具各名称超出首次的次数，
包括有新证据后的合理读取。HTTP 取任务账本，靶场事件文件另含启动和收尾健康检查。
各轮覆盖分母由已验证 Focus 和可信义务产生。P3-A 上线前轮纳入了更多获批义务，报告按实际
分母保留，未测量的事实不变。调查目标按其独立完成判据核对相关证据和调查记录。

四轮首个自主运行时模型输入均发现三份 Skill 的元数据，正文为零。每轮只加载一份相符正文。

| 目标 | 真实加载 | 正文加载后的可观测决策 |
| --- | --- | --- |
| 功能 | `api-functional-testing` | 给 `run_test_group` 的设计约束要求绑定同一包裹，做更新、回读和清理。 |
| 删除回归 | `api-regression-testing` | 读取批准策略，并在设计约束中引用批准的删除后回读规则、同一 parcelId 和前置存在性。回读义务最终未测。 |
| 偶发错误 | `api-failure-investigation` | 取得执行证据后按真实 `run_id` 调用 `investigate_failure`，保留 `readAvailability` 预期 200、实际 503 的失败。 |
| 上线前 | `api-functional-testing` | 读作用域并尝试补测。两个设计被 `MODEL_TOKEN_BUDGET` 拒绝。 |

轨迹证明了按需读取及之后的工具选择。没有做随机化有无 Skill 对照，无法单独估计其对质量或
成本的因果贡献。最终批四次上下文清理计数均为零，强制触发时的恢复由无密钥集成测试覆盖。
契约读取从三次降为一次，上线前一次同代际作用域读取返回了引用。功能轮最大单次 Agent
请求从 41,105 降到 37,779 字节，删除回归从 23,975 升至 37,263 字节，上线前从
34,620 降至 31,834 字节。任务总 Token 增长，上线前较低成本伴随零义务测量。

最终批创建四件资源，四件均取得 `CLEANED`，四个靶场最后存量均为零。未观察到写重放或
未授权工具执行。删除回归使用两个不同新资源，第二个用例复用了一部分前置流程。
功能和回归多次设计因 `PLANNING_INSTANCE_UNBOUND` 被预检拒绝。调查发现的断言失败与
靶场预设 503 一致。最终批观察到零误报，样本只覆盖此隔离靶场。

开发批和最终批分别留存。首个开发批四轮均中断、零业务请求。其中一轮命中新增错误码遗漏，
三轮命中旧 Focus 回退错误，两次网关用量未知。第二个开发批严格完成 1/4，功能轮测量
7/7、取得清理凭据后在未知用量处停止，已知部分 35,069 token，完整用量 UNKNOWN。
回归和调查均在 Focus 拒绝处停止。上线前轮 0/7，归档含规划和 JSON 错误。

共享核对数据见 [P3-A 运行证据](evidence/deepagents-p3a/summary.json)。本机完整证据在
`.eduwork/runs/p3a/`，每轮保留 `run.json`、目标事件、模型规划输出及报告。

## 未达验收项和下一步

最终批严格完成率由 2/4 降为 1/4。上线前义务覆盖未改善。下一里程碑针对设计阶段的实例映射，
用固定案例分别验证同资源正常读、删除后缺失读、按批准策略选择独立检查，再在新隔离靶场
冻结一批目标复验覆盖与成本。当前证据支持 Skills 接入与安全边界，尚不支持自主测试质量提升。
