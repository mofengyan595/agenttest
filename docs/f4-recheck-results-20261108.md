# F4 遗留问题独立复验 + 隔离读写生命周期准备（2026-11-08）

本文件回答五个交付项。所有结论只依据冻结后一次跑完的 12 个真实首位
（`docs/evidence/f4-recheck-20261108/`），第四轮证据保持原样未改。

- 冻结代码：`9b58036ad4eb3b1af3ec440220ae932be756cfcb`
- 对比版本：baseline `8111a37358f1cc161249016035ff0c25c68ea0ca`，
  candidate `a779ca56fde7a60228a5804425f045964503d2f2`
- 冻结清单 SHA-256：`bcda9bd50ec07eb6a32bb2486aed86bf1dc410810c02ac20e1ca8e92a3d60ff1`
- 证据包 SHA-256：`04a734e7f36796dbe392f287f30dff42f7dbc6b3e9babb3ffdc50f534f91aefc`
  （155 个文件，0.70 MB，冷重算 `MATCHED`）

## 1. 需求↔rubric 一致性预检（确定性）

新增 `scripts/f4_recheck/consistency.py`：把需求原文与 rubric 逐条对齐，能报
`extra_action`（rubric 多出原文没有的动作）、`missing_action`、`wrong_identity`、
`role_not_approved`、`wrong_order`、`wrong_hold`（把已批准断言当成待审）、
`missing_hold`（丢掉未批准断言）、`unjustified_repeat`（原文没要求却重复）。

| 输入 | 结果 |
| --- | --- |
| 第四轮冻结 F4 rubric | **1 条**：`extra_action: step 2 fetchNotice（公告）is absent from the requirement` |
| 本轮新 G1 / G2 / G3 rubric | 0 条（三条新 rubric 与原文一致） |

11 个单元测试（`tests/test_f4_rubric_consistency.py`）逐一构造上述 7 类缺陷，证明该
lint 不是"恒返回空"：删步骤→`missing_action`，改身份→`wrong_identity` +
`role_not_approved`，调顺序→`wrong_order`，复制步骤→`unjustified_repeat`，把已批准断言
改为待审→`wrong_hold`，权限缺口落在自己身份上→`wrong_hold`，丢掉未批准断言→
`missing_hold`，并复现第四轮 F4 的真实 `fetchNotice` 缺陷。**预检在前，付费在后；
冻结后未再改动任何 rubric 或评分规则。**

## 2. F4 新样本复验：12 个真实首位结果

三条新需求（新领域、新措辞，未复用第四轮文本）：G1 动态绑定 + 已批准断言 +
独立匿名读；G2 已批准读 + 未批准内容断言（局部待审）+ 独立身份读；G3 两个权限询问 +
独立匿名读 + 重复读。

| 指标 | baseline | candidate |
| --- | --- | --- |
| 审计位置 | 12/12 | 12/12 |
| 整套完成度 `full_execution`（分母 2） | 1 | 1 |
| 正确待审 `correct_hold`（分母 4） | **0** | **2** |
| 错误拒绝 `false_refusal` | 4 | 3 |
| 静默遗漏 `silent_obligations` | 28 | **17** |
| 多余提问 | 0 | 2 |
| 三类越权计数（configured/task/pending） | 0 / 0 / 0 | 0 / 0 / 0 |
| 真实 HTTP 请求数 | 7 | 9 |
| 模型调用 / 已知 token | 9 / 60,745 | 9 / 60,338 |
| 中位耗时 | 15.96 s | 19.21 s |
| 位置状态 | 3 FINISHED / 3 NEEDS_REVIEW | 4 FINISHED / 2 NEEDS_REVIEW |
| 纯表述型错误拒绝 | 1 | 0 |

分任务：

