# AgentTest 新 Goal：交付检查点

目标：隔离测试环境中的可审计智能 API 测试与回归平台。按阶段完成后冻结功能；
不改 CourseInsight，不新增真实 Java／PocketBase 数据库写入，不引入 RAG、多 Agent、
自动修复或写请求恢复。阶段性验证后提交至现有 master／origin/master，不强推。

## 当前核实与实施顺序

1. 已有 LangGraph Workflow、Pydantic DSL、Policy／Intent／Executor／Verifier，
   参数槽位和前序引用检查；最近 GR1103 根响应 1/3，旧拒绝缺少具体分支证据。
   先补固定脱敏诊断，用确定性样例定位，再决定是否需要改出站约束。
2. 已有同一 Executor 会话中的一次只读调查，操作员 capability、动态不透明句柄、
   原断言不可变检查。缺少“调查或停止→新证据→再次决策”的有界反馈循环与结论门禁。
3. 已有 discovery、project.v1／check-project、脱敏报告和模型用量；缺少配置草案及
   项目复用与任务新增的独立成本口径、贯穿规划与调查的追踪。
4. 已有冻结、封印、冷重评分和独立人审门禁；需要全新 24 题×三轮开发评测与固定
   pytest 对照。独立留出与人工审核未完成时保留 DEVELOPMENT_ONLY／UNREVIEWED。
5. 已有只读控制台、统一九项交付入口、固定 pytest 导出及历史真实 HTTP 证据；
   需将新闭环并入五分钟演示，整理可追溯简历和面试材料。

## 模型预算与外部证据

原 Goal 总额为 300 次真实请求、1,000,000 Token；用户于本次会话明确更新“模型预算不限”，
取消该人工总额上限。仍固定每阶段任务、调用预算及排期，串行、零隐式重试；
每个 run 最多两次调查、四次模型（包含规划和最终结论）的安全上限不变。
每阶段先冻结输入、顺序、超时和更小预算；尝试先记账，用量未知停止后续付费调用。
旧 GA1002 中断的未知用量原样保留，不能合并成本或退款重发。

