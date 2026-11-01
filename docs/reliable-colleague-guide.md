# 可靠测试同事：使用与无界面复测

## 从干净目录启动

准备Python3.11虚拟环境与项目锁定依赖（Windows见scripts/setup_env.ps1），以及web目录
的锁定前端依赖。已有环境无需重新安装。首次编译网页后启动：

```powershell
cd D:\Desktop\project\agenttest\web
npm.cmd run build
cd ..
.venv\Scripts\python.exe scripts/start_colleague_lab.py --port 18420 --data runs/reliable-colleague/playground
```

打开 http://127.0.0.1:18420 。三个项目、接口文档和工作区会从源码初始化，不需要旧runs、
数据库或历史凭据；启动本身不调用模型。真实模型读取本机已配置.env；缺失时阻止新模型
测试，不回退模拟。预约靶场的专用随机凭据仅传给本次子进程，文档和网页不填写秘密。
目标端口18421至18423。冲突时换一组连续四端口和新的data目录，不停止其他服务。
`--prepare-only`可只准备项目与文档，0模型、0目标请求。初始化重复执行保留已有配置，
不会替操作者重新批准规则；同一data目录不可换端口。
关闭命令停止本次四个服务；配置、任务、回归保留，临时目标资源不保留。重启会轮换
专用凭据，依赖旧身份的回归需按新配置重新确认。遗留资源证据不会被抹掉或自动重放。

网页仍使用“选择项目 → 交代测试 → 看结果与依据 → 保存并再次测试”。写场景需要在接入
配置中确认创建接口每次产生新资源、允许配套接口清理本轮数据；没有完整生命周期时不能
保存为写回归。每轮资源和清理结果留在报告中，清理未完成时按证据人工核实，不能直接重放。

当前写回归要求明确的新建201、由本轮创建结果直接绑定的资源读取/修改，以及已批准的
配套清理。即使旧ID的读取获准，也不能混入可执行写回归；可将其单独保存为只读检查。
嵌套资源创建、上传、长轮询、读取重试等尚不开放写回归，页面会说明缺少的准备或清理。
模型规划仍可能漏掉独立场景的登录，或将独立读取排成遇错即停的顺序；这类情况明确显示
未完成并保留原记录，不自动修补计划、重放写入或修改断言。

## CLI 与 MCP 共用工作区

先启动正常网页工作区。CLI 连接其回环地址，不自行启动另一套任务执行服务。
以下均在项目根目录执行；将命令中的项目、任务、运行ID换成前一步返回的ID。

```powershell
.venv\Scripts\python.exe -m agenttest workspace projects
.venv\Scripts\python.exe -m agenttest workspace submit --project-id web_项目ID --user-task "创建专用数据，读取后清理" --real
.venv\Scripts\python.exe -m agenttest workspace status --task-id 任务ID
.venv\Scripts\python.exe -m agenttest workspace evidence --task-id 任务ID --run-id 运行ID
.venv\Scripts\python.exe -m agenttest workspace save --task-id 任务ID --run-id 运行ID
.venv\Scripts\python.exe -m agenttest workspace saved
.venv\Scripts\python.exe -m agenttest workspace regression --task-id 来源任务ID --run-id 来源运行ID --output runs/ci/result-本次唯一名称.json
```

`regression` 禁用模型；每次创建新任务、准备新数据，不依赖原运行资源。完整通过退出0，
断言失败退出1，待补充/过期/环境错误/查询超时退出2，用户中断CLI退出130。输出文件含
终态与脱敏报告，已有文件不会覆盖。请求失败不自动重试；提交前会打印防重复提交ID，
如需核查同一次提交可通过 `--submission-id` 复用，不能把新提交当作原写请求恢复。
等待超时后用status核查原任务，不意味着原任务已失败或可以重新写入。
历史TaskContract包缺少写生命周期时仅保留隔离ASGI/Mock样例复现；不能改选真实HTTP传输绕过保护。

项目要求审阅时，先查看status中的具体方案，再用
`workspace start --task-id 任务ID --plan-version 版本 --review-approved` 批准指定版本。
对已有固定方案，可在regression命令附加 `--review-approved`。此选项不适用于预先批准
尚未生成的新模型方案。取消使用 `workspace cancel --task-id 任务ID`。

默认地址18120，可在workspace后、子命令前添加 `--server http://127.0.0.1:其他端口`。
`--session-file` 指定本机会话.env文件；其中含访问凭据，不提交、不分享、不放入模型上下文。
默认在被Git忽略的runs/workspace-client内。同文件的CLI与MCP共享任务归属；不同网页或
客户端会话不能读取对方任务。会话文件绑定服务地址，不能拿到其他地址继续使用。

MCP使用项目现有的可选依赖，启动命令：

```powershell
.venv\Scripts\python.exe -m agenttest.workspace_mcp --server http://127.0.0.1:18120
```

默认可查看和运行模型禁用的固定回归；允许安排新模型测试时，启动命令显式附加
`--allow-real-model`。若操作员明确允许开始已经审阅的方案，另附加 `--allow-reviewed-start`。
工具参数不接受目标地址、凭据、断言、预算或路径；它们由已启动工作区和注册项目管理。
每个MCP进程默认128次工具调用，项目任务继续遵守服务端HTTP、模型、Token和配置版本预算。

实现前已核查[官方v2迁移说明](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/migration.md)
及[工具管理API](https://py.sdk.modelcontextprotocol.io/api/mcp/server/mcpserver/tools/tool_manager/)，
使用当前锁定的MCPServer，不安装或升级依赖，不引入另一执行引擎。

## CI

CI先准备已确认项目和已保存的回归来源，启动同一工作区，再执行上面的regression命令。
固定复测不需要模型密钥；目标必须是CI自己准备的隔离环境。普通离线CI中的
`tests/test_workspace_client.py`直接使用同一任务API的ASGI传输及模拟目标，验证报告、
退出码、所有权和过期阻止，无需模型、网络或Docker。真实模型开发/正式验收必须单独
显式开启，不混进默认离线通过数。

正式验收的零调用排期和显式执行命令：

```powershell
.venv\Scripts\python.exe scripts/run_reliable_acceptance.py --plan
.venv\Scripts\python.exe scripts/run_reliable_acceptance.py --real
```

新排期使用三个已冻结的新需求各三轮，通过正常网页提交。资料中的外部检查标准不提供给
产品模型；第一次结果和失败永不覆盖，输出目录已存在时拒绝重跑。任一未知用量会停止
后续位置，除非本Goal另有明确授权；不能用新目录绕过停止。旧任务仅作回归或性能证据。

有实际修正时，保留原排期，用相同冻结资料另存复验：

```powershell
.venv\Scripts\python.exe scripts/run_reliable_acceptance.py --real --output runs/reliable-colleague/acceptance-v2-reverification --reverification-of runs/reliable-colleague/acceptance-v1
```

它会核对原需求资料摘要，明确标记“同一任务复验”，不替换首次失败或增加新任务成绩。
