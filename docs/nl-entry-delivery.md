# 自然语言入口：运行与验收

本轮允许用户在注册项目上直接输入支持范围内的测试目标，无需为每条需求写完整步骤、资源偏序或
TaskContract。它仍要求操作员事先审核契约、隔离目标、账号别名、业务断言、资源关系和目标词汇。
系统采用受控语法，不承诺任意自然语言、任意 API 或零配置接入。

## 三层演示

以下命令在仓库根目录执行。`--real`使用已配置供应商，产生模型费用；业务目标均为新建的专用
ASGI/内存应用，没有真实 Java、PocketBase 或数据库写入。`.env`只在本机，不提交。

```powershell
# 单接口：正常、枚举和合法长度边界；缺少错误响应预期的非法输入列待审
.\.venv\Scripts\python.exe -m agenttest goal `
  --project examples/portability/tickets/registered.project.json `
  --user-task '测试创建工单接口。' --suite --synthetic-target `
  --real --model-env .env --model-timeout 60 --runs-dir runs/demo-goal-single

# 流程：认证/动态ID/关闭后的状态策略由项目关系派生
.\.venv\Scripts\python.exe -m agenttest goal `
  --project examples/portability/tickets/registered.project.json `
  --user-task '先创建工单，然后关闭并查询。' --synthetic-target `
  --real --model-env .env --model-timeout 60 --runs-dir runs/demo-goal-flow

# 项目：目录分组，共享总预算，未执行组保留在分母
.\.venv\Scripts\python.exe -m agenttest goal `
  --project examples/portability/tickets/registered.project.json `
  --user-task '测试这个项目已授权的核心接口。' --synthetic-target `
  --real --model-env .env --model-timeout 60 --runs-dir runs/demo-goal-project
```

项目与单接口套件出现待审/预算排除时退出1；不能把退出1都理解为产品异常或接口缺陷。
工单六组需要11次业务请求，原目标只授权10次，列表组不会执行。合法样例通过也不表示非法输入
测试已完成。只需预览时改用`--preview`；无服务端账号的套件预览会列凭据待审，不发送请求。

需要有界调查时，在项目级注册中引用已批准的`capabilities.json`，不由浏览器或模型临时授权。
本轮冻结评估的两份可复用配置保存在归档`projects/`，断言和授权不变；它们补充已有公开用途及
已核对的GET能力。上述基础示例不启用调查，不把两个配置版本混为同一统计。

## 无Key复核

默认`python -m agenttest showcase --case all`沿用原离线演示。完整新入口离线排期通过真实JSON
适配器接MockTransport，固定回复验证机制，不能当真实模型成绩。运行新副本时使用全新输出目录：

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_nl_entry.py --output runs/new-entry-offline
```

该命令要求干净提交，执行固定12需求各三轮，不需要Key、Docker或外网。复核已有归档只需
`scripts/evaluate_nl_entry.py --verify <归档目录>`，不会重放业务或调用模型。
首次新需求归档使用`scripts/check_nl_entry_usage.py --verify <归档目录>`冷核对；其回归由原固定
pytest模板执行，配置fixture从项目与原文重新派生门禁，不手写TaskContract。

## 工作台与任务服务

启动方法见[能力与服务说明](nl-entry.md)。默认无任务注册时仍能无Key读取历史报告；显式启用后
可在`/tasks`选择项目、输入需求、补充/澄清、查看约束与预算、接收进度、取消后续动作、查看分层
证据及下载pytest。只有项目策略要求审阅才等待批准。

SQLite只保存脱敏元数据、状态、约束、用量与报告引用。原文和导出候选限有界内存；补充需求新建
计划/任务，共享剩余额度。默认串行、最多八个排队或运行任务；独占锁防止双进程重启误标活跃任务。
取消不承诺回滚，重启后未完成任务标INTERRUPTED，不恢复HTTP或重放写入。浏览器不能提供URL、
凭据、策略或代码；同源、回环、HttpOnly/SameSite会话及CSRF仅用于本地模式，不等于公网认证。

本轮浏览器验收使用Fake/ASGI；真实供应商验证通过CLI/同一核心引擎进行，未把界面验收算真实模型。
原生Function Calling仅有Mock协议证据；实际供应商排期使用已有JSON协议。

## 已保存证据

[原三轮及边界](nl-entry-value-report.md) · [首次无模型回归](../examples/regressions/NE_NEW01/USAGE.md) · [逐项核对](nl-entry-completion-audit.md)。

工作台截图来自Fake/ASGI浏览器验收：[桌面](demo-evidence/nl-entry-desktop.png)、[390px窄屏](demo-evidence/nl-entry-mobile.png)。
