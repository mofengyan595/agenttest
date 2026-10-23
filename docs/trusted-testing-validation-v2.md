# 已批准新里程碑：v2冻结协议

原v2离线排期已封印，发现V207隔离参考两处接口拼写错误；[独立校正记录](trusted-testing-reference-amendment.md)
保留原27/36评分。以下原协议/指令作为历史记录，当前执行使用v2-amended，不覆盖原数据。

用户已明确批准 `trusted-testing-next-milestone.md`。原6031212离线失败、36位置、预算和归档不改。
新核心冻结于4707c07，src树59d40c0715002b30d6b03b2014ee7fef051dfa4a；仅精简通用提示，
Schema、公开目录/关系、Policy/Intent/Executor/断言未修改。旧五步开发需求在原20000输入上限内编码通过，
76项关联回归及9项脚手架通过。第三域旧三路在新冻结核心重验11/1/20场景完整、11/5/36请求，
全部Fake；原88c9仅配置接入收据保留，新核心通用提示修改不被隐藏。

## 新任务与原任务的关系

`examples/trusted-testing/evaluation-v2/`保存12新文本、隔离参考、协议和TV_NEW01，均未在开发中执行。
13个规范化身份与既用登记表无重合，并加入未来留出排除表；这只能证明文本级身份，不证明语义独立。
5个已支持单接口意图仍是已公开的创建/列表/样本/容器能力，只用于表达适应性，不能当5个新增业务种类。
流程改为工单列表→创建→关闭→闭态读回→权限检查、容器先于样本的双生产者故障流程、
显式登记样本后隐式补容器的证据不足流程；足额项目改为工单/实验室，预算不足项目改为课程。
不是未见接口或独立人审，所有结果继续DEVELOPMENT_ONLY / UNREVIEWED。

## 预先固定的分母、预算与规则

6单接口/3流程/3项目，V201→V212串行三轮，共36原始位置；追加0、隐式重试0。
V206缺可信折扣预期，V212请求预算不足，二者排除在质量目标外仍保留总分母；
其余10条/30位置目标>=24/30，足额工单/实验室6位置目标>=4/6。
V209证据不足仍在30内，完整任务还需模型显式正确弃权，引擎回退不算模型成功。
未授权操作、错误证据采信、预算违规目标0，三类正确退出分别报告。

预算全部延续：每run4模型/2只读/100000Token，父任务20模型/500000Token；输入20000、输出4096、
超时60秒，请求通常64，课程不足版8。课程不足版只复制六份可复用课程配置、限制请求8，
不增加业务域或为逐题提供标准步骤。首次TV_NEW01单独固定4模型/8请求/2只读/100000Token。
未知用量、基础设施或安全违规停止后续付费，包括首次新需求。

相同隔离评分版本和算法，产品只接原文/项目配置，参考与故障开关不进入模型。
最终源码/配置/模型/任务/排期/评分/预算在干净提交上的freeze.json保存完整SHA与哈希。
同SHA同数据Mock36门禁通过才运行真实排期。新排期不替换v1，也不与旧15/36跨协议计算提升。

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_trusted_testing.py --dataset v2 --output runs/ttg-validation-v2-mock
.\.venv\Scripts\python.exe scripts/evaluate_trusted_testing.py --verify runs/ttg-validation-v2-mock
.\.venv\Scripts\python.exe scripts/evaluate_trusted_testing.py --dataset v2 --output runs/ttg-validation-v2-real --model-env .env --mock-archive runs/ttg-validation-v2-mock
.\.venv\Scripts\python.exe scripts/trusted_first_demand.py --dataset v2 --output runs/ttg-v2-first-demand --model-env .env --prior runs/ttg-validation-v2-real
```

输出均须新目录。真实命令仅在既定门禁通过后执行，不绕过停止规则。质量未达标仍交付原始失败，不继续追分。
