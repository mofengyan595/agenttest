# Goal 完成核对（2026-10-18）

按原 Goal 逐项检查，未把开发证据升级为正式独立评测。这里的源码与归档可定位实际实现和结果，不能仅凭本文声明通过。

| 原要求 | 当前证据与判定 |
| --- | --- |
| 现状、规则、分支、上游、远程及已有机制核查 | CODEX_TASK、README、progress 与实际源代码；master → origin/master，远程 AgentTest；原工作区干净，阶段性明确路径提交，未操作 CourseInsight |
| 固定引用诊断与零请求拒绝 | diagnostics/generic_references；test_reference_diagnostics、test_generic_references；GD 6/9、根分支旧失败保留，GW 新三题三轮 9/9，均开发证据 |
| 根响应/嵌套/数组与原门禁 | test_root_response_planning、test_generic_references、原 Policy/OpenAPI/Intent 回归；未放宽断言 |
| LangGraph/Pydantic 调查或停止闭环 | feedback.py / feedback_runtime.py，复用 Executor/Vault；每 run 四模型/两只读请求，最后决策保留给结论 |
| 非法工具/参数、重复、超时、预算、取消、证据不足 | test_feedback_runtime / test_feedback_check / test_feedback_cli，完整回归与真实 FE/PV 归档；未知用量停止逻辑不放宽 |
| 原判定独立、无写重放/自动修复/模型代码执行 | 原 Executor DSL，FeedbackResult 独立；business Outcome 与调查结论分别显示与评分 |
| Fake/Mock 后新任务真实验证 | FC 首次规划拒绝与两未执行保留；FE 业务 3/3、结论 2/3；PV 72 位置完整；全部使用已配置供应商 |
| 确定性接入草案及缺失信息、不自动授权 | onboarding.py、test_onboarding；五类待审项，独立草案 Schema、Literal false；check-project 拒绝草案 |
| 项目配置复用与两域成本 | onboarding_check 两域 3/4 文件复用、同模板 2 叶值改动；空白 3 摘要待填、复制模板 0、草案 0；不虚构人工节时 |
| 本地关联追踪及关闭上传 | local_trace.py、test_local_trace；FE 三报告五阶段、10 调用/8,647 Token 对齐，external_upload=false；没有 Langfuse 外部客户端 |
| 新 24 题四类各六、两域、三轮 | paired_tasks 与冻结；24 标识/五模板/72 位置，未将名称变体与轮次说成独立泛化样本 |
| 固定 pytest 对照、同目标断言、健康故障配对 | paired_baseline / paired_fixed_pytest；原 48 passed/24 failed，72 预期全部核对，24 正确配对；对照代码为代理预编写，非独立人工计时 |
| 固定源码/模型/输入/顺序/预算，无补跑和筛选 | f507a8b 的 37 原行及 c82bfa6 的 35 未执行位置；输入请求哈希一致，旧行/基线直接继承；原停止记录与版本均保留，未重试失败 run |
| DSL、授权、意图、完成、调查证据和结论分别评分 | 全 72 分母；59/56/51/27，配对24/24；证据20/21、相关工具23/28；原结论12/24保留，明确结束动作额外审计11/24 |
| 原失败/中断/未执行/Token/耗时/来源 | 原部分归档37+35、最终72+0；124请求/145,284 Token；Goal累计153/181,171；本轮业务 ASGI、真实第三方HTTP/DB写0；无未知用量 |
| 真实独立人审门禁及材料 | formal 仍要求真正独立人审；36 个 Goal 开发身份加入排除，enrollment.json 空包和 goal-independent-review.md 已交付；正式评测未完成，按 Goal 明示的开发评估回退交付 |
| README、架构、支持范围、运行/演示、证据、限制 | goal-delivery-guide、goal-value-report 及 README/technical-summary 更新；默认离线，无新增依赖；Docker/Langfuse 为未选可选项 |
| 五分钟正常/故障/反馈/拒绝/pytest 与只读 Console | guide 的五段脚本；原统一验收、反馈 Mock、现有固定回归；2 原 E2E +1 真实 FE 报告 E2E，桌面/390px 截图检查；没有 Web 执行入口 |
| 三条简历、代表案例和面试结构 | goal-resume-interview，技术/数字链接到代码或统计；问题→失败→方案→取舍→实现→验证边界；未编辑外部简历项目 |
| pytest/Ruff/CLI/构建、保护历史与敏感文件 | f507a8b 全量1527 passed/14环境跳过；调度48 passed；最终评分/配置/追踪39 passed；前端27 tests/typecheck/build；原归档未写回，私有输出/凭据不提交 |
| 最终统一交付与准确提交 CI | check_delivery 退出0，9/9 VERIFIED、source_unchanged=true、cleanup_unconfirmed=false；最终提交的六项 CI 按准确 SHA 在 GitHub 核对，原始核对响应保存 runs/goal-final-ci.json |

统一验收输出见 docs/demo-evidence/goal-delivery-summary.json；CI 以准确 SHA 对应的 GitHub 状态为证，不单凭本表声明。独立评测和人工节时是明确未完成/未验证项，不以代理声明或开发结果替代。