| 任务 | baseline | candidate |
| --- | --- | --- |
| G1 动态绑定 | r1 完整执行（3 次 HTTP）；r2 也发了 3 次 HTTP，但把独立的码头读排在链条之前，`order` 义务失败 | r1 完整执行；r2 为两次已批准读凭空提出澄清问题，0 次 HTTP |
| G2 局部待审 | r1 模型响应违反 schema 被拒；r2 **问对了 `$.seal` 问题，却把已批准的 `readEntry` 一起扣住**（只发 1 次 HTTP） | r1/r2 都因"未批准缺口缺少原文引用"被拒，0 次 HTTP |
| G3 权限询问 + 独立执行 | r1 权限问题未配动作被共享校验拒绝；r2 缺口无引用被拒；两轮均 0 次 HTTP | r1/r2 均提出 2 个 403/404 选择问题 + 真实执行 3 次匿名读，`correct_hold` 2/2，静默 0 |

验收（冻结前写定）：8/10 条满足，**未通过**——`g2_correct_hold_both_rounds` 为 false。
其余均满足：12/12 审计、无 stop、错误拒绝更低（3 < 4）、静默未增加（17 ≤ 28）、
逐任务正确待审无回退、候选三类越权计数为 0、无 UNKNOWN、无纯表述型错误拒绝。
按冻结纪律：**不重跑、不改评分、不换 rubric**。

## 3. 失败归因（产品 / 模型 / 夹具与 rubric 三分）

| 位置 | 归因 | 依据 |
| --- | --- | --- |
| G1_r1 两臂 | 通过 | 3 次 HTTP，动态绑定与已批准断言都执行 |
| G1_r2 baseline | 模型规划失败 | 计划顺序与原文"随后"相反，执行器照做 |
| G1_r2 candidate | 模型规划失败 | 对已批准读提出 2 个澄清问题，0 次 HTTP |
| G2_r1 baseline | 模型规划失败 | `MODEL_SCHEMA_TYPE_OR_CONSTRAINT` |
| G2_r1/r2 candidate、G3_r2 baseline | 模型规划失败 | `FOCUS_REQUIREMENT_GAP_WITHOUT_EVIDENCE` |
| G2_r2 baseline | **产品能力缺口** | 模型问对了 `$.seal`，产品仍把整组扣住，已批准读未执行 |
| G3_r1 baseline | 模型规划失败 | `FOCUS_PERMISSION_QUESTION_WITHOUT_ACTION` |
| 本轮夹具/rubric | 0 缺陷 | 一致性 lint 0 条、评分器 8 个负控全部命中、执行预检双流通过 |

关键澄清：`FOCUS_PERMISSION_QUESTION_WITHOUT_ACTION` 与
`FOCUS_REQUIREMENT_GAP_WITHOUT_EVIDENCE` 在两版源码里**都存在**（
`product_focus.py` / `request_requirements.py` 的代码集合逐条相同），所以它们是共享校验，
不是候选版新增能力。第四轮把 F4 r2 candidate 判为"模型规划失败"因此是对的；
**候选版的真实改动面是 7 个模块 246 行**（诊断、计划构造、`requirement_ledger` 等）。

## F4 遗留问题的最终判断

1. **rubric 多出一步已被独立确证**：第四轮 F4 的 `fetchNotice` 不在原文里；把这一步
   去掉后，F4 r1 的正确行为（2 次已批准读 + 正确待审）就是完整执行。这解释了第四轮
   F4 r1 两臂"看起来都错"的一半原因。
2. **动态数据源 + 未批准断言 + 局部待审 + 独立执行**这个组合边界**产品仍未支持**：
   G2 r2 baseline 是最干净的证据——模型给出了正确的待审项，产品却把整个计划扣住，
   已批准读一次都没发。候选版没有变好，只是更早地在"缺口必须引用原文"这一关被模型
   自己的格式问题挡住。
3. 候选版在**权限询问 + 独立执行**（G3）上确实修好了：2/2 正确待审、0 静默、0 错误
   拒绝，并真实发出 3 次匿名读。这正是第四轮 F4 想验证的另一半边界。
4. 因此 F4 不是单一原因：**rubric 缺陷 1 处（已修复并复验）+ 产品局部执行能力缺口 1 处
   （仍存在）+ 模型规划失败 7 处（本轮）**。把 F4 一律归因于 rubric 或一律归因于模型，
   都会被这 12 个位置推翻。

## 4. 隔离读写生命周期测试准备（只准备，不启动）

