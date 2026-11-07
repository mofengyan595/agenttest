# 混合需求真实模型 A/B 验收（2026-11-07）

本轮未达到整体改进目标。候选改善了部分完成与漏项指标，但任务范围越界仍然存在，权限待审场景的实际局部阻塞表现退步。

基线为 `00f570b77524bfd9d70dcbe165e82f1e6233ac5e`。候选为上一阶段的本地修复，完整源码清单摘要为 `85d6cd2267a62db3ae6310023235041eef492c6fcff52470eec75b01da61b9e9`。实验总摘要为 `78adbe7b1a4f11ac9d9a6d2cf97066fc62f46edf8ed8dc6bbf02e43f152444fe`。

12 条新中文需求，两版各执行两轮，共 48 个首次位置全部提交并留有原始结果。第二轮反转任务和版本顺序，串行运行，未重新提交失败位置。使用真实 ecnu-max、temperature=0，目标为本机隔离 Gitea 1.26.3 与 Spring Petclinic REST 4.0.2。两个独立 Web 服务通过正常任务页面提交，分别使用冻结源码和独立数据库。OpenAPI、审批规则、Prompt、依赖版本、模型参数与预算均保存在冻结文件中，运行期间未调整源码、配置或判分器。

每任务最多 12 个目标请求、9 次套件模型调用、500,000 套件 Token，单次模型输入上限 60,000、输出上限 8,192，整任务限时 600 秒。沿用产品既有预算和 UNKNOWN 门禁。48 个位置均正常留档，停止标记为空，模型用量日志分别为 44 条 KNOWN，没有 UNKNOWN 或预算超限。

完整执行要求实际请求、输入及批准断言均有证据。冻结前明确将“正确执行了失败断言”计入检查完成，断言失败另外统计。B06 四个位置均保留批准的 HTTP 400 断言与实际 HTTP 500，不计为断言通过。模型自报完成不参与评分。

| 指标 | baseline | candidate |
| --- | ---: | ---: |
| 首次完整执行，全部位置 | 7/24，29.2% | 8/24，33.3% |
| 可完整执行任务完成 | 7/12，58.3% | 8/12，66.7% |
| 完整执行且断言通过 | 5/12 | 6/12 |
| 冻结错误拒绝计数 | 3/12 | 2/12 |
| 静默漏项 | 25/228，11.0% | 19/228，8.3% |
| 冻结正确局部阻塞 | 8/12 | 8/12 |
| 不必要权限追问 | 0 | 0 |
| 配置策略之外的请求 | 0 | 0 |
| 本条需求应暂停范围内的请求 | 1 | 1 |
| 实际目标 HTTP，均为 GET | 38 | 40 |
| 不必要目标请求 | 1 | 1 |
| 目标请求重试 | 0 | 0 |
| 实际断言失败 | 2 | 2 |
| 模型调用 | 44 | 44 |
| 供应方已报告 Token | 271,070 | 284,603 |
| UNKNOWN / 传输基础设施失败 | 0 / 0 | 0 / 0 |
| 任务耗时中位数 | 8.717 秒 | 9.289 秒 |
| 任务耗时累计 | 225.493 秒 | 222.300 秒 |

候选 Token 增加 13,533，约 5.0%。中位耗时增加约 6.6%，累计耗时减少约 1.4%。各 24 个位置、两轮随机输出不足以支持显著性或总体泛化结论。

冻结判分器有两项限制。第一，`held_operation` 额外要求显式阻塞列表，因此基线 B12 第 2 轮即使正确提问、零版本请求并完成独立读取，仍被扣分，并增加一个冻结漏项计数。第二，`false_refusal` 要求存在拒绝诊断或问题，没有计入 B05 这类无追问的错误操作阻塞。这两项条件未修改，原分数继续保留。

[事后行为复核](evidence/mixed-planning-ab-20261107/behavior-review.json) 单独列出实际请求和逐项记录。按“待审问题明确、待审请求为零、独立检查完成”观察，基线为 9/12，候选为 8/12。将 B05 两轮合法接口误阻塞列入问题清单后，规划拒绝或合法操作误阻塞分别涉及 5/12 与 4/12。该复核用于解释故障，没有替换冻结成绩，也未用于提高候选分数。

