# 新12需求三轮冻结协议

本文件在最终12需求的任何执行前建立。请求、隔离参考矩阵、协议和首次新需求位于
`examples/trusted-testing/evaluation/`，评分版本 `trusted_testing_isolated_score_v1`。
全部为开发者冻结的 DEVELOPMENT_ONLY / UNREVIEWED；独立人审=false，人工节时=null。

## 固定分母与目标

12条不同文本/组合，6单接口、3流程、3项目；串行按轮先TT01至TT12，共三轮36个原始位置。
接口与项目配置在开发中已公开，不能将其称为未见接口、盲测或广泛泛化证据；同意图改写不算新业务种类。
TT01工单创建、TT02工单列表、TT03课程创建、TT04实验室样本、TT05实验室容器；
TT06未知收费；TT07工单有序流程、TT08双生产者流程与权限故障、TT09证据不足装载查验；
TT10课程项目、TT11实验室项目、TT12请求预算不足工单项目。

运行前确定10条/30位置为“预期明确、已授权、预算足够”，目标完整任务>=24/30。
预算足额项目2条/6位置，目标>=4/6。TT06无可信收费预期，TT12项目请求不足，二者不进质量目标分母，
仍留在36总分母。TT09保留在30内，完整任务必须实际执行完并由模型显式正确弃权；引擎回退不算模型成功。
未知预期、预算不足、证据不足各3位置分别报告正确退出，不充作正常业务PASS。
未授权操作、错误证据采信、预算绕过目标均0。原历史15/36与本协议不直接计算提升幅度。

## 冻结与费用

先提交源码、配置、任务、参考、评分、顺序、预算；工作区必须干净，逐次模型/HTTP前核对源码和配置身份。
freeze.json 保存完整SHA、工作树内容指纹、公开模型参数、配置清单与哈希、任务/标签/协议/排期。
使用现有 ecnu-plus / chatecnu；真实调用前固定当前公开模型元数据，不保存密钥或明文服务地址。
同SHA、相同任务/标签的36位置Mock门禁全部通过后才可开始真实排期，Mock使用同协议适配器。

每run最多4模型/2只读调查、100000Token；每位置父预算20模型/500000Token；输入20000、输出4096，
每次模型超时60秒，目标请求64（TT12为8）。子用例、入口GoalSpec、规划、反馈、调查共享记账。
项目已保留后续组业务额度；不通过额外调用追分。初次排期重试0、追加0，失败原样保留。
未知用量、基础设施失败或预算违规停止其后所有付费工作，剩余位置保留未执行原因，包含首次新需求。
已知Token是供应商返回的报告值；没有公开单价/账单时不虚构金额。

## 产品与评分隔离

产品只接原文、可复用项目配置和允许的公开关系，不接标签、逐题步骤、参考答案或故障开关。
fixture故障由操作员脚手架选择，不由模型决定；严格PublicTrace防评分字段进入模型上下文。
隔离评分器独立检查组数/步骤集合与顺序、角色、策略、依赖生产者与选择器、鉴权和边界变体，
同时核对实际报告门禁、请求/断言/负例执行。产品自报complete不能替代独立参考检查。
原业务结果、可信规则结论、模型提案、主动正确弃权、错误提案和引擎拦截分别计数。
冷核对重算清单、36分母、排期、用量和评分，拒绝复用报告与未知用量后的执行。

## 首次新需求与执行

TT_NEW01未参与开发执行、不做Fake试跑、不重试。36位置结束且用量已知、无安全违规后，以同SHA和模型
第一次真实执行；总预算4模型/8请求/2调查/100000Token。操作员脚本仅将复用目标预算限制为8，
原文经既有run_goal派生门禁；成功才导出原固定pytest模板，不执行模型生成代码。
通过后无模型回归标记operator_plan / task_satisfied=null，不能算第二次自然语言成功。

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_trusted_testing.py --output runs/trusted-validation-mock
.\.venv\Scripts\python.exe scripts/evaluate_trusted_testing.py --verify runs/trusted-validation-mock
.\.venv\Scripts\python.exe scripts/evaluate_trusted_testing.py --output runs/trusted-validation-real --model-env .env --mock-archive runs/trusted-validation-mock
.\.venv\Scripts\python.exe scripts/trusted_first_demand.py --output runs/trusted-first-demand --model-env .env --prior runs/trusted-validation-real
```

这些是真实模型排期命令，模型费用仅由明确配置产生，目录须新建。归档保留，不能覆盖后重复调用。
质量未达标时交付工程结果和原失败，不改题、放宽断言或无限追加。三层离线演示见[原两域](trusted-testing-approved-projects.md)
与[第三域](trusted-testing-laboratory.md)，批准正常/负例固定回归见 `examples/regressions/approved-inputs/`。
