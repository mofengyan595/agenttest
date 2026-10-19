# 竹湾交接：真实自然语言场景的固定回归

首次真实需求、配置审阅范围、来源和局限见[完整使用指南](../../../docs/nl-usage-guide.md)。
`provenance.json`保留来源run、准确源码、原报告摘要与实际无模型回归结果；全部DEVELOPMENT_ONLY / UNREVIEWED。

在仓库根目录、现有dev环境运行：

```powershell
.\.venv\Scripts\python.exe -m pytest examples/regressions/NU01 -q
```

`bundle/`由真实CLI完整通过后的固定模板导出，四文件及manifest没有手工修改。
`project/`是必要的显式配置：OpenAPI、隔离目标与角色、批准策略、五步TaskContract及四条偏序、只读能力。
`configuration-review.json`是代理静态核对，不是独立人工审核。
其中reviewed_files保留源归档的字节摘要；便携项目JSON依仓库规则规范为LF，
其实际字节摘要另存provenance的portable_files_sha256。原契约/目标/策略/任务的语义指纹不变。
`conftest.py`是操作员维护的ASGI运行适配器，不是模型代码；它禁止模型和真实HTTP transport调用。
每次回归创建新的合成工单服务和短期凭据，使用原策略断言，不恢复原请求，不做写入重试。
列表步骤只断言HTTP200和items存在；本包不证明列表归属过滤正确性。
固定回归报告为operator_plan，不能用其PASS声称再次完成自然语言规划或节省了人工工时。