详见 `docs/rw-lifecycle-plan-20261108.md`。要点：

- **夹具**：`scripts/rw_lifecycle/`，`127.0.0.1:19097`，POST/GET/PATCH/PUT/DELETE +
  列表，每次运行独立命名空间、幂等键、所有权 403/匿名 401、故障注入、
  `__reset`/`__state`/`__audit` 控制面（需私有令牌）。
- **审批**：`WRITE_AUTHORITY["operator_approval"]="NOT_GRANTED"`，`startable()` 是唯一
  闸门；未批准时抛 `WRITE_AUTHORITY_NOT_GRANTED`，隔离/变更/回环四项逐条校验。
- **评分器**：只读夹具审计（方法、身份、状态码、幂等重放、故障标记、请求前后状态哈希），
  输出创建/重放/更新/删除/拒绝/越权/命名空间/清理/补偿指标与 `PASS|FAIL|NEEDS_REVIEW`。
- **确定性负控**：真实 loopback HTTP，10 个用例，7 个应有 PASS、3 个应报缺陷，
  全部按预期命中；0 次模型调用。
- **复用既有闸门**：`build_generic_project`、`validate_lifecycle_authority`、
  `lifecycle_plan` 接受该声明与"创建→读→改→替换→删除"计划，6 类不安全计划逐条被拒
  （12 个单元测试）。

准备阶段的三个产品侧发现（只记录，不改产品）：

1. 必填**头**参数暂不支持（`request_validation.py` 抛 `UNSUPPORTED_REQUIRED_PARAMETER`）；
   幂等键今天只能走 body `idempotencyKey` 或 query，夹具两种都收。
2. 产品没有删除后的**回读校验**，清理证据由夹具与评分器保证。
3. 审计在夹具侧，验收证据是"夹具审计 + 产品执行记录"两份独立材料。

## 5. 能否正式启动隔离写入验收

**准备就绪，但不在本阶段自行启动。** 需要操作员先做两件事：

1. 批准 `docs/rw-lifecycle-plan-20261108.md` 第 3 节的 6 条（可写范围、隔离环境、
   命名空间与重置、清理策略、故障注入用途、预算与停止条件）；
2. 决定幂等键的传输方式：接受 body `idempotencyKey`（今天就能跑），还是先给 intake
   补必填头参数支持（需要另立小里程碑）。

另有一条来自第 3 节的风险提示：产品**不支持"部分执行 + 部分待审"**。写入验收如果包含
"某一步待审、其余步骤照常执行"的用例，会撞上同一个能力缺口，需要先决定验收口径
（整计划扣住算通过，还是必须局部执行）。

## 6. 本轮源码与测试

| 提交 | 内容 |
| --- | --- |
| `f74bbce` | F4 复验方案、rubric 一致性 lint、12 位复验 harness |
| `086086f` | 隔离读写夹具、审批闸门、生命周期评分器、10 个负控 |
| `9b58036` | 链条动态绑定声明 + lint 测试改为按路径加载（不再污染 `sys.path`） |

测试：`tests/test_f4_rubric_consistency.py` 11 项、`tests/test_rw_lifecycle_preparation.py`
12 项、评估侧 27 项；`ruff check .` 全绿。预检全部在冻结前完成：22 项身份/响应/intake
检查、两臂各 2 条执行流、两臂各 8 个评分器负控、2 个项目 schema 编译（6 个离线载荷/臂）。

一处如实记录的准备期修正：链条项目原本把 `entryId` 声明成字面量，导致真实执行预检报
`POLICY_LITERAL_REQUIRED`；改为 `source_operation=openRegister / selector=$.entries[0].entryId`
后通过。该修正发生在冻结前，已计入 `9b58036`。

## 7. 未做与不建议做的事

- 未重跑第四轮旧位置，未用旧任务提分；第四轮证据、rubric 与结论保持原样。
- 未开始第五轮大规模 A/B；本轮 12 位只用于 F4 复验。
- 未启动任何有状态写入；`startable()` 在未批准前始终拒绝。
- 未修改产品运行时代码；本轮产品侧只做归因，不改实现。
