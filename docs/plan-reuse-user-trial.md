# 使用者体验待审包

入口已准备；真实参与者验证尚未完成。以下使用模拟模型和专用ASGI内存目标，不消耗模型额度，
不访问真实业务或数据库。模拟成功不作为用户价值证据，没有真实计时则不报告节省工时。

## 首次接入与体验

沿用README的本地Python/前端环境。批准配置在`examples/trusted-testing/projects/tickets`和`courses`，
注册文件引用契约、隔离目标/角色、业务策略、目标词汇及只读能力。接入者仍需审核错误语义和预算；
OpenAPI不是完整业务预期。支持的新需求不要求逐题手写TaskContract、资源偏序或完整步骤。

仓库根目录启动演示服务：

```powershell
.\.venv\Scripts\python.exe scripts/serve_plan_reuse_demo.py --port 18010
```

另一个终端启动前端：

```powershell
$env:AGENTTEST_API_PROXY = 'http://127.0.0.1:18010'
npm.cmd --prefix web run dev
```

打开`http://127.0.0.1:5173/tasks`，选择tickets，输入自己未参与开发的受支持新需求。
支持词汇包括创建、关闭、读回、权限拒绝和列出；勾选“包含Schema用例候选”可检查单接口变体。
查看派生来源、结构与业务状态、基础计划、未执行原因及回归导出。退款等未知业务预期应保留待审。
此入口明确标记SIMULATED，不用它评价真实模型自然语言质量。真实模型配置沿用`docs/nl-entry.md`，
本Goal不会因体验包追加付费追分。

## 三层命令演示及固定回归

输出目录须尚不存在；以下是工程演示，不是新的独立模型评测。

```powershell
.\.venv\Scripts\python.exe scripts/demo_plan_reuse.py --scope single --output runs/reuse-single-new
.\.venv\Scripts\python.exe scripts/demo_plan_reuse.py --scope flow --output runs/reuse-flow-new
.\.venv\Scripts\python.exe scripts/demo_plan_reuse.py --scope project --output runs/reuse-project-new
.\.venv\Scripts\python.exe -m pytest examples/regressions/plan-reuse -q
```

原阶段模拟记录为单接口1次规划/10请求、流程1/4、项目6/26。补齐项目正常边界后，
当前三条命令再次运行：单接口1/10、流程1/4、项目6/28，完整业务范围均满足，真实模型0；
新记录在`runs/plan-reuse-delivery-{single,flow,project}-v2/summary.json`。
此流程演示以权限读接口结束，没有可派生的字面量输入；在上述工作台输入“请验证创建、关闭工单流程”，
则可查看1个业务结构与4个批准负例、共10请求。该路径的模型调用陷阱测试已验证只规划一次。
单接口导出两份派生正常/负例资产，原模板逐字节复制；模型与真实HTTP禁用回归2 passed/1.86s。
回归报告operator_plan/task_satisfied=null，每次重新生成资源，不冒充第二次自然语言成功。

## 真实反馈

使用者须在操作前开始计时，保留原始需求、配置修改、修改次数、卡点、报告ID和回归资产。
同时说明结果是否有用及依据，不只记录PASS。与固定pytest比较时，须按相同覆盖实际编写fixture/断言并计时。
当前没有这些人类操作记录，独立人审、人工节时及真实用户价值仍未验证。
请使用`plan-reuse-user-feedback.md`记录，代理不联系他人或代签。
