# 已接入项目到固定回归：新需求单次实跑路径

本路径沿用现有 `agent --project --export-regression`、ReportStore和只读控制台。
辅助脚本只为两个已批准的一次性ASGI项目提供transport和短期凭据环境，
没有新增业务服务、网络目标、模型生成代码执行或计划修复。

## 状态和职责

新增需求[竹湾交接](../examples/nl-reliability/new-usage.json)在开发/验证题中未使用，
五步结构与已用任务不重复。它在干净提交 `9c2f694a7dcc4a9ad06114595528930815951b43`
上做唯一真实尝试：5步原业务PASS、task_satisfied=true、1次模型/3080 Token、0调查，
完成导出；随后模型环境移除、模型和真实HTTP调用陷阱启用，无模型pytest **1 passed / 4.39s**。
原报告 `113e59676b4c4ef18de62f449f33028b`，回归报告 `6bb052f516094ba7bdc95a9d0e88cc6c`；
后者为operator_plan、task_satisfied=null、model_call=null，不冒充又一次自然语言理解。
[实际结果](demo-evidence/nl-usage-results.json)与原封印 `runs/nlr-usage-pending` 保留，
输入/报告/manifest字节摘要和两份实际报告均冷核对通过，没有Fake替代本新需求。

本轮由代理显式编写需求与场景映射，复用既有契约、目标、六项工单策略/断言、两角色和GET批准能力；
本需求显式映射5个步骤及4条偏序。整个开发/验证18题另有123步骤位置、105条相邻偏序，
两域共10项公开用途注释。生成TaskContract依赖这些显式配置，实际接入仍需操作员审阅和维护；
数量是本轮配置规模，不是已经完成的真实人工作业或人审统计，也不是自动理解后的独立正确答案。
`configuration-review.json`记录代理静态检查及内容摘要，标记UNREVIEWED，绝非独立人审签名。

模型接收原始需求、公开契约、批准策略与派生关系，不接收TaskContract标准步骤、
参考计划、故障标签或评分答案。错误计划原样拒绝；规则不会给模型补步骤或换策略。

## 可审阅和执行的命令

仓库根目录，已有项目虚拟环境与依赖；不需要安装新依赖。

```powershell
# 仅配置准备；输出必须是runs/内的新目录，不读取模型凭据
.\.venv\Scripts\python.exe scripts/nl_usage_path.py prepare `
  --case examples/nl-reliability/new-usage.json --output runs/my-new-demand
```

审阅输出中的 `project/task.json`（原文、五步、引用、偏序）、`policies.json`
（open/closed断言及公开用途）、`target.json`（隔离授权、角色和预算）、
`capabilities.json`（只读用途）、`openapi.json` 与 `configuration-review.json`。
任何输入变化都会使原静态检查摘要不匹配，在调用模型前停止；应重新准备并审阅。
短期合成凭据只在进程环境内生成，不写入项目文件。接入自己的服务仍需操作员提供
独立审阅的契约、目标、凭据、策略/断言和TaskContract，本脚本不授权真实目标写入。

在[已明确授权的验证后段](nl-validation-resumption.md)完成后，干净提交上进行一次真实尝试：

```powershell
.\.venv\Scripts\python.exe scripts/nl_usage_path.py real `
  --output runs/my-new-demand --model-env .env
```

执行前freeze记录输入、源码身份、公开模型参数和预算：1次规划、最多3次反馈、2次GET调查、
零隐式重试；模型60秒、输出4096、总25000，每请求输入沿用配置上限。
已有freeze拒绝再次执行。不成功就保留报告，不提供固定计划替代失败。

只有原业务PASS、全部意图满足、用量已知才导出bundle；随后移除模型环境设置，
使用固定可信pytest模板和人工ASGI fixture回归。fixture对模型调用和真实HTTP transport设置
失败陷阱，并检查实际新报告为operator_plan、PASS、task_satisfied=null、没有模型统计。
回归不声称再次完成了自然语言理解，不恢复原会话，不重试原写请求。

已有开发题ND02仅用于适配器离线验收；新需求没有参与开发Mock或调参。
正常/配置变更拒绝/不完整计划拒绝/已有临时目录保护四项集成检查在18项关联验收内通过。

## 直接使用已导出的无模型资产

[NU01回归目录](../examples/regressions/NU01)包含原CLI导出的bundle、配置静态检查、
原八项项目文件、人工ASGI fixture与来源摘要。bundle四文件逐字节保留，manifest绑定原真实报告；
测试代码来自固定可信导出模板，模型只提供JSON计划。fixture复用隔离工单target，
每次创建新目标和短期角色凭据，不读取.env，不访问真实数据库。

```powershell
.\.venv\Scripts\python.exe -m pytest examples/regressions/NU01 -q `
  --basetemp=runs/pytest-my-nu01
```

临时目录必须是自己可清理的专用路径；pytest会管理它，不要指向用户数据。
回归仍使用原TaskContract及逐步策略断言，缺少/改变配置或断言失败会报错。
全部四个已导出场景可用 `python -m pytest examples/regressions -q` 离线运行。

## 查看原业务、模型与规则

[七份展示报告及来源](demo-evidence/nl-examples.json)保留正常、规划拒绝、症状、证据不足和超时。
保留原五份，追加本次真实新需求和真实NV11候选证据不足报告；原Mock示例仍明确标注，
真实证据不足示例中的模型不当主张由规则拒绝，不记模型答对；展示选择不替代全72位置成绩。

```powershell
$env:AGENTTEST_RUNS_DIR = 'docs/demo-evidence/nl-examples'
.\.venv\Scripts\python.exe -m uvicorn agenttest.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers
# 另一终端：Set-Location web；npm run dev
```

打开只读[本地控制台](http://127.0.0.1:5173/runs)，可逐层查看：

1. 原业务Outcome和实际断言；检测故障时仍为ASSERTION_FAILED。
2. 模型选择的工具、提案、引用及主动弃权。
3. 规则独立支持的可观察症状与满足条件的证据；不足为NEEDS_REVIEW。
4. 建议被拒绝、策略/引用不匹配或证据不足的原因。

示例ND05模型取得证据后主动弃权，而规则支持访问边界症状：产品结论与模型成绩分别保留，
不能将规则补足记为模型答对。持续回读要求同策略，不混用open与closed预期；
所有结论限于观察到的症状，不推断服务内部实现根因。控制台没有业务执行或模型调用按钮。

完整[真实对照结果](nl-validation-completed.md)为产品29/36对31/36，模型结论9/18对8/18；
正常差距由首基线超时产生，主动弃权未改善。不能据此宣称全面提高。
`ticket_list_v1`仅验证HTTP200及items存在；list步骤不是列表归属过滤正确性的证明。
新需求CLI→导出→无模型回归闭环已完成；人工节时、独立人审、通用泛化均未验证。
