# 自然语言规划的可信策略

此文件和 examples/nl-policies.json 由操作员选择并管理，模型无写入权限。
这些规则延续 mini target 已有契约，独立于 user_task，不通过模型的 reviewed 获得批准。

| ID | 角色及前提 | 不可改变的断言 |
| --- | --- | --- |
| nl_login_v1 | alice / bob，执行器注入认证请求，普通参数为空 | 200 且 access_token 存在 |
| nl_create_v1 | alice / bob，name 为 1–80 字符的普通字面量 | 201，courseId 存在，响应 name 等于提交 name |
| nl_read_v1 | 与已批准创建步骤相同角色，绑定其 courseId | 200，响应 courseId 等于请求 ID，name 等于该创建步骤提交的 name |
| nl_forbidden_v1 | 与已批准创建步骤不同角色，绑定其 courseId | 403 |

只开放上述参数槽。步骤名和课程名称可以变化，接口、角色关系、来源选择器、比较关系和状态码不能变化。
查询不能用任意常量 ID，也不能绑定其他操作、另一条创建步骤的名称或认证响应。
原有 examples/demo-policies.json 的固定绑定完全保留；新策略仅用于独立 mini_target_nl_v1 场景。
测试名称只允许合成数据。课程及账号都属于本次运行的一次性 mini target，结束即丢弃。
