# 第三项目：实验室存储合成目标

## 冻结与实际复用

核心先提交于 `88c9a3b4ff14454c3711b5bbea7f0d28b94d9d75`，
`src/agenttest` 树为 `371bc2dc910b4a63b80a9f5956453713239a9bd1`。
随后仅新增本项目契约、fixture、批准配置与开发验证；三条路径执行前后都核对核心无差异、无新增核心文件。
实际收据为 [第三项目离线证据](demo-evidence/trusted-testing-laboratory.json)，`core_unchanged=true`。
这是开发者专用合成环境的配置复用证据，不是独立人审，也不能推导广泛泛化能力。

该领域使用两个独立生产者：`makeSpecimen` 登记样本、`makeTray` 登记容器；
`placeSpecimen` 同时消费二者 ID 并生成第三种装载记录；`getPlacement` 再消费装载记录 ID，
并分别验证技术员可读、审查员被拒绝。含两个路径参数和不同响应选择器，结构不同于工单关闭流程。
没有新增逐题 TaskContract、标准步骤、任务映射或第三项目核心分支。

## 批准规则

仅专用 ASGI / 内存目标：正常创建 201，查询 200；审查员读取 403 / code=ACCESS_DENIED。
label 为 3–8 字符，aliquots 为 1–4 整数；zone 为 cold/ambient；position 为 1–8 整数。
输入错误明确批准 422 / code=INVALID_INPUT。这些错误语义来自本合成目标规则，不由 OpenAPI 推断。
15 项负例覆盖 body 字段必填缺失、错误类型、枚举外值和长度/数值上下越界。
装载负例先取得合法样本/容器 ID，只变异 position；路径和动态来源不参加负例变异。

原生 `goal --synthetic-target` 仍只接两份旧样例；此第三 fixture 由操作员验证脚本选择并传入同一
`run_suite`，没有改核心调度。普通 CLI 也可连接操作员启动的专用 loopback fixture，但本阶段未将该路径
冒充已完成的真实 HTTP 验证。提供的三层命令走实际 CLI 脚本、ASGI 和已有引擎。

## 首次接入成本

| 审核或配置项 | 数量/内容 |
|---|---|
| 可复用项目配置 | 6 文件：OpenAPI、target、policies、goals、registered project、只读能力 |
| 目标 fixture | 1 个专用 ASGI 文件，所有业务数据仅在内存 |
| 冻结元数据 | 1 文件，记录已存在的核心 SHA/树 |
| 接口/公开目标 | 4 接口，5 目标 |
| 基础策略/负例批准 | 5 / 15 |
| 角色及账号环境别名 | 2，凭据由 fixture 提供给执行器，不给模型 |
| 资源依赖 | 4 条批准关系：装载←样本、装载←容器、正常查询←装载、拒绝查询←装载 |
| 有界只读能力 | 1 个原策略的 GET 查询，最多沿用每 run 两次 |
| 本阶段新增需求手写步骤/偏序/TaskContract | 0 / 0 / 0 |
| 首次人工审核 | 输入约束、错误语义、资源选择器/角色、只读无副作用依据、隔离目标和预算 |

开发脚本和测试属于工程验证成本，不能从表中隐藏；配置数量不等于人工工时。
没有真实人工计时，因此 human_time_saved=null、independent_human_reviewed=false。

## 已执行与可复制演示

```powershell
.\.venv\Scripts\python.exe scripts/check_laboratory.py --output runs/laboratory-demo
.\.venv\Scripts\python.exe -m pytest tests/test_laboratory_portability.py -q
```

输出目录必须为新目录。使用原 CatalogFixturePlanner 的确定性 Fake 提案，不能计作模型规划收益。

| 路径 | 实际完整场景 | ASGI 请求 | 模拟模型调用 | 实际模型调用 |
|---|---:|---:|---:|---:|
| 单接口（登记样本） | 11/11 | 11 | 11 | 0 |
| 流程（双生产者、装载、查询与拒绝） | 1/1 | 5 | 1 | 0 |
| 项目 | 20/20 | 36 | 20 | 0 |

项目 4/4 接口实际执行，15/15 批准负例被正确拒绝；正常断言与拒绝断言均通过。
预算在执行前固定为 64 请求、20 模型、500000 Token，实际 Token 为模拟计数，不能当真实花费。
公共引擎仍按原规则拒绝未批准路径变异与未知“保存年限”业务要求。
另外 8 项 pytest（30.73s）覆盖三层路径、错误接受、读回错误、跨角色泄漏和待审出口；Ruff 通过。
真实模型、真实第三方业务请求或数据库写入 0。正式三轮新 36 位置尚未运行。

以上为阶段3原记录。后续6031212的固定36位置Mock预检在第8位置因通用输入上界超限停止，
真实排期仍未启动；[完整记录](trusted-testing-results.md)。该次预检核心与88c9一致，未放宽预算或断言。
用户随后批准通用提示精简的新里程碑，当前演示应显式传 `--core-sha HEAD` 选择新提交核心；原接入收据不改。
