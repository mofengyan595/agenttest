---
name: api-functional-testing
description: Test a feature or prepare a service for release. Choose approved functional coverage and exercise complete resource lifecycles.
---

# 功能测试

1. 用 `get_test_scope` 定位目标相关义务。一次只执行一个有价值的分组。
2. 功能测试优先选含核心写操作的分组。给 `run_test_group` 的 `design_note` 明确写出同一资源的正常读取、批准的更新和清理顺序。只有批准策略提供删除后回读时才加上该检查。
3. 上线前测试先覆盖各相关分组及不同类型的已批准义务。完成一条生命周期后，优先处理仍未执行的分组或独立的已批准参数检查。创建更多资源不能替代覆盖。
4. 用 `get_test_coverage` 检查未测义务、异常和资源状态。补测需对应不同义务，避免只重复一次创建或读取。
5. 报告中引用真实证据，列出未测义务和待清理资源。以产品完成判据为准。
