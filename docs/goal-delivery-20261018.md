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

全 Goal 硬上限 300 次真实请求、1,000,000 Token，串行、零隐式重试。
每阶段先冻结输入、顺序、超时和更小预算；尝试先记账，用量未知停止后续付费调用。
旧 GA1002 中断的未知用量原样保留，不能合并成本或退款重发。

2026-10-18 核查[供应商配额文档](https://developer.ecnu.edu.cn/vitepress/llm/limit.html)：
credits 与 Token 分开，输入缓存和峰谷时段影响计费；余额需登录开放平台查看。
用户在本次会话重新查询并提供 52.13／20,000 credits，重置时间为北京时间
2026-10-19 09:18；本次接收时间约 2026-10-18 11:21。它是用户当前查询值，
不是程序实时余额接口；后续以供应商失败／usage 和本地硬预算执行停止。
不将 credits 与 Token 合并统计，不把价格估算当实际扣费。

## 阶段 1：进行中

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
