# 模拟服务人工编写策略 v1

这份策略由任务书要求落实为固定示例，仅适用于 AgentTest 一次性 mini target。
`reviewed: true` 表示示例作者确认的预期；平台不能为未审查策略自动补上此标记。
真实服务的策略必须由其业务负责人另行审查，不能直接套用这些预期。

## authentication

本地临时账号密码由演示运行时生成，仅在内存中使用。成功登录返回 200 和 access_token。

## resources

创建课程返回 201 和 courseId；创建者读取返回 200、保留创建时的 name。

## ownership

另一个已登录用户读取创建者的课程必须返回 403。
`ownership` 故障开关只在独立 mini target 中跳过归属检查，应产生 ASSERTION_FAILED。

## batches

上传两行合成 CSV 返回 202 和 batchId；第一次查询为 RUNNING，之后为 SUCCEEDED，count 为 2。
终态只接受 SUCCEEDED；FAILED 是合法可观察终态，但违反成功测试预期。
结果接口返回两条 items。`terminal` 故障开关令终态为 FAILED，应产生 ASSERTION_FAILED。

报告保存策略内容 SHA-256，可与原始 JSON 计划重算比对；断言值不会自动修改。
