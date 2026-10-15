# 登录复用与第三方跨接口链路（2026-10-15）

## 本轮范围

保留 P304 首次重复 bob 登录导致的失败及历史成绩，不在旧题上重跑调分。
组合规划现在向所有任务提供同一份 authentication_rules：每个可见角色最多登录一次，
需要认证时在首次受保护操作前登录，后续步骤复用该角色的状态。另一个角色登录不会
覆盖它。类型检查对同角色重复登录返回 MODEL_SCHEMA_COMPOSED_AUTHENTICATION；
不自动删除、补登录、排序或重试，不放宽原 DSL／Policy／Intent 校验。
这些改动补齐规划提示和诊断；本轮四条新任务的首次真实生成均通过，属于小样本开发证据。

准备四条新的[组合开发任务](../examples/composed-authentication-development.json)：
P401 bob 创建与 alice 拒绝访问，P402 反向角色，P403 三目标组合，P404 单角色读取。
文字未复用旧题，但语义属于已有受控任务族，不能称为独立泛化样本。
新增任务继续排除在正式独立审核题集之外。

## 匿名资源依赖

原绑定校验要求生产者与消费者都带非空角色，匿名 REST 接口无法串联。
新增操作员显式配置的 role_relation=anonymous，只允许两端角色都为 null。
未指定关系、same、different 的旧规则仍要求身份；混合匿名与具名角色不能借此通过。
生产者策略、已发生步骤、精确选择器、操作白名单、目标地址及任务完整性检查继续生效。
候选发现复用同一角色关系检查，候选本身仍不授予执行权限。

新的 [Petclinic 只读配置](../examples/portability/petclinic-chain/provenance.json)
复用 afc8fc1 的人工限定契约，新增两条任务：列表查询后分别使用实际响应第 1 项、
第 6 项的 ID 查询详情，核对响应 ID 与请求 ID 一致。没有假设列表排序或把种子 ID
写进计划；仅依赖全新 H2 有六条宠物类型的已核对前提。操作仍只有两个 GET，
无鉴权、业务写入、异步或上传。它不是第三方复杂业务或鉴权验证。

离线检查使用不同于种子值的乱序 ID，覆盖动态引用、错误详情 ID、空列表、字面 ID
替代引用、错误选择器及来源策略。Mock／Fake 结果只证明机制，不能算真实模型成绩。

已有 CLI 可以使用这些文件，无需新增执行器：

```powershell
$taskPath = 'examples/portability/petclinic-chain/first.task.json'
$task = Get-Content -LiteralPath $taskPath -Raw | ConvertFrom-Json
.\.venv\Scripts\python.exe -m agenttest agent --user-task $task.user_task --fake-plan examples/portability/petclinic-chain/first.plan.json --contract examples/portability/petclinic/openapi.json --target examples/portability/petclinic-chain/target.json --policies examples/portability/petclinic-chain/policies.json --task-contract $taskPath --runs-dir runs/petclinic-chain
```

