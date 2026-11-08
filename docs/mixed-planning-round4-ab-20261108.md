# 第四轮全新真实模型 A/B 验收（2026-11-08）

第四轮只回答一个问题：2026-11-08 那五处离线规划修复，在**全新真实模型任务**上是否真的改变行为。
本节所有数字来自一次冻结排程的首次执行：16 个唯一首位，一位置一提交，零重放、零重试、零停止。

## 1. 冻结与版本

| 项目 | 值 |
| --- | --- |
| 基线（修复前） | `8111a37358f1cc161249016035ff0c25c68ea0ca` |
| 候选（修复后） | `a779ca56fde7a60228a5804425f045964503d2f2` |
| 差异 | 仅 7 个 `src/agenttest/*.py` 运行时模块 |
| 排程冻结 | `457d90967b5119cd0d955a1adbf83655ac161a7a6293dde4619435249f407faf` |
| 排程提交 | `9998940`（新增 harness、四篇需求、方案文档） |
| 模型 | `ecnu-max`，温度 0，每位置 ≤4 次调用、≤8 次目标 HTTP、≤180 s |
| 目标 | 全新只读回环项目 `127.0.0.1:19091`，新受众 `custodian`/`curator`/`auditor`/`inspector` |

四篇需求与独立参考答案冻结在 [方案文档](mixed-planning-round4-ab-plan-20261108.md)，答案只存在于评估侧，
从未进入产品运行时上下文。

## 2. 首次执行结果

16/16 位置全部完成审计，无停止、无 UNKNOWN、无基础设施错误；两版用量均为产品台账 `KNOWN`
（与评分侧读数逐 token 一致）。

| 指标 | 基线 8111a37 | 候选 a779ca5 | 判读 |
| --- | --- | --- | --- |
| 完整执行（执行类 4 位置） | 3/4（F1 1/2、F3 2/2） | 4/4（F1 2/2、F3 2/2） | 候选不劣 |
| 正确部分待审（待审类 4 位置） | 0/4 | 2/4（F2 2/2） | 候选更好 |
| 错误拒绝 | 4（F2 r1、F2 r2、F4 r1、F4 r2） | 2（F4 r1、F4 r2） | 严格下降 |
| 静默漏项 / 已观测义务 | 28 / 76 | 11 / 76 | 未增加，下降 61% |
| 配置 / 任务 / 待审越权 | 0 / 0 / 0 | 0 / 0 / 0 | 两版均零 |
| 未获批请求 | 0 | 0 | — |
| 目标 HTTP 请求 | 14 | 20 | 候选多读合法资源 |
| 模型调用 / token | 13 / 84,111 | 15 / 94,953 | 候选多 2 次调用 |
| 中位耗时 | 17.98 s | 17.03 s | — |
| 表述性拒绝（非安全） | 0 | 0 | 未出现 |

逐位置明细见 `positions.csv` 与 `results.json`；原始模型请求/响应、目标 HTTP 审计行、每位置
`terminal`/`run-*`/`score` 均在证据包内。

### 2.1 逐篇判读

| 篇 | 基线 | 候选 | 差异性质 |
| --- | --- | --- | --- |
| F1 动态来源 + 跨接口绑定 + 响应断言 | r1 通过；r2 顺序未验证 | 两轮通过 | 跨用例顺序（P0-3 顺序面） |
| F2 权限待审 + 多个权限问题 + 独立合法读取 | 两轮聚焦阶段 0 请求直接拒绝 | 两轮两问、读公告与心跳各一次、3 请求、正确待审 | 权限面修复生效 |
| F3 重复读取 + 跨用例顺序 + 执行前置 | 两轮通过 | 两轮通过 | 重复本身无差异，无回归 |
| F4 断言 + 数据来源 + 局部阻塞 | 两轮判负 | 两轮判负 | 见第 5 节评估缺陷 |

基线两轮拒绝的诊断码分别是 `FOCUS_REQUIREMENT_GAP_WITHOUT_EVIDENCE` 和
`FOCUS_PERMISSION_QUESTION_WITHOUT_ACTION`：合法读取被搁置且没有提出任何可回答问题，正是第三轮
要修的失败类。候选在这两处都产出了带 `source_quote` 的权限问题和 `status_choices {403,404}`，
并把公告、心跳这类与待审项无关的读取真正执行掉。

## 3. 预演（全部在真实调用之前完成，零模型调用）

1. **环境预演**：29 项身份/响应/入项检查——每个操作员身份的 200/401/403、缺失资源的 404、
   只读状态哈希，逐项与冻结期望比对。
2. **执行路径预演**：两版各跑两条真实批准流程（字面量入参与 `$.cards[0].slip` 动态绑定），
   经真实执行器与真实 HTTP，独立评分器判为完整执行。
3. **评分器负向控制**：固定八项——一个正例与七个缺陷类，逐项断言预期的旗标翻转：
   错误身份（步骤角色改为未获批身份）、错误来源（绑定规则改为 `$.cards[0].ref`）、
   缺漏需求（删掉一个必需步骤）、重复不足（删掉第二次读取）、顺序错误（重排审计序列）、
   未获批请求（插入一条越权审计行）、无问题全局阻塞（零请求 + 只有拒绝诊断）。
   七项全部被检出，正例保持通过。
