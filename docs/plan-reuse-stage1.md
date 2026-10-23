# 阶段1：成熟工具开发对照与选型

起点 `86d68225ab39c6f31d950298e51d0c77d1f2cac0` 工作区干净、远端 master 相同。
准确 SHA 的 Python 工作流39965588196四组合、Web39965588041双平台六项全部success。
本阶段真实模型0、外部业务HTTP0、真实数据库写入0，全部DEVELOPMENT_ONLY/UNREVIEWED。

## 官方资料与版本

2026-10-23读取并核对官方[数据生成](https://schemathesis.readthedocs.io/en/stable/explanations/data-generation/)、
[状态依赖](https://schemathesis.readthedocs.io/en/stable/explanations/stateful/)、
[Python API](https://schemathesis.readthedocs.io/en/stable/reference/python/)。生成包含多阶段，失败收缩也会发送额外请求；
状态测试可从响应连接接口，公开契约和Links有维护成本，不能假定通用工具没有跨接口能力。
官方[OpenAI函数调用建议](https://developers.openai.com/api/docs/guides/function-calling)支持将已知参数和确定性工作交给代码；
这只是职责划分依据，不是本项目有效性的实验证据。

PyPI确认Schemathesis4.32.4要求Python>=3.11。先说明用途后安装至独立`.venv-comparison`；
项目依赖随后对齐原dev/agent锁文件，完整Windows快照见`requirements-comparison.lock`。
主`.venv`和系统环境未改，基础离线路径不依赖Schemathesis。依赖检查无冲突。

## 公平范围

复用原validation专用内存目标、同一公开OpenAPI、9项批准策略和确定性断言。
三组每目标状态各9个请求预算，初始状态都是全新ASGI app；目标故障开关仅在隔离脚手架中选择。
固定pytest手写9个策略用例及合法输入fixture；当前AgentTest用自然语言入口和模拟规划器；
Schemathesis使用公开API的策略生成9个正例基线，再由相同批准规则产生8个负例。
**三组的固定负例变化及业务断言均来自AgentTest规则，不归功于Schemathesis或模型。**

Schemathesis另生成9份原生负例，仅记录不执行：宽泛fuzz不等于逐字段固定批准。
Hypothesis只开generate阶段、固定设置、无database/收缩；生成时零HTTP，
不使用工具自带网络执行器、状态探索或重试。实际请求仍过原Executor、Policy和共享预算。
本阶段仅是无状态、共同批准子集的适配实验，**不能代表Schemathesis完整原生能力或工具优劣排名**。
跨接口和自然语言没有在此处混用分母；通用工具自然语言任务记能力范围外，不记失败。

## 首次开发结果与限制

[公开收据](demo-evidence/plan-reuse-tools-development.json)，完整报告在`runs/plan-reuse-tools-development-v3`。
每格分母均9；表中通过是实际断言通过，故障目标的失败是预期检出。

| 组 | 正常：请求/业务通过 | 错收非法输入：请求/业务通过 | 错误码不符：请求/契约通过/业务通过 |
|---|---|---|---|
| 固定pytest | 9/9 | 9/1 | 9/9/1 |
| 当前AgentTest模拟 | 9/9 | 9/1 | 9/9/1 |
| Schemathesis数据+批准适配 | 4/4 | 4/1 | 4/4/1 |

Schemathesis各状态5项基线中的整数使用了`5.0`等JSON数值表示，触发现有严格类型检查，零HTTP。
这是生成器与AgentTest支持子集的适配差异；生成器没有声称完成自然语言目标，不能将5项当成模型或工具缺陷。
未修值、未删分母。契约能发现非法输入被接受，正确422但业务错误码不符仍需批准业务规则检出。
固定和模拟组27个请求，工具适配组12个请求，所有单组<=9；模拟规划共27次，真实调用0。

两次原脚手架失败分别为未执行报告索引和bytes证据序列化，目录v1/v2保留；
v3只完善记录，不修改业务断言或生成值。原始生成数据保存在归档中，bytes显式保存类型。
无人工计时，不能报告节省工时。接入仍需审核契约/规则/目标和工具适配，不能隐藏配置成本。

## 选型与验证

本轮继续使用已有确定性派生；Schemathesis作为独立可选对照，暂不引入产品依赖或第二套执行体系。
理由是已有逐字段批准能力，当前直接适配还会拒绝部分合法JSON Schema数值表示；
完整契约模糊测试和原生状态探索仍适合交给成熟工具，后续需另行批准更宽的执行范围。

主环境：`pytest tests/test_mature_tools_comparison.py tests/test_approved_negative.py -q`：29 passed/1 skipped。
工具环境：同文件的工具对照测试2 passed；最终重验2 passed/27.48s，Ruff与pip check通过。
沙箱默认临时目录失败、Windows异步唤醒socket阻塞均未计作产品失败；
改仓库内basetemp并在批准的沙箱外运行离线测试，测试防联网钩子保持开启。

下一准确阶段：从保存失败定位合法基线问题，实现任务内不可变合法结构复用与确定性变体；每个变体重新走原门禁。