上述 CLI 会访问真实本机目标；本轮临时实例已关闭，重新启动需在对应隔离授权范围内。
离线检查可直接运行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_composed_authentication.py tests/test_anonymous_dependencies.py tests/test_petclinic_chain.py --basetemp=runs/pytest-authentication-chain
```

## 真实验收授权与方案

准备动作不启动 Java、不连接已有数据库、不调用真实模型。
原 Petclinic 授权的两次真实调用已用完，本轮不会自动沿用该额度。

操作员已明确答复“允许按此隔离方案验证”。批准方案：复用先前构建且 SHA-256 为
27780435e5d67e68b17dfd0e8b1c7e9a4c7787516ee8c3d1c682591867d0523c 的 JAR，
不下载或安装依赖。只绑定 127.0.0.1:18886，使用随机命名的全新进程内 H2，
关闭 H2 控制台与鉴权，限定子进程生存期并在结束后关闭。启动会写入隔离种子数据；
业务 API 仅 GET，不修改 CourseInsight 或上游业务源码。

先做两条固定计划真实 Java 验收，再冻结源码、策略、任务及准确出站内容。
最多六次 ecnu-plus 调用：P401–P404 四条进程内 mini 任务，以及 F101／F102
两条 Petclinic 任务，每题首次生成一次、无重试、无固定回复兜底。模型输入只含
公开契约与批准目录、合成任务，不发送账号、凭据、TaskContract requirements 或标准计划。
每次输入上界 20,000、输出上界 4,096，累计最坏预留 144,576 Token；
预留不是实际用量或费用。模型等待 60 秒，单任务总时限 120 秒；用量未知或服务异常停止。
固定 Java 验收最多 4 次业务 GET，真实模型 Java 链路最多另有 4 次，元数据查询另计。

完成后校验归档、按完整分母重新评分，保留首次失败和未尝试记录；不改断言求通过。
继续标记 DEVELOPMENT_ONLY／UNREVIEWED、human_reviewed=0、formal_eval_v2_score=null。

[AGENTS.md](../AGENTS.md) 要求 `New real Java database writes require explicit operator approval.`
本轮已取得新 H2 初始化和新增六次模型额度的明确批准。

## 实际结果

公共策略、身份安全、组合规划与跨项目流程回归 **302 passed / 221.28s**；
另补模型适配、诊断、批处理与评测完整性 **202 passed / 69.68s**。
这两组共 504 项，不与前期重叠的 116 项机制检查相加。模拟未装 LangGraph 的基础环境
**111 passed、9 skipped / 8.65s**，跳过仅为四条组合与五条匿名图执行项，其余检查仍运行；
没有安装或卸载依赖。Ruff、pip check、差异检查与 23 文件敏感信息核对通过。
本轮没有重跑整个仓库全量测试；提交后的 CI 按准确 SHA 另外核对。

新任务固定计划先完成四条进程内 mini 与两条真实 Java 链路，六条均 PASS；
Java 业务 GET 共 4 次。然后冻结源码、任务、策略、完整目录、准确模型请求及固定 JAR，
所有真实调用遵守同一预算，没有重试、自动修补或固定计划回退：

| ID | 任务 | 首次真实结果 | Token | 业务请求 |
| --- | --- | --- | ---: | ---: |
| P401 | bob 创建、读取与 alice 拒绝访问 | PASS | 3,041 | 5 次进程内请求 |
| P402 | alice 创建、读取与 bob 拒绝访问 | PASS | 3,057 | 5 次进程内请求 |
| P403 | 一致性、权限与批处理三目标 | PASS | 3,443 | 9 次进程内请求 |
| P404 | bob 创建与读取一致 | PASS | 2,859 | 3 次进程内请求 |
| F101 | 列表第 1 项 ID → 详情 ID 一致 | PASS | 1,023 | 2 次真实 Java GET |
| F102 | 列表第 6 项 ID → 详情 ID 一致 | PASS | 1,026 | 2 次真实 Java GET |

四条组合任务 **4/4** 完成，登录步骤对每个所需角色仅一次；两条第三方跨接口任务
**2/2** 完成，ID 使用实际列表响应绑定。六次 ecnu-plus 请求均 HTTP 200、用量已知，
合计 **14,449 Token**，其中组合任务 12,400，第三方 2,049。两个任务族分别计数，
不与旧题合并计算改善幅度或任意项目成功率。正式评分仍为 null。

固定与真实批各六条完成封印校验、报告摘要及完整分母重新评分；归档位于忽略目录
`runs/optimization-20261015/baseline/` 与 `real/`。真实模型驱动 Java GET 为 4，
加固定验收的 4 次，共 **8 次 Java 业务 GET**；元数据请求另计，API 写入为 0。
初始化 H2 写入已明确批准，不能把 API 只读写成数据库完全无写入。

首次准备运行器将两份只有空白格式不同的契约按原字节比较，误报 LIVE_CONTRACT_DRIFT，
在业务与模型调用前停止并关闭实例。已确认 JSON 内容和解析契约完全相同，仅修正私人
记录器的比较方式，保留初次记录；未放宽生产导入器、请求校验或断言。
最终核对两个自有 Java 进程均退出、18886 端口无监听；临时 H2 随进程销毁。
原始回复、凭据、源码缓存、服务日志与私人运行器不提交。

本轮证明了显式匿名资源依赖及两条真实第三方只读链路，仍未验证第三方鉴权、业务写入、
复杂链路、异步或多轮稳定性。下一准确里程碑是预先冻结少量新的跨项目任务并重复三轮，
分别记录任务完成、失败类型、耗时和已知用量；开发诊断与正式独立评测继续分开。
