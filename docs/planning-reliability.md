# 规划可靠性与安全诊断

## 执行前的意图检查

`mini_target_nl_v1` 在现有 DSL、OpenAPI、Policy Registry 校验通过后，再由确定性代码检查任务和计划。没有模型自评、计划修补或断言降级。

- 当前只识别受控中文任务：一个用中文弯引号或英文双引号标出的课程名称、明确的 alice/bob 创建者、创建后本人查询；归属任务还需明确另一角色与拒绝关系。
- 名称必须逐字符一致；创建、本人读取、他人读取均引用同一个创建响应的动态 ID。Policy Registry 继续提供已审核的 201/200/403 和 ID/名称等值断言。
- 一个创建、一个本人读取；归属任务另有一个他人读取。角色登录必须先于其受保护操作，拒绝查询在本人查询之后。不自动补步骤或数据。
- `intent.py` 使用固定词汇和显式关系规则，不导入评测模块、任务文件或 Golden Labels。新课程名不需要进入词表；任务中的额外目标、开放问题或歧义进入 NEEDS_REVIEW。
- 这不是通用中文语义理解器。词表之外的合法需求也可能被保守拒绝；只扩展经过单独审阅和测试的语法，不以放宽匹配提高评测成绩。
- 旧 Executor/非自然语言场景仍按原受信配置执行；`intent_verified` 仅表示本受控范围的意图检查通过。

原有测试中的含糊占位需求换成与原固定计划一致的明确名称/角色；原有安全、ownership、terminal、预算和报告故障断言继续保留。固定 FakeLLM 仍输出同一个计划：有效率可以高，但意图不符时现在在执行前停止，不能把 API 断言 PASS 当作需求完成。

## 诊断协议 v1

报告 `diagnostic` 只有 `layer`、固定 `code`、最多八个受控字段路径。Pydantic 的输入、异常上下文、错误文本和未知字段名称均不落盘；未知路径项替换为 `*`。原 `outcome` / `reason` 保留兼容性；`validation_issue` 不再依赖异常字符串匹配。

| 层 | 典型固定码 | 含义 |
| --- | --- | --- |
| json | JSON_SYNTAX / JSON_DUPLICATE_KEY / JSON_SIZE / JSON_STRUCTURE_LIMIT | 语法、歧义或预算 |
| model | MODEL_SCHEMA_MISSING_FIELD / EXTRA_FIELD / VALIDATOR_REJECTED / TYPE_OR_CONSTRAINT（共同前缀 MODEL_SCHEMA_） | 结构化草案错误，附安全字段路径 |
| model | MODEL_DUPLICATE_ARGUMENT / MODEL_USAGE_INVALID / MODEL_CHOICE_INVALID / MODEL_ENVELOPE_INVALID | 草案转换、用量或响应信封错误 |
| dsl | DSL_UNKNOWN_OPERATION / DSL_DUPLICATE_STEP_ID / DSL_OPERATION_NOT_PERMITTED / DSL_AUTHENTICATION_ORDER | 操作、步骤或授权顺序 |
| openapi | OPENAPI_BODY_REQUIRED / PARAMETER_REQUIRED / UNKNOWN_BODY_FIELD（共同前缀 OPENAPI_） | 契约字段错误 |
| openapi | OPENAPI_VALUE_TYPE / ENUM / LENGTH / BOUND / EXCLUSIVE_BOUND / FORMAT_BOUND（共同前缀 OPENAPI_VALUE_） | 请求值约束 |
| policy | POLICY_NOT_APPROVED / POLICY_SCOPE_MISMATCH / POLICY_ARGUMENT_MISMATCH | 无审核依据或作用域不符，NEEDS_REVIEW |
| dependency | DEPENDENCY_FORWARD_OR_UNKNOWN / DEPENDENCY_AUTH_RESPONSE / POLICY_DEPENDENCY_MISMATCH / DEPENDENCY_RESOLUTION_FAILED | 引用顺序、Token 绑定或解析错误 |
| intent | INTENT_NAME_MISMATCH / CREATOR_MISMATCH / PATH_MISMATCH / RELATION_AMBIGUOUS / UNSUPPORTED_LANGUAGE（共同前缀 INTENT_） | 明确任务不符或无法可靠验证，NEEDS_REVIEW |

完整固定码由对应模块的显式抛出点定义；不把模型生成的文本当错误码。模型 API 超时、服务错误、预算等仍使用原有 MODEL_* 分类。意图满足率与策略选择正确率由独立评测器读取人工标注计算；运行时 `intent_verified` 不代替该评测。

首次历史 T01/T05/T09 只有粗分类，精确原因保持未知；新诊断不回填历史。T12 只确认课程名摘要与要求不同，不猜测原始名称或模型动机。T18 的首轮工具失败也不被后续修复抹去。