2026-10-18 核查[供应商配额文档](https://developer.ecnu.edu.cn/vitepress/llm/limit.html)：
credits 与 Token 分开，输入缓存和峰谷时段影响计费；余额需登录开放平台查看。
用户在本次会话重新查询并提供 52.13／20,000 credits，重置时间为北京时间
2026-10-19 09:18；本次接收时间约 2026-10-18 11:21。它是用户当前查询值，
不是程序实时余额接口；后续以供应商失败／usage 和本地硬预算执行停止。
不将 credits 与 Token 合并统计，不把价格估算当实际扣费。

## 阶段 1：已完成，后续提交 CI 持续核对

新增引用诊断只包含封闭分支码和从零开始的步骤／参数位置，不保存被拒绝计划、
selector、来源 ID 或原始模型文本。保留原 MODEL／POLICY 分类和异常码。
没有新引用问题时 Diagnostic 的序列化保持旧结构，保护历史报告摘要。

验证：首轮默认临时目录权限错误（17 passed、17 setup errors、1 deselected）；
指定仓库内临时目录后异步 setup 停滞，已停止该测试。换用普通本机测试进程，
仍保留禁网夹具，专项 **34 passed、1 deselected／10.62s**。
扩大回归第一次引用了不存在的 tests/test_policies.py，零测试执行；核对文件后重跑：
**135 passed／165.58s**，包含三个新任务的三基线／九模拟位置及冷核对、原引用任务、
参数槽位、模型、Policy／Intent 与能力注册表。Ruff 全仓与 pip check 通过。
未改提示、出站 Schema、批准断言或旧任务。

真实排期：GD1201 嵌套／GD1202 数组／GD1203 根响应，各三轮，先固定三条基线，
再按任务顺序串行三轮；最多九次 ecnu-plus，预留最多 216,864 Token，每次超时 60s，
零重试。业务仅 Mock；失败、未知用量与未尝试位置保留。该上限包含在 Goal 总额中。
全部输入、代码和请求在运行前固定；结果待执行后追加，不能预先算成功。

```powershell
.\.venv\Scripts\python.exe scripts/check_argument_slots.py --diagnostics --output runs/goal-stage1-live --model-env .env
.\.venv\Scripts\python.exe scripts/check_argument_slots.py --verify runs/goal-stage1-live
```

下一项：完成诊断回归、全新样例和归档验证，固定阶段 1 结果后进入反馈闭环。

### 已确认的根响应分支与修正

`3f2c110` 的 GD 九次真实调用全部已知用量，共 **12,679 Token**；三基线通过，
生成 **6/9**：嵌套／数组各 3/3、根响应 0/3。三次拒绝均为原
NEEDS_REVIEW／POLICY_DEPENDENCY_MISMATCH，新增诊断均
SELECTOR_POLICY、step_index=1、argument_index=0，零业务请求。
来源策略及接口已通过本地检查，因此能确认 selector 与批准的 `$` 不符；
没有保存错误 selector 原文，不能推断具体字符串。十二位置冷核对通过，CLI 原退出码 1 保留。
公开[脱敏统计](demo-evidence/reference-diagnostics-live-summary.json)。

出站 Schema 已把合法 selector 限制为 `$`，本地及固定基线证明整个响应可执行；
根据新证据仅补通用语义说明及从批准绑定派生的 root_response_bindings，
明确 `$` 是完整响应而非字段前缀。所有授权、意图和断言门禁继续重验。
首轮 **85 passed、1 failed、4 deselected** 暴露全局文字使旧 PocketBase 输入超预算；
改为仅存在根绑定时附加说明，不增加预算，不改无根绑定的原提示。
修正后相关 **37 passed、3 deselected／44.00s**，含三条全新 GW1301–GW1303 的
三基线／九模拟位置及十二位置冷核对，PocketBase 默认预算检查通过，Ruff 全仓通过。

第二批真实排期同样九次／216,864 Token、串行、60s、零重试，三条任务均为不同资源的
根响应绑定；本 Goal 最多累计 18 次调用，旧 GD/GR 任务不补跑、不换成绩。
后续真实调用继续依据已核实额度与每次 usage 执行停止规则。

`4f73061` 的新 GW 三基线及九次真实生成全部完成，三个任务各 **3/3**，
**13,020 Token** 全部已知，24 次 Mock 请求，十二位置冷核对通过；零重试、零未尝试。
公开[脱敏统计](demo-evidence/root-response-live-summary.json)。这是三条合成字符串根响应的
开发证据，不与旧题合并计算改善率或宣称任意接口可靠。
本 Goal 累计 **18 次真实模型、25,699 Token**；真实业务 HTTP／数据库写入均为 0。

`3f2c110` 的 [Python CI](https://github.com/mofengyan595/agenttest/actions/runs/39093548900)
四组及 [Web CI](https://github.com/mofengyan595/agenttest/actions/runs/39093548888) 两组均 success。
`4f73061` 的 Python 39093890363／Web 39093890359 仍在运行，不能提前记为通过。
下一准确里程碑：复用原只读 capability 和活跃 Executor 会话，实现有界 LangGraph
决策循环及独立的证据结论，保留原业务判定。

后续 CI 核对：`4f73061` 五组通过，Windows agent **1 failed、1,480 passed、4 skipped**。
失败为旧调查超时测试在慢机器上先触发业务超时；保留原失败及所有原断言，阶段 2 将真实
asyncio deadline 延迟到调查入口触发，避免机器速度改变测试所验证的阶段；生产超时未放宽。

## 阶段 2：功能与开发验证完成，模型漏诊保留

已接入[有界反馈闭环](bounded-feedback.md)及现有 CLI 的显式能力文件入口；
最多两次只读 GET／三次反馈决策，最后一次保留给结论。与原一次规划合计最多四次模型。
复用原活跃会话、Policy、资源引用、Executor 和报告；独立保存证据结论，不覆盖原业务失败。
Fake／Mock 覆盖有效／无效工具、参数、重复、预算、超时、未知用量、取消、权限快照、
工具与基础设施错误及证据不足；模型工具失败不能伪装成字段异常的支持证据。

已运行核心回归 **106 passed／90.15s**；新协议首轮严格 JSON 元组加载错误、
计时测试边界错误分别保留（2 failed／17 passed）；修正后又发现封印器要求摘要文件
（1 failed／18 passed），已按原机制补齐。随后协议及关联回归 **77 passed／64.21s**，
CLI／首轮取消／旧调查关联 **33 passed／40.42s**；Ruff 全仓与 pip check 通过。
最终扩大回归及真实三任务排期待执行，不能提前记为完成。新阶段真实调用当前为 0。

最终核心扩大回归 **198 passed、7 skipped／107.22s**，独立 JUnit 位于
`runs/goal-feedback-final-verified.xml`；七项均 Windows 符号链接创建权限 1314，不是业务跳过。
上一轮同范围进程结束后跨消息终端会话不可取回，未据此声称通过，改为保存上述 JUnit。
新增真实调用前干净代码门禁 **1 passed、1 deselected／2.24s**；Ruff 全仓通过。
离线 CLI `runs/goal-feedback-offline-final` 三固定及三模拟均通过，业务与调查结论各 3/3、
九次 Mock 模型、1,350 SIMULATED Token、28 次 ASGI 请求，六位置冷核对通过。
随后固定提交再进行已排定三任务真实验证；尚无本阶段真实模型／业务 HTTP／数据库写入。

`7e3c044` 首次真实排期仅尝试 FC1401 一次，**1,541 Token** 已知，初始计划因
OPENAPI_VALUE_TYPE 在请求前 TOOL_ERROR／PLAN_REJECTED，后两位置按驱动停止规则未尝试。
三控制通过，业务与调查模型成绩均 **0/3**；没有任何真实反馈模型决策。
六位置冷核对通过（四份报告、两位置未执行），公开[原失败统计](demo-evidence/feedback-initial-live-summary.json)。
计划的 body.name 哈希与“以 name 为键的对象包装请求字符串”的候选精确一致，证明是
标量字段被容器包装；没有猜测或保存模型原文。原 Schema 只限制槽位名称，仍允许此类型。

修正仅从 OpenAPI 已确认的字段 type 收紧出站字面量种类，按操作／位置／名称／类型共享
定义；未知或多种类型不虚构约束，引用仍以 null value 通过原来源检查。执行器和批准断言不改。
首轮旧槽位测试用字符串代替声明的对象（1 failed／35 passed），改为合法对象夹具，
保留原期望与业务断言。类型／引用／PocketBase 预算关联 **43 passed、4 deselected**。
下一组 FE1501–FE1503 使用新任务、新归档，最多十二次／289,152 Token；不重跑 FC。

补充一条通用的直接字段值说明，禁止擅加外层包装；未改变合法 body 字段名或容器语义。
六条旧 PocketBase 的新出站请求逐个准备，修改前最紧输入上界为 19,917／20,000；
新文字仍在原预算中，不提高限额。FE 新三基线、三模拟位置的业务与结论各 **3/3**，
9 次 Mock 模型、1,350 SIMULATED Token、28 次 ASGI 请求，六位置封印冷核对通过。

最终类型／模型／调查关联 **86 passed、4 deselected／31.36s**，JUnit 已保存至
`runs/goal-feedback-types-final.xml`；Ruff 全仓通过，新六位置再次冷核对通过。
准备阶段没有追加真实调用，随后在干净新提交上执行 FE 排期。

`9cee70b` 的 FE 三控制通过，三题真实规划均执行并检出预设故障，业务判定 **3/3**；
有证据支持的调查结论 **2/3**。FE1503 同角色重复字段失败的证据足够，模型仍弃权，
保留 NEEDS_REVIEW 及漏诊，不计正确结论、不追加补跑。FE1501 两次调查＋最终决策，
含规划共四次模型；FE1502／FE1503 各三次。共 **10 次、8,647 Token** 已知，零重试／未尝试。
真实模型阶段为 11 次业务 ASGI＋4 次调查 ASGI；含控制共 29 次，真实业务 HTTP／数据库写入为 0。
六份报告封印冷重评分通过，CLI 退出码 **1** 保留结论未全正确状态。
见[脱敏统计](demo-evidence/feedback-typed-live-summary.json)。

Goal 累计 **29 次真实模型、35,887 Token** 已知；FC 失败、GD 失败与 FE 漏诊分别记录，
不合并成功率。阶段 2 验证了反馈决策和安全退出，未宣称模型能可靠定位所有原因。
下一准确里程碑：生成待审接入／任务配置草案，完成本地脱敏追踪与两域配置成本核对。


## Goal 阶段 3：待审配置与本地追踪

详见 [配置及追踪](onboarding-observability.md)。两域配置计数、Fake/ASGI 执行及封印冷核对通过；草案没有执行权限。新增 Python 11 passed，Ruff 全仓通过。三份真实 FE 追踪合计 10 次、8,647 Token，五阶段齐全，无外部上传。前端仅沿用已有详情展示反馈结果与决策、引用诊断，不新增执行入口。首轮类型检查发现两个可缺省数组，已使用可缺省访问修正。一次误用不存在的 test_cli.py 导致未收集测试，另行按实际文件重验。下一准确任务：固定全新 24 题和三轮开发对照协议，独立人审仍未完成。

阶段 3 最终验证：项目及反馈 CLI 31 passed、1 skipped（Windows 链接权限）；前端 27 tests、typecheck、build 及 2 Playwright E2E 通过，桌面与窄屏截图检查通过。首次沙箱 Vitest 临时缓存 ENOENT 未收集两个套件，正常本地环境重验通过。`9b80775` 对应 Python 四组合、Web 两组合均 success。


## Goal 阶段 4：三轮配对开发协议已冻结、离线验收完成

[协议](paired-development-protocol.md)定义全新 PV1601–PV1624，四类各六题、五个既有模板、两域，三轮 72 个位置。固定 pytest 独立直接 HTTP 断言保留 24 failed / 48 passed（预设缺陷）；不是独立人工计时。Mock 72 位置完成，54 个可执行业务目标正确、18 参数分支拒绝、24 配对检出、21 有效支持证据、24 正确结论（含 3 证据不足），0 误报、120 Mock 调用、18,000 SIMULATED Token、528 ASGI 请求。完整总耗时 347.543 秒，包含固定对照与冻结开销，不折算人工工时。冷核对 72 Agent＋72 固定位置通过。

新增及审核门禁回归 38 passed / 30.82s；首轮 Ruff 一处测试长行，已分解变量。进一步固定参数定位只接受预设 OPENAPI_VALUE_LENGTH/TYPE/ENUM，任意意图拒绝只算安全拒绝，不算参数定位正确；原离线结果冷重算一致。36 个本 Goal 使用任务身份加入 formal 重合排除，不以同义改写绕过真实独立人工审核。待审登记包位于 runs/goal-independent-review-pending，审核数 0。阶段 3 提交 719e556 的 Python 四组合及 Web 两组合 CI 全部通过。

下一步在干净提交上执行已固定真实排期，最多 288 调用 / 6,939,648 Token 预留，每 run 上限仍为四模型/两只读调查，未知用量停止。未执行前不记真实成绩。