4. **浏览器入口预演**：两个普通 Web 入口加载同一构建产物、均可提交且零提交、零调用。

## 4. 验收判定

| 条件 | 结果 |
| --- | --- |
| 16 个位置全部审计 | 通过 |
| 无停止 | 通过 |
| 错误拒绝严格下降（4 → 2） | 通过 |
| 静默漏项不增加（28 → 11） | 通过 |
| 每篇待审不退化（F2 0→2，F4 0→0） | 通过 |
| 候选三类越权计数为零 | 通过 |
| 无 UNKNOWN、无表述性拒绝 | 通过 |

**第四轮验收达成。** 该判定按冻结规则计算，未在事后调整任何评分口径。

## 5. 评估缺陷披露（必须先读）

F4 的冻结 rubric 多写了一条原文并未要求的「读取公告」步骤，而 F4 的正文只要求
书架 → 凭条 → 标题断言。后果是 F4 的每个位置都必然少一个动作与一个状态义务（静默 2），
且两版完全对称；它不改变 A/B 方向，但会让 F4 无法通过。

处置：冻结分数原样保留、不修改 rubric、不重跑、不换口径刷分。`rubric-defect.json` 给出仅作
补充分析的复算（去掉该条多余步骤），结论不变：

| 补充口径（非冻结） | 基线 | 候选 |
| --- | --- | --- |
| 错误拒绝 | 3 | 1 |
| 正确部分待审 | 1（F4 r1 行为本就正确） | 3（F2 两轮 + F4 r1） |
| 静默漏项 | 24 | 7 |

也就是说：修正 rubric 后，候选在 F4 第一轮其实做到了「局部阻塞」——只拦住未获批的 `$.title`
断言，仍执行了书架与凭条；两版只在 F4 第二轮（聚焦阶段就拒绝、零请求）达成一致失败。
下一轮必须把 F4 的正文与 rubric 对齐后重测该类，且不得复用本轮位置。

## 6. 四个结论

1. **五类修复是否生效**：两类拿到区分性证据——权限待审/多问题/独立读取（基线 0 请求拒绝 →
   候选正确待审），跨用例顺序（基线 r2 顺序失败 → 候选两轮通过）。另外三类在本轮**未被区分**：
   动态来源（两版 F1 都能正确绑定，基线 r1 也通过）、混合条款（被 F4 rubric 缺陷掩盖）、
   中文表述（两版都没有出现表述性拒绝）。未被区分不等于被否定，但也不能算已证实。
2. **已解决 / 仍存在**：已解决——聚焦阶段的分布性拒绝、权限问题缺失、跨用例顺序、独立合法读取被
   搁置；静默漏项从 28 降到 11（执行类位置为 0）。仍存在——F4 第二轮两版都在聚焦阶段拒绝
   （候选码 `FOCUS_PERMISSION_QUESTION_WITHOUT_ACTION`，基线码 `FOCUS_SELECTED_ACTION_WITHOUT_SOURCE`），
   F4 第一轮的产品行为正确但被过严 rubric 判负。
3. **是否达到第四轮验收标准**：达到（第 4 节七项条件全部满足，且补充口径下结论不变）。
4. **能否开始 API 读写生命周期验收**：可以开始准备，但需先前置两件事——修正 F4 的正文/rubric
   配对并换新文本重测该类；为读写验收新建隔离项目与独立授权（含写操作批准、可回滚状态与
   独立评分器负向控制）。本轮只读结论不覆盖任何写路径。

## 7. 复现

```powershell
.venv\Scripts\python.exe scripts\round4_ab\prepare.py init runs\round4-ab-20261108
Copy-Item -Recurse web\dist runs\round4-ab-20261108\shared-web-dist
.venv\Scripts\python.exe scripts\round4_ab\serve.py runs\round4-ab-20261108
.venv\Scripts\python.exe scripts\round4_ab\prepare.py schemas runs\round4-ab-20261108 --arm baseline
.venv\Scripts\python.exe scripts\round4_ab\prepare.py schemas runs\round4-ab-20261108 --arm candidate
.venv\Scripts\python.exe scripts\round4_ab\prepare.py preflight runs\round4-ab-20261108
node scripts\round4_ab\smoke.mjs runs\round4-ab-20261108
.venv\Scripts\python.exe scripts\round4_ab\execution_preflight.py runs\round4-ab-20261108 baseline
.venv\Scripts\python.exe scripts\round4_ab\execution_preflight.py runs\round4-ab-20261108 candidate
.venv\Scripts\python.exe scripts\round4_ab\scorer_preflight.py runs\round4-ab-20261108 baseline
.venv\Scripts\python.exe scripts\round4_ab\scorer_preflight.py runs\round4-ab-20261108 candidate
.venv\Scripts\python.exe scripts\round4_ab\prepare.py freeze runs\round4-ab-20261108
node scripts\round4_ab\run.mjs runs\round4-ab-20261108
.venv\Scripts\python.exe scripts\round4_ab\collect.py summary runs\round4-ab-20261108
```

证据包：[docs/evidence/mixed-planning-round4-ab-20261108/](evidence/mixed-planning-round4-ab-20261108/README.md)
（`evidence.zip` 的 sha256 见同目录 `evidence.sha256`；`recompute.py` 可在不导入产品代码的前提下
冷重算 16 个位置并与存档分数逐项比对）。
