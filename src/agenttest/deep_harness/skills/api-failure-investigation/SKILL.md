---
name: api-failure-investigation
description: Investigate an intermittent API error or a failed check using approved observations and persisted execution evidence.
---

# 失败调查

1. 用 `get_test_scope` 找到相关义务。优先使用已有报告或可执行的只读观测。若只读观测需要资源前置条件，只能通过已批准、带清理的分组建立。
2. 发现失败时，把实际 `run_id` 传给 `investigate_failure`。需要核对多个步骤时用 `read_test_report`，按步骤或操作定位证据。
3. 把断言失败、环境故障和未通过预检的方案分别报告。引用预期、实际结果和失败所在步骤。只有已批准前置条件成立的断言失败才能归为业务异常。
4. 调查不能重放结果未知的写操作。没有异常证据时，说明观察范围和未证实原因。
