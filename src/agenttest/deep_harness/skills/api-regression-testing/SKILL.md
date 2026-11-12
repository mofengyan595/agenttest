---
name: api-regression-testing
description: Verify a recently changed API or a reported regression, especially delete and read-back with the original resource identity.
---

# 回归测试

1. 用 `get_test_scope` 找到包含变更接口和必要前置条件的已批准分组。优先执行该分组。
2. 对删除回归，用 `get_approved_policies` 核对删除后回读是否获批。将设计约束写入 `run_test_group` 的 `design_note`。要求从批准的创建或资源来源绑定同一身份，先验证存在，再删除，再用同一标识回读。沿用政策批准的角色和状态断言。
3. 只有删除成功还不足以证明资源消失。若回读没有批准的断言或缺少前置条件，报告相应的缺口。
4. 从覆盖工具确认变更相关义务和清理凭据。对于已执行的等价用例，复用报告。
