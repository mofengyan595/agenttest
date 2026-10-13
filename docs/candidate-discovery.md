# OpenAPI 测试候选发现 MVP

这是确定性、离线的测试机会发现模块。**候选测试 ≠ 已批准测试 ≠ 已执行测试 ≠ 已验证缺陷。**
不调用 LLM、HTTP、DNS、Executor.run、凭据环境或业务数据库；不输出 Scenario，不修改断言，不绕过 Intent Validator。所需依赖全部来自现有 Python 基础环境。

## 使用与真实输出

从仓库根目录运行，输出文件必须尚不存在：

```powershell
.\.venv\Scripts\python.exe -m agenttest discover `
  --contract tests/fixtures/discovery/openapi-3.1.yaml `
  --policies tests/fixtures/discovery/policies.json `
  --target tests/fixtures/discovery/target.json `
  --exclude-operation excludedOp `
  --output runs/discovery/example.discovery.json
```

Linux 将解释器替换为 `.venv/bin/python`，使用同样参数（可写成单行）。无需启动服务或设置 API Key。`--target` 可省略，此时无法确认目标执行前提，正常候选增加 `TARGET_CONFIGURATION_REQUIRED`；`--business-scenario` 在无 target 时选择作用域，有 target 时以它的 business_scenario 为准。`--exclude-operation` 可重复。

本轮同一命令实际运行（输出路径使用 `final-fixture.discovery.json`）：

```json
{"format_version": "agenttest.discovery.v1", "candidates": 19, "operation_coverage": {"numerator": 1, "denominator": 2}, "scenario_coverage": {"numerator": 8, "denominator": 9}, "needs_review": 19, "tests_executed": 0, "http_requests": 0, "model_calls": 0}
```

结构化文件包含 7 个正常候选、11 个静态负例、1 个不支持 Schema 的待审项。7 项通过静态预检，仍全部 NEEDS_REVIEW。以下是实际正常候选中的字段摘录：

```json
{
  "operation_id": "listResources",
  "operation_ids": ["listResources"],
  "method": "GET",
  "path": "/resources",
  "scenario_kind": "normal",
  "polarity": "normal",
  "policy_refs": ["list_v1"],
  "schema_valid": true,
  "policy_match": true,
  "review_status": "NEEDS_REVIEW",
  "executable_status": "STATIC_VALIDATED",
  "missing_evidence": []
}
```

输出使用排他创建与 `.discovery.json` 后缀；已有文件、`report.json`、历史评测文件均不覆盖。失败只输出固定错误分类，不打印输入、凭据或异常正文。文件包含完整候选、覆盖摘要、缺失依据、输入摘要与算法版本，不包含原始 OpenAPI/Policy 内容。

## 模块与算法

| 文件 | 职责 |
| --- | --- |
| `src/agenttest/discovery_models.py` | 严格 Pydantic 候选、输入变化、依赖和覆盖模型；不兼容可执行 DSL |
| `src/agenttest/discovery_schema.py` | 复用 request_schemas / supported_schema / validate_value；有限值搜索与边界描述 |
| `src/agenttest/discovery.py` | 作用域筛选、资源关系遍历、Registry.resolve、原 DSL 静态预检、ID 与覆盖统计 |
| `src/agenttest/__main__.py` | discover 子命令，有界文件读取与排他输出 |
| `src/agenttest/policies.py` | 新增 definitions() 副本读取；不改变批准/resolve 逻辑 |
| `tests/test_discovery.py`、`tests/fixtures/discovery/` | 独立合成回归与 3.0/3.1 契约、策略及只读目标；未修改冻结样本 |

1. 契约走既有 load_openapi：1 MB 上限、唯一键、引用展开预算与完整文档校验；仅支持无环本地引用，远程引用直接拒绝，绝不下载。策略必须由操作员通过独立 `--policies` 文件选择，不能由契约、模型或浏览器提交批准标志。
2. 按 operationId 与策略作用域建立索引，显式排除项单列。读取 Schema 支持范围，未知组合/格式等输出 review 记录。
3. 从固定合成值、enum 成员和声明边界有限搜索正常输入，并同时验证契约与 policy literal_schema。忽略 example/default/description 内容。只纳入必填字段、策略要求字段或当前变化字段，避免向 closed_arguments 策略增加可选参数。
4. 分别输出正常边界与静态负例。无交集、边界违反策略、无资源来源等保留为 review/BLOCKED；不编造可用的正常请求。负例不给 Policy 引用或正常断言，不推断 400/422。
5. 只有 binding_rules 的 source_policy_id、source_selector、role_relation 形成资源边。默认 `$` 在标量资源绑定中不足以证明 selector，标 MISSING_RESOURCE_SELECTOR。按依赖优先深度遍历，检测循环、缺失策略、缺角色和配置排除；再由 Registry.resolve 核验所有步骤。角色只来自批准策略与配置。
6. 需要认证时使用配置中的 login_operation 与同角色批准登录策略，输出登录前置关系；跨角色资源流分别登录。认证正文由原 Executor 管理，发现器既不生成也不读取用户名、密码、JWT。没有真实凭据时原 validate_plan 以空环境停在认证门禁，记录缺口；不使用假凭据让预检通过。
7. candidate_id 是输入摘要、算法版本与固定候选记录的 SHA-256；稳定排序、去重，无时间戳、随机 ID 或机器路径。同一输入产生相同 JSON 字节。输入摘要绑定解析后的 ContractSummary、Registry、目标配置、作用域与排除项，供复核来源；摘要不是人工批准证明。

`schema_basis` 使用 **ContractSummary 的 JSON Pointer**（如 `#/operations/2/parameters/0/schema/exclusiveMinimum`），不冒充原始文件行号。依据与 `contract_sha256` 配合定位已展开的契约。输入变化只公开合成数值/布尔、字符串长度、enum 索引及操作描述；不公开 enum 原文、默认值、示例、策略断言或业务请求/响应。

