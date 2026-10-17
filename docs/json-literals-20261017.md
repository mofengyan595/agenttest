# 通用 JSON 字面量（2026-10-17）

本轮补齐通用规划入口对嵌套对象、数组和有限小数的表达。Executor 已有递归请求校验，新增草稿转换仍复用它及原 Policy／Intent／执行门禁；mini、Java、batch 和 composed 的草稿字段与提示不变。

## 表达与边界

当前通用草稿的 value 可包含封闭的结构化节点：对象为 `{"object":[{"key":"enabled","value":true}]}`，数组为 `{"array":[1,0.125,null]}`，可递归嵌套；标量仍为普通 JSON 值。转换器将节点还原为普通请求数据，不执行代码、不插值引用，也不从容器加载目标、凭据或策略。原五个参数字段保留，没有 json_value 或 JSON 文本二次编码。

结构化节点保留严格输出的封闭字段，同时支持业务对象的可选字段、空容器及显式 null；这些语义不能通过自动补 null 或删字段替代。参数 Schema 使用三个互斥来源分支：标量、body 容器、前序步骤引用。[当前官方 OpenAI 文档](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat)要求对象关闭额外属性、所有字段 required，并支持受限 anyOf／递归结构；学校兼容接口仍须真实实测。

容器只允许 body 字段，path／query 继续只接受标量。每个还原后的字面量最多 20,000 UTF-8 字节，拒绝重复对象成员、非有限数字及超过原有深度／节点限制的数据；外层回复的字节、Token 和步骤预算继续生效。嵌套节点增加结构深度，不能将底层最大深度当作任意业务嵌套深度的承诺。任意对象、数组及小数是否允许，仍由批准的请求 Schema 和任务约束决定。

本轮不扩大既有 OpenAPI 子集：根请求体仍为对象，不支持开放属性、oneOf／anyOf、format: float／double 或在容器内部插入动态步骤引用。这些输入继续 NEEDS_REVIEW，不绕过校验。

原 equals／in 和 enum 比较只在最外层区分类型，嵌套 `true` 与 `1` 可能被 Python 等值判定混淆。本轮改为递归精确类型比较；对象键顺序不重要，数组顺序、字段缺失与显式 null、整数与布尔等区别保留。没有改任何批准断言来让失败通过。

## 新任务与验收方法

[J801–J803](../examples/json-literals-development.json)为三条新的合成草稿任务：

| 任务 | 数据重点 | 配对故障与指定断言 |
| --- | --- | --- |
| J801 | 嵌套 bounds、可选字段缺失、空数组、0.125 | readDraft / $.payload.config 中 enabled 从 true 变为 1 |
| J802 | 对象数组、嵌套数组／布尔、显式 null、1.75 | readDraft / $.payload.items 数组倒序 |
| J803 | 负小数、中文引号、空数组、-0.375 | readDraft / $.payload.ratio 返回错误小数 |

每题创建后通过实际响应 key 读取同一资源。目标是一次性 HTTPX MockTransport，不是陌生第三方服务。每题先生成一次，在正常目标完整通过后才用同一计划分别重放新正常对照和指定故障，预先固定九个位置。故障只由操作员工厂选择，不进入模型输入。工具错误、意图失败、未执行均保留，不能冒充故障检出。

公开命令只运行固定回复，或对既有归档重新评分：

```powershell
.\.venv\Scripts\python.exe scripts/check_json_literals.py --output runs/json-literals-new
.\.venv\Scripts\python.exe scripts/check_json_literals.py --verify runs/json-literals-new
```

输出须为新的、仓库内忽略的 runs 目录。固定回复 Token 为 SIMULATED，不能计作真实模型结果。源码和任务冻结后不改提示、预期或样本；来源变更会中止验收。

按用户已有同项目同模型的常规预算授权，本轮真实验证上限为 **3 次 ecnu-plus、预留 72,288 Token**，J801–J803 各一次，无重试、备用回复或失败后调题。先完成固定验收，再冻结干净提交、逐字节出站请求、任务及模型配置。未知 usage 或基础设施错误停止后续调用，保留原失败与未执行位置。无 Java、真实数据库或生产请求，无新增依赖。

全部为 DEVELOPMENT_ONLY／UNREVIEWED、human_reviewed=0、formal_eval_v2_score=null。新能力及这三题的局部结果不证明任意项目可靠性，不合并旧任务成绩。离线验收与真实调用结果随本轮交付记录。