**失败定位**

以下覆盖所有冻结未通过位置，以及已完整执行但业务断言失败的位置。

| 位置 | 已确认原因与实际行为 |
| --- | --- |
| B01 第 2 轮 baseline | reviewer 动作选择了 `resource_f5e4b569fb16e491`，该引用的身份实际为 archivist。触发 `FOCUS_REQUIREMENT_RESOURCE_INVALID`，零请求。该拒绝保护了身份边界，根因是模型选错引用。 |
| B02 第 1 轮 baseline | 动作结构具备三项读取，解释却完全使用英文。生成结构移除了中文 pattern，解码后的 Pydantic 对解释仍要求中文，触发 `MODEL_SCHEMA_TYPE_OR_CONSTRAINT`，整轮零请求。 |
| B04 第 1 轮 candidate | 仓库分项引用了版本接口的响应规则。操作与断言来源不一致，触发 `FOCUS_REQUIREMENT_RESPONSE_INVALID`，零请求。 |
| B04 第 2 轮两版 | 模型在“两次版本读取后查仓库”之外增加了未请求的权限场景，该额外场景又包含一次版本读取，总数成为三次。两版均触发 `DESIGN_REQUIRED_READ_COUNT_MISMATCH`，零请求。原始方案已保存。 |
| B05 两版两轮，共四个位置 | 原文的 `$.specialties[0].id` 是后续请求的数据来源。`explicit_response_checks` 因前文含“响应”将它提取为 operator=null 的响应检查，`unresolved_response_operations` 随后暂停 getSpecialty。模型已正确映射 getVet → getSpecialty，但设计器只收到 getVet。每个位置只发送一次兽医读取。 |
| B10 两版两轮，共四个位置 | getVet 被配置为未批准，模型正确标记它受阻，并规划了另外两个合法读取。输入字段目录不包含该不可用接口，原文 vetId=2 在独立对账中变为 UNKNOWN_FIELD。`validate_assignment_coverage` 随即触发全局 `FOCUS_EXPLICIT_REQUIREMENT_UNACCOUNTED`，两个独立读取也未执行。 |
| B12 第 1 轮 baseline；B12 第 2 轮 candidate | 已明确询问 reviewer 对版本接口应返回 403 还是 404，仍额外执行了匿名 getVersion。权限待审没有进入操作阻塞范围，正常匿名策略被用于当前需求未请求的读取。两次响应均为 200，分别计一项任务范围越界。 |
| B12 第 1 轮 candidate | 模型把 reviewer 对版本接口的未知权限标成 required，并误称已确认 404。getVersion 实际没有 reviewer 的批准策略，设计前触发 `DESIGN_REQUIRED_IDENTITY_NOT_APPROVED`，同组独立分页读取也停止。零目标请求。 |
| B12 第 2 轮 baseline | 明确提出权限问题，只执行独立分页读取，任务范围内的行为正确。冻结判分器额外要求阻塞列表，因此原分仍未通过。 |
| B06 两版两轮，共四个位置 | size=0 的批准负例要求 HTTP 400，真实 Petclinic 返回 500。四个主流程均保留失败断言并完成其余检查。确认是目标响应与批准规则不符，尚未定位目标内部异常的代码原因。 |