## 状态含义

| 字段/状态 | 含义 |
| --- | --- |
| polarity=normal | 已找到符合受支持约束及适用绑定规则的静态草案；仍可能缺目标、认证或动态资源值 |
| polarity=negative | 描述有意违反约束的输入变化，仅静态发现 |
| polarity=review | 未支持或未找到可信正常草案；不作为已发现的有效场景计数 |
| schema_valid=true | 实际合成的普通输入通过原请求 Schema 校验 |
| schema_valid=null | 有动态资源、执行器管理的认证值，或无法确认；不表示通过 |
| policy_match=true | 草案用既有 Registry.resolve 匹配批准策略；不授予候选新的权限 |
| STATIC_VALIDATED | 普通输入、策略、目标/DSL 静态预检通过；没有运行测试、认证业务结果或验证自然语言意图 |
| BLOCKED | 缺目标/凭据/资源实际值等前提，或原门禁拒绝；查看 missing_evidence/rejection_reason |
| STATIC_ONLY_NEGATIVE | 原请求校验必须继续拒绝，当前没有负例执行策略 |
| review_status=NEEDS_REVIEW | 所有候选固定待审，不自动批准，不产出 PASS/BUG |

## 静态覆盖口径

- **Operation**：分母为具有 operationId 且未被显式排除的契约操作，包括未支持项；分子为至少有一个 normal/negative 静态候选的操作。缺 operationId、显式排除、不支持和无候选分别列出。示例有 4 个操作：1 个无 ID、1 个排除，纳入 2 个，其中 1 个未知 uuid format，所以为 **1/2**。
- **Scenario**：先显式列出 Operation × Scenario Kind slots。每个操作有 normal，依据受支持约束增加 required/enum/上下界/长度/类型，依据策略增加 role_scope/resource_flow；unsupported_schema 是诊断项，不给分母加一个容易覆盖的奖励项。每个 slot 最多计一次，review 不计。示例 listResources 的 8 种类型 + unsupportedOp 的 normal 共 9，发现 8，故为 **8/9**。这不代表全部参数组合、边界点或真实业务场景已覆盖。
- **Policy**：逐条报告 in_contract、in_scope、trusted_assertions、has_candidate、static_preflight_met，不能由“存在策略文件”推断可执行。未审阅/空来源、契约外策略、错误作用域均保留诊断。
- **Fault Detection**：Registry/契约没有六类机制的统一可信映射，返回 NOT_MAPPED；静态涉及、策略批准、实际执行、实际检出四个列表分别为空。实际 tests_executed=0，不计算缺陷检出率，不套用已有历史评测成绩。

分母为 0 时保留 0/0，不显示 100%。操作覆盖与场景覆盖都不是已执行覆盖率。

审查整改后额外提供 `operation.with_normal_candidates`（至少有 normal 草案）、`static_preflight`（至少一项 STATIC_VALIDATED）、`trusted_policy`（纳入作用域且有已审阅策略）、`execution`（本模块恒 0）四个 numerator/denominator，分母均沿用 included operations。原 `ratio` 的正常+静态负例口径不变；旧 `.discovery.json` 缺细分字段时解析为未知，不回填历史成绩。

本轮补查确认：可选 requestBody 可以完全省略；策略或变体选中 body 后才要求内部必填字段。无有界 witness、enum/约束冲突、矛盾边界均不能 STATIC_VALIDATED；非法约束类型/值安全进入 NEEDS_REVIEW。数值上下界同时声明 inclusive/exclusive 时，schema_basis 同时保留现有两项依据，并对规范化摘要实际逐条解引用。多条来源策略仍严格引用指定 source_policy_id，不自动换策略。来自 Policy enum、没有契约 enum 索引的数字/布尔不再输出字面量，以 approved_enum_shape 描述，原 witness/审批结果不变。完整逐项证据与平台限制见[审查整改](review-remediation.md)。

