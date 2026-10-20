# 独立人工评测待审核材料

状态：UNREVIEWED；真实独立人工审核 0；正式评测未完成。开发阶段结果不能升级为正式成绩。本 Goal 按授权完成开发排期和本材料，未填写任何虚假的审核人或独立性声明。

本机待审登记包 `runs/goal-independent-review-pending/enrollment.json` 由已有 `evaluate_v2.py review-template` 生成，只有空白登记结构，不是可执行 Suite。新克隆可重新生成同样空包：

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_v2.py review-template --output runs/review-new
```

审核者需要从真实、未用于实现/调参/开发评估的原始需求独立采样，保存来源材料摘要和带时区采样时间，核对每条 Role/Operation/Policy/参数/资源关系/子目标，编写独立 Rubric、故障标签与安全拒绝依据。只有真实参与者才能填写审核身份、是否参与开发、独立性确认和实际审核时间。代理可以整理材料，不能代签。

已用 PV1601–PV1624、FC1401–FC1403、FE1501–FE1503、GD1201–GD1203、GW1301–GW1303 原36个身份保留；
本轮另加入ED01–ED06、EV01–EV12、EE01–EE06，共60个唯一身份，位于
examples/eval-v2/goal-used-task-identities.json，由原require_review门禁核对；C/T/B历史排除继续保留。
规范化大小写、空白和标点不能绕过相同任务检查；语义相近和来源独立性仍需人工判断，
不宣称哈希能识别所有改写。新三组对照和自然语言确认均是DEVELOPMENT_ONLY / UNREVIEWED，不能升级为正式留出题。

本轮私有开发材料可供审阅实验方法，但不能成为独立留出题：

- `runs/goal-paired-live/freeze.json`：开发任务、输入、模型、预算与顺序。
- 同目录 `results.json`、`reports/`、`fixed-junit.xml`、`fixed-pytest.log`：完整分母、原始脱敏报告、固定 pytest 对照。
- 同目录 `archive.json`：文件摘要封印；`check_paired_development.py --verify` 只冷评分，不调用模型或目标。
- [独立开发协议](paired-development-protocol.md)：调查选择、有效证据、症状结论与证据不足的评分定义。

正式流程沿用 [人工审核说明](eval-v2-human-review.md)：真实审核后建立独立新版本 Suite，review-freeze 生成版本锁，再 validate --formal 核对锁与一致性。当前 formal 评分能力仍限原支持范围；新增调查指标保持独立开发协议，未把原正式评分器伪扩展成已通过正式验收。取得独立任务与人工标签是后续外部输入，不通过重复运行本轮题目替代。

## 2026-10-20 自然语言入口新增排除

上文60为旧Goal当时登记。本轮在既有79身份上新增NE01–NE12与NE_NEW01，当前共92个唯一身份，
均在[排除表](../examples/eval-v2/goal-used-task-identities.json)。首次新需求通过也不能成为正式留出题。
当前待审空包为runs/ne-independent-review-pending/enrollment.json，仍为UNREVIEWED、人审0。
新增[原三轮/追加重试/首次需求证据](nl-entry-value-report.md)只供方法审阅，不能代替独立采样。
