# 通用规划参数名约束（2026-10-17）

## 问题与修改

[PocketBase 实测](pocketbase-acceptance-20261017.md)的 PB903 两次把整个请求体装进一个名为 `body` 的参数，实际接口只声明 `title`、`state`、`owner`。原 OpenAPI 门禁已在请求前拒绝，但规划阶段没有限制参数名。本轮只修复这个已复现的问题，旧题成绩仍保留 1/3。

通用 Planner 的出站 Schema 现在同时约束接口、参数位置与参数名：body 使用对象的顶层 properties，path/query 使用声明的参数；托管登录参数必须为空。相同槽位集合共享定义，保留所有候选，不通过截断目录或提高预算来适配。字段确实叫 `body` 时仍可用，嵌套对象中的同名键也保留。

返回后再次按同一接口槽位检查，提供方即使忽略输出 Schema，也不能把未知参数转成可执行计划；错误码为 `MODEL_SCHEMA_GENERIC_ARGUMENTS`，不保存原始错误值。字段类型、必填项、媒体类型、策略、资源来源、角色和任务完整性仍由原门禁核对。此处没有增加执行授权，没有修改通用 Prompt、业务断言或旧任务。

生成的类型标题和 JSON 空白被移除，重复定义复用，`number | integer` 中重复的 integer 分支按相同 JSON 接受集合合并。原字节上界预算和模型 Token 用量核查不变；字节减少不冒充 Token 或成本下降。较大的接口目录仍可能在调用前因预算拒绝，需要操作员缩小接入范围。

## 新任务与调用边界

三条新任务见[固定输入](../examples/argument-slots-development.json)，均为开发者构造的合成任务，没有独立人工审核：

| 任务 | 验证点 |
| --- | --- |
| GA1001 | 创建备忘并动态回读；顶层 subject/attributes，嵌套合法 body 键，query.view |
| GA1002 | 合法顶层 body 对象、布尔数组与 batch；路径 envelope_key 和 query.body |
| GA1003 | 数组对象与小数；body.warehouse、路径 parcel_id 和 query.warehouse |

先执行三条固定基线，再按 GA1001→GA1002→GA1003 排定三轮，每题每轮一次。总分母 12 个位置，其中真实模型最多 **9 次 ecnu-plus**；每次最多预留 24,096 Token，总上限 **216,864 Token**，零自动重试。沿用用户在本会话给出的额度自主授权；超时、服务故障或用量未知时停止剩余模型位置并保留未尝试项。

业务始终是一次性进程内 MockTransport，不启动 Java、PocketBase 或数据库，不连接真实业务服务。出站内容只有公开契约、批准策略目录、合成任务及输出 Schema；凭据仅供模型客户端认证，不写入归档。输入、源码身份和排期在执行前冻结；报告与计划摘要封印后可离线复核。

## 复现

仓库已有 Python 3.11 `.venv`，不新增依赖。默认入口只模拟模型：

```powershell
.\.venv\Scripts\python.exe scripts/check_argument_slots.py --output runs/argument-slots-new
.\.venv\Scripts\python.exe scripts/check_argument_slots.py --verify runs/argument-slots-new
```

真实调用必须显式增加 `--model-env .env`，只允许 ecnu-plus，按上述固定上限执行；冷复核不读取模型配置或联网。运行时保持源码和 Git 状态不变，输出目录必须全新。退出码 0 表示全部固定基线与九个规划位置通过；1 保留失败／未执行，2 表示输入或归档不合法。

## 本轮验证状态

初步专项 53 passed／1 deselected；扩大回归 **153 passed／199.23s**，覆盖原 JSON 字面量、模型适配、任务约束、Petclinic 和工单链路。最初新验收器因 plan_schema 快照不一致，三条固定基线在模型调用前停止，零业务请求；修正后新任务离线三条基线与九个模拟位置全部完成，原失败归档保留。

最终相关回归 **67 passed／218.71s**，包含新任务、用量未知后停止剩余八位置、PocketBase 6＋18 个离线位置、两域固定成功／故障／拒绝及固定回归导出。默认 CLI **3/3 固定基线、9/9 模拟规划**，24 次 Mock 业务请求；12 位置冷复核通过，1,350 Token 为 SIMULATED。Ruff 全仓、pip check、四份文档本地链接与十一份发布文件密钥扫描通过。

准备阶段真实模型调用仍为 0；冻结版本后执行真实验证，结果据实际报告补记。本机没有重跑全仓或 Web，最终推送另核对准确 SHA 的 CI。此批始终 `DEVELOPMENT_ONLY / UNREVIEWED`，`human_reviewed=0`、`formal_eval_v2_score=null`；不与 PocketBase 的 16/18 合并，不声称新旧任务之间的改善率或任意项目保证。

下一准确里程碑：减少陌生项目的人工接入配置，先复用现有发现器输出可审阅的策略草案和缺失项；草案不能自行成为执行授权或断言来源。