固定验收实际完成九个位置：正常生成 3/3、新正常对照 3/3、配对故障检出 3/3，6 PASS／3 ASSERTION_FAILED；共 18 次 Mock 业务请求，3 次模型传输为模拟、450 Token 为 SIMULATED，真实模型调用为 0。冷核对九个位置通过。基础安装模拟 36 passed、3 skipped / 6.09s，仅跳过图执行；扩大首轮 213 passed、1 skipped、1 failed / 98.44s，失败为新增测试将返回摘要误当完整归档读取，改为读取 results.json 后复验，业务预期和判定未改。

修正后专项 **39 passed / 38.80s**，包括完整归档、复杂数据的固定 pytest 导出和重新执行。Ruff、pip check、差异及发布密钥扫描通过，无新增依赖。旧工单九位置和稳定性十二位置冷核对通过，零新请求；旧失败和原始成绩保留。

## 首次真实验证及格式修正

实现 5f3e411 的第一版使用 JSON 文本载体。J801／J802／J803 各真实生成一次，全部 HTTP 200、usage 已知、零重试，但均在容器参数被 MODEL_SCHEMA_VALIDATOR_REJECTED 拒绝，**0/3 完成、零业务请求**；用量分别为 2,276／2,299／2,060，合计 **6,635 Token**。六个配对位置均 NOT_ATTEMPTED，没有把安全拦截算作故障检出。原九个位置封印并冷核对通过。

当时诊断只能定位 arguments，不保留具体校验分支，不能唯一推断模型回复的具体错误。静态检查确认来源互斥条件未进入出站 Schema，JSON 文本内部语义也不受结构化输出约束。修正为上述结构化节点和显式来源分支，并增加重复成员、非有限数、来源冲突和字面量上限的独立诊断；原三题不重试、不改原期待或成绩。

准备脚本曾将业务 Schema 的 labels 属性误当评分标签，在请求前中止、调用为 0；仅修正私人开发准备脚本的上下文分类，正式独立评测规则保持原样。模型只接收可信构造的公共上下文，正常／故障上下文和出站字节必须一致。

补充验收使用独立新文件中的 [J804／J805](../examples/json-literals-repair-development.json)，字段值、主题和数组数据均为新样本，仍为同一任务族。先完成新的两题固定生成／对照／故障六位置，再冻结实现与请求；最多 **2 次 ecnu-plus、48,192 Token**，各一次、不重试，前后两批合计最多五次／120,480 Token。首批 0/3 继续单独保留，不与补充批次合并成改善率。

```powershell
.\.venv\Scripts\python.exe scripts/check_json_literals.py --repair --output runs/json-literals-repair-new
```

结构化节点版离线补充六位置通过：正常生成／正常对照／指定故障各 2/2，4 PASS／2 ASSERTION_FAILED，12 次 Mock 业务请求；300 Token 为 SIMULATED。完整相关回归 **171 passed / 69.56s**，基础安装 **35 passed、3 skipped / 7.80s**；仅图集成需要 agent 可选依赖。两批既有归档均重新冷核对通过，零新模型请求。

## 结构化节点的真实结果

实现与请求固定在 **4fdd13d**，只执行新两题，各一次：

| 任务 | 正常生成 | 同计划正常对照 | 同计划故障 | 实际 Token |
| --- | --- | --- | --- | ---: |
| J804 | PASS，2 次 Mock 请求 | PASS | ASSERTION_FAILED，config 的 true／1 类型差异 | 2,439 |
| J805 | PASS，2 次 Mock 请求 | PASS | ASSERTION_FAILED，items 数组倒序 | 2,734 |

两次真实模型请求均 HTTP 200、usage 已知、零重试，合计 **5,173 Token**；两个任务完成，两个正常对照通过，两个故障由指定断言检出。六份报告为 4 PASS／2 ASSERTION_FAILED、12 次 Mock 业务请求，无未执行或不确定项。正常／故障重放使用首次生成的同一计划和全新 Mock 状态，没有再次生成或更改断言。

首批文本版三次失败原样保留；两批共 **5 次真实调用、11,808 Token**，仍分开报告 0/3 与新两题 2/2，不计算改善百分比或合并成泛化成功率。两份真实归档分别冷核对九／六位置通过，无新请求。原始报告、冻结请求和私人运行器位于忽略的 `runs/json-literals-20261017/`，不随提交发布。

当前已取得结构化对象、数组、显式 null、有限正负小数和动态资源绑定的两题真实小样本证据，未验证陌生第三方复杂写入、独立大样本或模型自主诊断。后续进入统一验收和演示整理，正式独立评测资格不变。
