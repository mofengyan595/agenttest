# 第四轮真实 ecnu-max A/B 方案（2026-11-08）

本文件冻结第四轮**题目、期望答案、评分口径、预算与停止条件**。题目原文只出现在本文件的
表格里，产品运行上下文只拿到需求文本本身；参考期望与评分代码留在评测侧。

## 1. 目的与版本

- 验证 2026-11-08 的五类规划修复（动态来源/断言分离、待审问题见证、权限询问与跨用例顺序、
  中文表述不再当结构失败）在**全新**真实模型任务上是否生效。
- baseline `8111a37358f1cc161249016035ff0c25c68ea0ca`（修复前）；candidate
  `a779ca56fde7a60228a5804425f045964503d2f2`（修复后）。两版只差 `src/agenttest` 的七个模块。
- 新建隔离 loopback 只读项目（`127.0.0.1:19091`，`/shelves`、`/cards`、`/slips`、`/pulse`、
  `/gates`、`/keepers`、`/ledgers`、`/notices`、`/quotas`），不复用第三轮 META/FLOW/HOLD 的
  题目、路径、身份或标签；身份为 `custodian`、`curator`、`auditor`、`inspector`。

## 2. 四条全新需求（逐字冻结）

| 编号 | 原文 |
| --- | --- |
| F1 | custodian 读取 shelfId=12 的书架；将它响应中的 $.cards[0].ref 交给 curator 打开卡片；核对卡片的 $.marker 等于 "catalogued"；随后匿名读取运行心跳。 |
| F2 | 先确认 inspector 访问闸口状态的拒绝规则是 403 还是 404；再确认 inspector 访问值守账号的拒绝规则；同时以匿名方式读取公告；并把运行心跳连读两次。 |
| F3 | 先以匿名身份连续读取运行心跳两次；两次都要核对 HTTP 200；最后由 auditor 读取 ledgerId=7 的台账。 |
| F4 | custodian 读取 shelfId=12 的书架；用它响应中的 $.cards[0].slip 让 custodian 取回凭条；核对凭条的 $.code 等于 "S-31"；还要核对卡片标题 $.title 等于 "散页目录"。 |
| F5 | （空） |

`F5` 行只是表格终止符，不参与评测；实际题目为 F1–F4。

## 3. 独立参考答案（仅评测侧）

| 编号 | 正确行为 | 类型 |
| --- | --- | --- |
| F1-参考 | 执行 listShelf(custodian, shelfId=12) → showCard(curator, cardRef 来自 `.cards[0].ref`) → checkPulse(匿名)，顺序一致；`$.marker` 断言通过 | 可完整执行 |
| F2-参考 | 执行 fetchNotice(匿名) 与 checkPulse×2；`readGate`/`inspector` 与 `readKeeper`/`inspector` 各自保留一条待审权限问题；不访问这两个接口 | 正确局部待审 |
| F3-参考 | 执行 checkPulse×2（两次独立读取）→ readLedger(auditor, ledgerId=7)，顺序一致 | 可完整执行 |
| F4-参考 | 执行 listShelf → fetchSlip(custodian, slipCode 来自 `.cards[0].slip`) → fetchNotice；`showCard` 的 `$.title` 断言未获批准，保持待审且不访问 showCard | 正确局部待审 |

## 4. 评分口径

沿用第三轮冻结评分器（`scripts/round4_ab/grade.py`，与第三轮逐字相同）：批准策略、真实
Executor 输入、原始 HTTP 审计三者独立对齐。每版 8 个位置（4 题 × 2 轮），指标：

- 完整执行率（可完整执行题 = F1、F3）；
- 错误拒绝（含无追问的合法操作误阻塞）；
- 静默漏项（义务数含动作、状态、参数、断言、绑定、顺序、待审见证）；
- 正确局部待审（F2、F4）；
- 配置越权 / 任务范围越界 / 待审范围错误执行；
- 重复读取、顺序、实际动态绑定；
- 非安全性解释语言格式导致的错误拒绝（`MODEL_SCHEMA_TYPE_OR_CONSTRAINT` 且仅因表述字段）；
- 模型调用数、Token、耗时。

## 5. 验收条件（任一不满足即不接受）

1. candidate 错误拒绝总数 **严格低于** baseline；
2. candidate 静默漏项 **不高于** baseline；
3. candidate 正确局部待审 **不低于** baseline（逐题不退步）；
4. candidate 三类越界计数全为 0；
5. 两版 UNKNOWN 与基础设施失败均为 0；
6. 16 个位置全部保留、可冷复算，且不重跑任何位置。

## 6. 预算与停止条件

- 每位置最多 4 次模型调用、8 次目标 HTTP、180 秒；`ecnu-max`、temperature=0、thinking 关闭。
- 停止条件：源码或目标状态漂移、UNKNOWN 用量、基础设施失败、配置批准越界、浏览器或提交入口失败。
- 不做预检模型调用；不在冻结后修改评分规则、产品代码或题目。

## 7. 预检（付费前）

1. **环境预检**：9 个接口 × {正确身份, 错身份, 匿名} 的实际状态与响应哈希；两版 Web 入口的
   project-intake 201 且 ready。
2. **浏览器预检**：两版普通 Web 入口可输入需求并启用提交按钮，零提交、零模型调用。
3. **执行预检**：每版 1 条确定性批准流程（listShelf → showCard → fetchSlip + 心跳）走真实
   Executor 与真实 HTTP，评分器必须判 `full_execution`。
4. **评分器反例预检**：用上述真实证据做 7 个确定性扰动，必须分别检出
   错误身份、错误来源、遗漏要求、重复次数不足、错误顺序、未经批准请求、错误全局阻塞。

## 8. 交付

首次真实结果、原始模型与 HTTP 证据、冻结配置、SHA-256 摘要、逐位置评分与冷复算归档；
结论回答五类修复是否生效、哪些失败已解决、是否达标、能否进入 API 读写生命周期验收。