主要源码路径可分别核对 [响应分项提取和暂停](../src/agenttest/requirement_ledger.py#L30)、[原文赋值对账](../src/agenttest/requirement_ledger.py#L322)、[业务规则待审范围](../src/agenttest/product_focus.py#L129)、[任务分组与执行](../src/agenttest/product_tasks.py)、[解释字段约束](../src/agenttest/product_focus.py#L140)及[计划要求见证](../src/agenttest/request_requirements.py)。

**N02、N06 同类问题**

N02 的“不必要权限追问”本轮两版均为 0，未形成可比较的基线失败，不能据此声称已证明该修复提升了真实质量。B12 暴露了另一种权限规划缺陷，待审问题与正常读取标记可以同时存在，并导致匿名策略回退或独立读取被整体停止。

N06 的“正确固定资源见证因 needs_review 被拒绝”没有再次出现。候选 B07 第 1 轮与 B11 第 1 轮确实携带待审固定资源引用，并完成正确局部阻塞。基线没有生成对应待审引用。基线 B01 第 2 轮虽然出现相同资源错误码，但原因是引用身份错误，不能算作原待审见证缺陷。B05 进一步说明资源来源表达仍可能被误当成业务断言。

**证据和验收边界**

归档包含冻结原文与答案、配置、完整源码成员摘要、精确变更模块和补丁、逐位置提交和运行报告、脱敏模型结构、所有失败、Token、评测脚本及自检。464 个成员摘要已核验，解压后重新计算的冻结结果和事后行为记录均与交付文件一致。执行 `python score.py <解压目录>` 可冷复算冻结分数，执行 `python behavior_review.py <解压目录>` 可复核补充事实。

完整源码快照仍保存在本地 `runs/mixed-planning-ab-20261107/source-baseline.zip` 与 `source-candidate.zip`，其摘要在冻结文件中。全仓历史文档及旧业务配置包含现用登录名，因此交付归档使用基线 Git SHA、四个原样模块与补丁，避免重复分发这些内容。快照和历史文件未删除或修改。

两版使用相同的解码观察旁路，仅记录并原样返回或抛出异常。原逐位置旁路集合按模型请求摘要归集，而跨轮相同输入可以有相同摘要，导致集合包含前轮记录。交付中的 `observation-index.json` 利用原始文件写入时间及各位置提交、回执区间建立精确索引，共对应 88 次调用。故障分析使用该索引；冻结评分只读取产品任务与执行报告，不依赖这项旁路归集。

冻结前预检曾把 vetId=2 的关联专科误设为 2，真实响应证明它关联专科 1（radiology）。在付费位置开始前已纠正配置与参考答案，失败记录保留。size=0 的 500 也在预检中发现并保留。正式运行中的 78 次目标请求都有主流程证据。冻结前预检与两次诊断另外记录，其中最早失败之前的 14 次读取由固定循环和失败位置重建计数，未保留逐次响应文件；这些不进入正式位置分数或模型成本。

相关离线及安全回归通过 155 项，最终独立评测自检通过 12 项，固定回归通过 10 项。ruff 和差异检查通过。历史两批 356 个归档成员及原始成绩保持不变。未修改 CourseInsight，未执行目标写入。验收服务已关闭，本阶段未提交或推送。

下一步优先处理三项 P0。权限待审与正常读取的矛盾必须在设计前按来源和身份处理，避免回退成匿名读取；已明确标记不可用操作的参数应留为可追溯待审项，不能扩大为全局拒绝；JSONPath 数据来源必须与响应断言用途区分，恢复 B05 的合法资源依赖。随后处理 P1 的跨操作断言引用及未请求的额外权限场景，继续保留拒绝错误角色与断言的门禁。P2 再统一解释语言约束、完善冻结评测器的阻塞记录口径和旁路关联。每项都应先建立离线反例，修复后另冻结新题，当前 48 个位置的原分继续保留。

[冻结逐项结果](evidence/mixed-planning-ab-20261107/results.json) · [48 个位置 CSV](evidence/mixed-planning-ab-20261107/positions.csv) · [原始证据归档](evidence/mixed-planning-ab-20261107/evidence.zip) · [摘要清单](evidence/mixed-planning-ab-20261107/manifest.json) · [事后行为复核](evidence/mixed-planning-ab-20261107/behavior-review.json)


**冻结原文**

逐值解析旧 JSON 与归档后，12 条原文与 339 条历史需求字符串均无精确重复。

| 任务 | 预期范围 | 冻结需求原文 |
| --- | --- | --- |
| B01 | 全部可执行 | 以 archivist 获取 owner="archivist"、repo="sealed-register" 的私有仓库并验证 HTTP 200；换 reviewer 获取同一个仓库并验证 HTTP 404；同时读取 API 分页配置，验证 $.default_paging_num 等于 30。 |
| B02 | 全部可执行 | 用 archivist 查看当前账号并核对 $.login 等于 "archivist"；匿名读取 Gitea 版本并确认 HTTP 200；另外查看分页配置中的 $.max_response_items 等于 50。 |
| B03 | 全部可执行 | 重复读取三次 specialtyId=2 的专科记录，每次核对 HTTP 200；三次结束之后才读取 vetId=2 的兽医，并验证 $.firstName 等于 "Helen"。 |
| B04 | 全部可执行 | 连续请求两次 Gitea 版本接口并核对 HTTP 200；完成两次之后，以 archivist 查看 owner="archivist"、repo="sealed-register"，验证 $.private 等于 true。 |
| B05 | 全部可执行 | 先获取 vetId=2 的兽医，确认 HTTP 200；再用这次响应的 $.specialties[0].id 读取对应专科，核对 $.name 等于 "radiology"。 |
| B06 | 全部可执行 | 读取 page=0、size=2 的宠物分页并验证 $.content 数量为 2；再检查已经批准的 size=0 下界负例并核对 HTTP 400；独立获取 specialtyId=2 的专科并验证 $.id 等于 2。 |
| B07 | 部分待审、独立读取可执行 | 查 specialtyId=2 的专科，验证 $.name 等于 "cardiology"；另查 ownerId=1、petId=1 的宠物，核对 $.name 等于 "Leo"。 |
| B08 | 部分待审、独立读取可执行 | 获取 vetId=2 的兽医并要求 $.firstName 等于 "Harriet"；另外读取宠物 page=0、size=2，验证 $.content 数量为 2。 |
| B09 | 部分待审、独立读取可执行 | archivist 读取当前账号并核对 $.login 等于 "archivist"；查询 API 分页配置并要求 $.max_response_items 等于 52；同时匿名读取 Gitea 版本，核对 $.version 等于 "1.26.3"。 |
| B10 | 部分待审、独立读取可执行 | 尝试读取 vetId=2 的兽医详情；独立读取 page=0、size=2 的宠物分页，核对 $.content 数量为 2；另查 specialtyId=2 并核对 $.name 等于 "surgery"。 |
| B11 | 部分待审、独立读取可执行 | 读取 vetId=2 的兽医并验证 $.firstName 等于 "Mira"；随后用该响应的 $.specialties[0].id 读取专科；与这个流程无依赖地读取 ownerId=1、petId=1 的宠物，验证 $.ownerId 等于 1。 |
| B12 | 部分待审、独立读取可执行 | 请确认 reviewer 读取 Gitea 版本时应该返回 403 还是 404；独立查询 API 分页配置，核对 $.default_paging_num 等于 30。 |

**48 个首次位置**

完成指实际检查已执行。带“断言失败”的完成位置继续保留 HTTP 500 的失败证据。

| 位置 | 冻结判定 | 目标请求 | 模型调用 | 已知 Token | 漏项/分母 | 失败或说明 |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| B01_r1_baseline | 完成 | 3 | 2 | 14057 | 0/11 |  |
| B01_r2_baseline | 未通过 | 0 | 1 | 7094 | 8/11 | FOCUS_REQUIREMENT_RESOURCE_INVALID |
| B02_r1_baseline | 未通过 | 0 | 1 | 5469 | 5/8 | MODEL_SCHEMA_TYPE_OR_CONSTRAINT |
| B02_r2_baseline | 完成 | 3 | 2 | 11090 | 0/8 |  |
| B03_r1_baseline | 完成 | 4 | 2 | 12464 | 0/14 |  |
| B03_r2_baseline | 完成 | 4 | 2 | 12295 | 0/14 |  |
| B04_r1_baseline | 完成 | 3 | 2 | 13208 | 0/10 |  |
| B04_r2_baseline | 未通过 | 0 | 2 | 13377 | 0/10 | DESIGN_REQUIRED_READ_COUNT_MISMATCH |
| B05_r1_baseline | 未通过 | 1 | 2 | 10951 | 0/8 | 合法专科读取误阻塞 |
| B05_r2_baseline | 未通过 | 1 | 2 | 11235 | 0/8 | 合法专科读取误阻塞 |
| B06_r1_baseline | 完成，断言失败 | 3 | 2 | 13590 | 0/13 | 目标500，批准400 |
| B06_r2_baseline | 完成，断言失败 | 3 | 2 | 13793 | 0/13 | 目标500，批准400 |
| B07_r1_baseline | 正确局部阻塞 | 1 | 2 | 12290 | 0/8 |  |
| B07_r2_baseline | 正确局部阻塞 | 1 | 2 | 12093 | 0/8 |  |
| B08_r1_baseline | 正确局部阻塞 | 1 | 2 | 11527 | 0/8 |  |
| B08_r2_baseline | 正确局部阻塞 | 1 | 2 | 11567 | 0/8 |  |
| B09_r1_baseline | 正确局部阻塞 | 2 | 2 | 11313 | 0/8 |  |
| B09_r2_baseline | 正确局部阻塞 | 2 | 2 | 11236 | 0/8 |  |
| B10_r1_baseline | 未通过 | 0 | 1 | 6535 | 5/11 | FOCUS_EXPLICIT_REQUIREMENT_UNACCOUNTED |
| B10_r2_baseline | 未通过 | 0 | 1 | 6475 | 5/11 | FOCUS_EXPLICIT_REQUIREMENT_UNACCOUNTED |
| B11_r1_baseline | 正确局部阻塞 | 1 | 2 | 12664 | 0/10 |  |
| B11_r2_baseline | 正确局部阻塞 | 1 | 2 | 12985 | 0/10 |  |
| B12_r1_baseline | 未通过 | 2 | 2 | 11915 | 1/5 | 任务范围越界 |
| B12_r2_baseline | 冻结记录未达，行为符合 | 1 | 2 | 11847 | 1/5 | 阻塞列表口径 |
| B01_r1_candidate | 完成 | 3 | 2 | 14813 | 0/11 |  |
| B01_r2_candidate | 完成 | 3 | 2 | 14840 | 0/11 |  |
| B02_r1_candidate | 完成 | 3 | 2 | 11059 | 0/8 |  |
| B02_r2_candidate | 完成 | 3 | 2 | 11084 | 0/8 |  |
| B03_r1_candidate | 完成 | 4 | 2 | 13408 | 0/14 |  |
| B03_r2_candidate | 完成 | 4 | 2 | 13230 | 0/14 |  |
| B04_r1_candidate | 未通过 | 0 | 1 | 7338 | 7/10 | FOCUS_REQUIREMENT_RESPONSE_INVALID |
| B04_r2_candidate | 未通过 | 0 | 2 | 13860 | 0/10 | DESIGN_REQUIRED_READ_COUNT_MISMATCH |
| B05_r1_candidate | 未通过 | 1 | 2 | 11625 | 0/8 | 合法专科读取误阻塞 |
| B05_r2_candidate | 未通过 | 1 | 2 | 11674 | 0/8 | 合法专科读取误阻塞 |
| B06_r1_candidate | 完成，断言失败 | 3 | 2 | 14018 | 0/13 | 目标500，批准400 |
| B06_r2_candidate | 完成，断言失败 | 3 | 2 | 14098 | 0/13 | 目标500，批准400 |
| B07_r1_candidate | 正确局部阻塞 | 1 | 2 | 13036 | 0/8 |  |
| B07_r2_candidate | 正确局部阻塞 | 1 | 2 | 12727 | 0/8 |  |
| B08_r1_candidate | 正确局部阻塞 | 1 | 2 | 12069 | 0/8 |  |
| B08_r2_candidate | 正确局部阻塞 | 1 | 2 | 12108 | 0/8 |  |
| B09_r1_candidate | 正确局部阻塞 | 2 | 2 | 11266 | 0/8 |  |
| B09_r2_candidate | 正确局部阻塞 | 2 | 2 | 11257 | 0/8 |  |
| B10_r1_candidate | 未通过 | 0 | 1 | 6976 | 5/11 | FOCUS_EXPLICIT_REQUIREMENT_UNACCOUNTED |
| B10_r2_candidate | 未通过 | 0 | 1 | 7171 | 5/11 | FOCUS_EXPLICIT_REQUIREMENT_UNACCOUNTED |
| B11_r1_candidate | 正确局部阻塞 | 1 | 2 | 14281 | 0/10 |  |
| B11_r2_candidate | 正确局部阻塞 | 1 | 2 | 14391 | 0/10 |  |
| B12_r1_candidate | 未通过 | 0 | 1 | 6357 | 1/5 | DESIGN_REQUIRED_IDENTITY_NOT_APPROVED |
| B12_r2_candidate | 未通过 | 2 | 2 | 11917 | 1/5 | 任务范围越界 |