## 支持范围与预算

支持标量 string/integer/number/boolean、required 参数/JSON body/顶层字段、enum、minimum/maximum、exclusiveMinimum/exclusiveMaximum、minLength/maxLength，以及批准资源关系的简单流程。JSON body 限顶层标量；必填 header/cookie、复杂编码和 multipart 继续待审，optional header/cookie 沿用原校验器的忽略行为。

3.0 的 exclusive 边界是布尔修饰，3.1 是数值边界；依据契约版本校验，不互相猜测。3.1 nullable 及联合类型不在本子集内。参考 [OpenAPI 3.0 Schema](https://spec.openapis.org/oas/v3.0.3.html#schema-object)、[OpenAPI 3.1 Schema](https://spec.openapis.org/oas/v3.1.0.html#schema-object)。策略 literal_schema 沿用原 Registry 约定，不擅自按契约版本重写。

oneOf/anyOf/allOf、pattern、未知 format、开放对象、嵌套请求值、数组输入、CSV、异步场景发现、response links/business permissions 推断均未实现。远程引用直接拒绝。已有 enum 会按顺序取首尾边界，不复制原文；整数边界用 ceil/floor，浮点排他边界使用 nextafter，并重新交给原校验器。

预算：200 个操作、500 条策略、每操作 100 个字段、2,000 个去重候选、依赖深度 20/构建步骤 100，受目标 max_steps 再限制。合成字符串最多 256 字符；搜索不到有界 witness 就待审，不能声称 Schema 本身不可满足。超总预算失败，不返回看似完整的截断覆盖率。

## 验证与后续接点

离线测试覆盖两个契约版本、正常/负例、枚举/数值/长度/类型、未知 Schema/Policy/selector、认证顺序/跨角色/循环、闭合参数、输入稳定性/去重/摘要、分母、脱敏、输出不覆盖、原门禁仍拒绝负例。验收命令与实际结果见 [进度](progress.md)。本机默认测试的网络保护始终保留。

发现模块不增加 Web API 或候选页面：把本地策略文件变成浏览器可传策略，会改变当前文件权限作为信任根的设计。后续可提供**服务器预先批准候选产物的只读查询**，仍不能执行。发现模块没有 FastAPI Schema；本轮 ReportStore 新增可选 revision 的 API Schema 与 TS 类型已另行重新生成。

- **Planner**：默认 planning_context 不追加候选；Eval v2 与独立开发 A/B 已有显式 B 组，通过脱敏筛选追加正常候选，仅建议计划，仍经过原 DSL/Policy/Intent 门禁。没有启用默认增强。
- **Agent Eval**：另建、审阅并冻结新的 C 系列候选集；本轮 fixture 是开发回归，不作为独立泛化成绩或修改旧 Golden 标签。
- **Web**：未来展示稳定候选 ID、依据、NEEDS_REVIEW 与静态分母，不能把 STATIC_VALIDATED 显示为执行 PASS。
- **Observability / RAG**：后续独立 RAG 离线评估已作出 No-Go，默认保持 No-RAG，见[技术决策](project-technical-summary.md#rag-技术选型记录no-go)。候选和检索文本均不能授予业务权限；当前不增加服务依赖或新实验。

## 后续开发 A/B 与默认策略（2026-10-13）

两个已使用的 AI 开发样本在 A/B 均为 2/2 端到端成功；四次真实调用合计 9,861 Token。A 输入/输出/总量为 3,109/721/3,830，B 为 5,291/740/6,031；B 未观察到质量收益，用量增加。每组两条、固定 A→B 顺序、Prompt 已明确提示路径，不能据此计算显著性或广泛泛化。[脱敏统计副本](demo-evidence/candidate-ab-summary.json)、[开发协议](development-ab.md)。

私有离线审计确认：B 预组合的信息在 A 的 Policy/binding_rules/authentication 中已有；存在登录/依赖重复，且公开投影缺少逐步骤角色对应，反向角色路径可能显示相同。旧执行门禁仍有效，未发现越权执行。压缩原型在 36 个开发上下文中减少 23.64% 字节，58 个上下文可恢复，144 行 Fake/Mock 对照结果一致；没有新真实调用，也没有证明实际 Token 节省或模型质量改善。

因此当前 mini 维持 A 默认，B 仅显式实验；原型留在私有研究档案，不接入生产。新增 batch 的有类型规划与可信执行不代表 Discovery 已支持 CSV/异步候选。完整取舍见[项目说明](project-technical-summary.md)与[工程审查](engineering-review-20261013.md)。历史数据和正式门禁均保留。
