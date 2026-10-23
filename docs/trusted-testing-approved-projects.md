# 原两域的项目级负例批准

本次新增配置放在 `examples/trusted-testing/projects/`，没有覆盖历史配置、预算或归档。
每域六份可复用文件：契约、隔离目标、策略、公开目标目录、注册信息、只读能力。
`tickets_limited` 是工单的请求预算变体，不算第四业务域。

## 可信错误预期

课程沿用 `src/agenttest/mini_target.py` 的 `CourseInput(name: str)`；新增两项 name 缺失/类型错误批准。
工单沿用 `src/agenttest/ticket_target.py`：subject 长度1–80、priority low/high；更新 state 长度1–20。
新增11项批准：创建7（subject四项、priority三项）、关闭4（state四项）。这些输入由 Pydantic/FastAPI
在进入业务处理前拒绝，审核后的错误断言为 HTTP 422 且 `$.detail` 存在。
仅在受控 ASGI fixture 中使用，不由 OpenAPI 自动推断真实服务错误语义；没有扩展到真实Java/PocketBase。

正常 policy 的业务闭包仍有效：关闭只允许 state=closed。负例先按有效闭包验证，再由确定性执行器
变异批准字段；不能把路径ID、角色、凭据、资源来源或其他字段当成负例逃逸通道。
priority 错误类型同时违反 type/enum 时必须明确批准两个违例，不能只批准其中一个。

## 开发验证与配置成本

使用旧需求执行 `scripts/check_trusted_profiles.py`，没有运行新12条验收文本。
同一 OpenAI 兼容适配器接 MockTransport，usage=100输入/50输出/150总Token，均为模拟数据。

| 旧需求路径 | 场景 | 实际ASGI请求 | 模拟模型调用 | 结果 |
|---|---:|---:|---:|---|
| 课程创建单接口 | 3 | 6 | 3 | 完整 |
| 工单创建单接口 | 10 | 10 | 10 | 完整 |
| 课程项目 | 5 | 13 | 5 | 完整 |
| 工单项目 | 17 | 26 | 17 | 完整 |
| 工单项目预算8 | 17 | 8 | 8 | 部分执行，保留未执行分母 |

本次原两域各复用六配置文件，基础策略4/6，新增负例批准2/11；资源关系、角色与状态规则沿用已有审核。
增加 GoalSpec 待审开关和预算配置，单run4模型/2调查，父任务20模型/500000Token，请求通常64、受限版8。
输入/输出上界20000/4096，单run总Token100000。新增需求不需手写步骤/偏序/逐题TaskContract；
首次接入仍需审核契约、策略、错误语义、角色、依赖选择器、隔离环境和预算。
脚手架/测试/评分参考是额外工程成本，不能当作零配置或人工节时证据。

```powershell
.\.venv\Scripts\python.exe scripts/check_trusted_profiles.py --output runs/trusted-profiles-demo
```

输出目录必须是新目录；默认无真实模型和外部业务请求。第三域成本见[实验室接入记录](trusted-testing-laboratory.md)。
