# TV_NEW01 固定回归

原始需求：创建工单“岑川终验”，然后关闭、列出、读回、权限拒绝。

首次真实执行源码f1c34eb5990287f8aca8fea76372dcac085246fc，原报告
fc11977feb47432e940c7b88e20c3ed1；一次ecnu-plus规划，2979 Token、5次ASGI请求、PASS、task_satisfied=true。
该需求未做Fake预执行，无追加尝试；原模板、scenario和manifest逐字节复制，配置快照请求上限8。

原命令在业务与导出成功后误用批次封印格式，CLI退出1；仅补归档元数据后冷核对通过，
没有重新调用模型或目标。这个包装失败不改变原始PASS，也不被隐藏成首次CLI全程成功。

```powershell
.\.venv\Scripts\python.exe -m pytest examples/regressions/TV_NEW01 -q
```

固定回归通过时是operator_plan / task_satisfied=null，模型与真实HTTP均被fixture禁止。
这验证新鲜ASGI环境中的固定计划，不是第二次自然语言规划成功，不恢复原HTTP会话或原写请求。
开发者自检，独立人审未完成，人工节时未知。
