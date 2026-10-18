# 通用规划引用字段约束（2026-10-18）

## 解决的问题

[上一轮](argument-slots-20261017.md)已限制参数名，但 GA1002 有一份真实返回在 select 字段校验时拒绝。原始模型文本未保存，因此不能猜测具体错误字符串；原七份完整记录、失败、中断和未知用量继续保留。

本轮复用现有公开策略中的 source_policy_id／source_selector：封闭目录的出站 Schema 只列可见来源策略允许的引用字段，没有引用来源时只允许字面量。返回后按当前策略、接口和参数精确检查引用是否来自先前步骤，以及来源策略、接口和 selector 是否一致；不能借用其他策略的候选。开放策略中未定义的槽位仍只限制受支持的 JSONPath 语法，不虚构完整允许列表。

引用语法非法为规划格式错误；语法合法但违反已批准绑定时，保留 NEEDS_REVIEW 与原 POLICY 诊断。角色、授权、请求类型、资源关系和任务完整性仍由原 Policy／DSL／Intent／Executor／Verifier 决定，不把出站候选当作授权，也不修复模型生成的引用。通用 Prompt、旧任务内容和核心断言没有修改。

递归容器的出站定义改用较短的标识符，数据格式与约束不变；旧 PocketBase 目录仍能放入原默认预算。没有新依赖，没有提高默认模型预算，也不把请求字节减少称作 Token／成本下降。

## 全新开发任务

[三条新任务](../examples/reference-selectors-development.json)使用共享的既有验收器，业务始终是一次性 MockTransport：

| 任务 | 读取创建结果的方式 | 其他核对 |
| --- | --- | --- |
| GR1101 | $.receipt.locator.value，嵌套字段 | 创建请求中的对象、布尔值和查询参数原样回读 |
| GR1102 | $.entries[1].resource，数组索引 | 第一项为不同的干扰编号，同时回读小数数组 |
| GR1103 | $，整个响应是资源编号字符串 | 根引用仍合法，核对原始布尔值及查询参数 |

这三条是开发者构造的合成任务，保持 DEVELOPMENT_ONLY／UNREVIEWED，human_reviewed=0、formal_eval_v2_score=null。三轮重复仍是三条任务，不是九条独立新题；不与旧任务成绩合并或宣称改善百分比。

## 排期、额度与复现

先运行三条固定基线，全部通过后才按 GR1101→GR1102→GR1103 的顺序执行三轮模型规划。总分母十二位置，模型最多 **9 次 ecnu-plus**，每次预留最多 24,096 Token，总预留 **216,864 Token**，零自动重试。超时、基础设施故障或用量未知时停止后续模型位置，保留未尝试项。

模型沿用用户在本会话给出的额度自主授权；本轮额度与旧中断记录分开，旧未知位置不退款、不重发。出站只有公开接口、策略、合成需求及输出格式，没有真实响应、凭据或评分标签；模型 Key 只用于客户端认证。既有 .venv 与锁文件即可运行，不安装依赖，不启动 Java／PocketBase／数据库，不连接真实业务服务。

```powershell
.\.venv\Scripts\python.exe scripts/check_argument_slots.py --references --output runs/reference-selectors-new
.\.venv\Scripts\python.exe scripts/check_argument_slots.py --verify runs/reference-selectors-new
```

默认只模拟模型。真实调用显式增加 `--model-env .env`，仍只使用 Mock 业务；冷核对读取冻结的任务集合，不加 `--references` 或模型配置。输出目录必须全新，运行中保持源码与 Git 状态不变。正常完整验收退出 0，存在失败／未执行时退出 1，输入或归档错误退出 2。

## 已运行的验证

首轮专项 **1 failed／78 passed／4 deselected**，发现 JSON Schema 的 pattern 按子串匹配；加首尾约束后 **79 passed／4 deselected，16.39s**。初次新任务固定基线仅 1/3：identity／tokens 命名被现有敏感字段投影移除，两个引用因此无法解析；当轮模型调用为 0，原失败保留。改用普通资源字段名后复验，没有放宽脱敏或业务断言。

扩大回归 **127 passed／3 deselected，83.69s**；最终相关回归 **111 passed／236.94s**，覆盖新任务与本地拒绝、原通用参数、跨项目、PocketBase、模型客户端及固定回归。默认新任务 CLI **固定基线 3/3、模拟规划 9/9**，24 次 Mock 业务请求，十二位置冷核对通过；1,350 Token 为 SIMULATED。旧任务三份 TaskContract 与旧冻结摘要一致，旧十二位置归档可继续冷核对；Ruff 与 pip check 通过。

准备阶段真实模型调用为 0；代码冻结后按上述排期执行，结果据真实记录补记。本机未重跑全仓或 Web，最终提交另核对准确 SHA 的远程检查。

下一准确里程碑：在当前引用与授权门禁基础上降低接入配置成本，先复用已有发现器列出候选业务链、策略草案和缺失依据，交由操作员审阅；不自动批准断言或执行。
