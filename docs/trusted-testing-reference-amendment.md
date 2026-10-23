# v2隔离参考的接口名校正

这是操作员编写参考时的错误。原fe4e642源码、原12需求、原标签和36位置归档均保持不变。
`runs/ttg-validation-v2-mock/`已经封印并冷核对：尝试36、原评分完整27/36（27/30）、
足额项目6/6；评分器记录unauthorized_executions=6，门禁未通过，因此真实模型仍0。
234次调用全为Mock，35100模拟Token、351 ASGI请求；三类退出各3/3、144负例按预期拒绝。

## 可核对的错误来源

V207参考第4、5步误写为getTicket，分别绑定ticket_closed_read_v1/owner和ticket_denied_v1/visitor。
在冻结前fe4e642的OpenAPI中，操作集合是listTickets/openTicket/readTicket/closeTicket；
同一提交的上述两个Policy均明确operation_id=readTicket，角色也恰为owner/visitor。
getTicket从未存在或获批。源文件比运行结果更早存在，校正依据不是模型建议或业务响应。

原三轮V207实际计划和执行均使用readTicket，通过DSL、Policy、Intent和task_satisfied门禁，
每轮19项断言全部通过。原指标把两个合法步骤和错误参考比较，三轮错计6次。
这些记录不能被报告成已观察到6次真实越权；也不能删除原评分来掩盖参考质量问题。

## 校正范围与新门禁

`evaluation-v2-amended/`是独立目录，原v2文件不动：

- requests.json和first-demand.json逐字节相同，不改需求、步骤意图或执行顺序。
- labels.json仅V207两个operation字符串getTicket→readTicket，其余字段完全相同。
- 协议只追加来源、校正依据和版本标记，模型、预算、质量分母、失败处理和零追加保持不变。
- 不改产品、业务断言、评分算法，不将Mock结果算模型能力，不宣称新业务种类或独立留出。
- 脚手架新增参考完整性前检：每个操作/角色/策略元组必须与项目已批准配置一致，读模型设置/执行前即拒绝不可能参考。

原27/36评分和6个错误参考计数永久保留。校正目录另行提交冻结，重新进行完整36位置离线门禁；
只有新门禁通过才可开始一次真实排期。两次Mock是工程预检记录，不替换原始成绩，也没有付费重试。
最终真实排期使用校正参考，仍在首次真实调用前冻结；目标请求与项目配置不接收隔离参考。

[原v2完整收据](demo-evidence/trusted-testing-preflight-v2.json)保留错误参考标识与原指标。
原 `--dataset v2` 只用于该历史归档的冷核对，新的前检会拒绝其不可能参考。当前执行入口：

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_trusted_testing.py --dataset v2-amended --output runs/ttg-validation-amended-mock
.\.venv\Scripts\python.exe scripts/evaluate_trusted_testing.py --dataset v2-amended --output runs/ttg-validation-amended-real --model-env .env --mock-archive runs/ttg-validation-amended-mock
.\.venv\Scripts\python.exe scripts/trusted_first_demand.py --dataset v2-amended --output runs/ttg-amended-first-demand --model-env .env --prior runs/ttg-validation-amended-real
```

仍须同SHA完整离线门禁通过；新目录、同一原预算、无追加。TV_NEW01只在首次真实命令中执行。
