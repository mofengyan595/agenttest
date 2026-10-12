# 审查整改与 Candidate Discovery 门禁（2026-10-12）

审查基线与开始时 HEAD 均为 `9e988d51bd34d7fabb00785f4737f4c5fcb0be93`，初始工作区干净。
先读取 AGENTS、CODEX_TASK、README、candidate-discovery、engineering-upgrade、web-console、progress 及调用链。
本轮仅在 AgentTest 修改文件，整改验收阶段未创建分支、commit、push、调用收费模型或向真实 Java 服务发请求。后续用户已明确授权提交与推送，实际状态以 Git 记录为准。

## 逐项核验

表中位置均为**审查基线**行号，整改后的实现见下一节。不把本地操作员控制目录的风险称为已证明的远程任意写入。

| 项目 / 状态 | 文件与行号 | 触发条件与最小证据 | 实际影响 / 修复必要性 | 兼容性影响 |
| --- | --- | --- | --- | --- |
| 同 ID 发布：确认，已修复 | report.py:148–165 | 两个 save 共用 report.tmp；控制写入/替换交错实测 `saved / FileNotFoundError`，第一个保存者发布了第二个调用者的内容；完成后再保存旧副本实测 completed 退回 false | 临时覆盖、发布错误和静默状态回退，必须修复 | 新增 CAS revision，冲突明确 ValueError；独立写者须先 read 最新版本，不能合并/重跑 HTTP |
| 写入路径：确认，已修复 | report.py:146–155 | 在 Run ID 目录预放 Windows junction，基线实测在 outside/report.json 写入成功；read 的防护没有用于 save | 本地目录被预置链接时可写出根目录；可信操作员根降低攻击面，但不消除误配置风险 | 拒绝根、祖先、run、文件的链接/reparse/hardlink；以前使用链接报告目录的配置需要改为真实目录 |
| Run ID：确认，已修复 | report.py:149、168–169 | `12345678-1234-1234-1234-123456789abc` 保存成功、读取失败 | 写读合同不一致，可产生无法通过公开 API 读取的目录 | 两端均要求 32 位小写 hex；旧标准 ID 报告缺 revision 仍读取为版本 0，不迁移历史；非规范 UUID 从未属于读取 API 支持范围 |
| E2E 分页：确认，已修复 | console.spec.ts:39、49；History.tsx:134–152 | 隔离 13 份报告，旧断言实测 `Expected:13 / Received:12` | 少于 13 条的夹具掩盖测试错误，必须补强 | History 原有每页 12 条、筛选重置已正确，无需改页面或重新搭框架 |
| 本地 API：确认需要加固，已修复 | main.py:14–22 | 基线没有 Host/Origin/peer 校验；仅没有 CORS 头不能阻止简单 POST 导入和解析负载 | 非预期 Host、跨站浏览器请求和误暴露风险；本服务不是生产认证系统 | 新增小型本地门禁；默认 Vite 5173/preview 4173/API 8000/E2E 18000、TestClient、CLI 均保留；其他 Origin 要明确配置 |
| 历史数值：确认，已修复 | report_queries.py:37–48 | 合成旧报告 `equals $.phone actual=13800138000` 实测原样公开 | Pydantic 类型不是公开授权；旧/非标准数值须二次脱敏 | HTTP 状态码继续显示；数值统计需独立可信 Registry 与策略摘要/字段匹配，无足够依据的历史值隐藏，不伪造批准 |
| exclusive 语义：已实现，未重复开发 | discovery_schema.py:16–57；request_validation.py:84–104 | 3.0 布尔与 3.1 数值边界；整数有效边界、无效等值及错误方言均有回归 | 未发现正常候选被错误 STATIC_VALIDATED | 保留原算法与 Executor 门禁 |
| enum 冲突、无 witness、矛盾上下界：已实现，补回归 | discovery_schema.py:60–96；discovery.py:285–321 | enum 与 minimum 无交集、上下界相反、排他等值、超有界字符串都不会 STATIC_VALIDATED | 不能把搜索失败称为 Schema 必然不可满足；原保守行为正确 | 未改预期或扩展 Schema；静态负例仍可能纳入原静态覆盖 |
| 非法约束值：确认，已修复 | request_validation.py:17–48 | 直接 ContractSummary / operator literal_schema 中 minimum 字符串、非法长度或空 enum 未被 supported_schema 拒绝，可触发 TypeError/IndexError | CLI/程序化可信配置边界应给 NEEDS_REVIEW，不能崩溃；公共 OpenAPI 导入原已校验合法文档，不夸大为远程漏洞 | 只收紧非法或无法处理的非有限/超浮点范围约束；不新增 Schema 支持 |
| 可选 requestBody：确认，已修复 | discovery.py:160–167、246–256；discovery_schema.py:147–161 | body 可省略，内部 value 必填；closed_arguments 无 body 策略在基线返回 BLOCKED / POLICY_ARGUMENT_MISMATCH | 错把 body 内 required 当成必须发送整个 body，漏掉真实有效候选 | 无 body 草案按 media=None 校验；策略/变体选中 body 时继续包含必填字段；空对象不可由 DSL 表达时保守阻塞 |
| 可选参数与 closed_arguments：已修复/已实现，跳过重写 | discovery.py:165–167；test_discovery.py 原回归 | 未选中的可选参数省略；被选中的边界还须通过闭合策略 | 未发现回归 | 保持现有实现 |
| 角色、认证、可信源、环、多源：已实现，补回归 | discovery.py:185–222、256–317；policies.py:75–113 | 未审阅 source、未知 source、缺角色、环与预算均阻塞；多策略遵循精确 source_policy_id，登录按已批准元数据顺序 | 没有凭据/资源实际值不会通过静态预检，也不伪造运行结果 | 不增加来源推断或搜索引擎；多个可选登录策略按稳定排序取首项，失败不会自动换策略 |
| 覆盖率“算执行”：误报，静态口径正确 | discovery.py:486–510；candidate-discovery.md | 分子来自非 review 的 normal/negative，分母为纳入操作及显式 slots；矛盾 Schema 可只有负例覆盖 | 文档本来已说明静态发现；不能改历史分母或套用缺陷成绩 | 保留 ratio，新增正常/静态预检/可信策略/执行四项；执行恒 0，旧产物缺细分时保持未知 |
| schema_basis：部分确认，已修复 | discovery.py:416–426 | 同时有 minimum=2、exclusiveMinimum=1 时实际边界由 minimum 决定，原定位仅保留 exclusiveMinimum | 约束证据不完整；不是原始 OpenAPI 文件坐标 | 同时列出存在的 inclusive/exclusive 指针；根据规范化摘要与 contract_sha256 实际逐条解引用验证 |
| Policy enum 输出：确认，已修复 | discovery_schema.py:163–176；discovery.py:240 | 契约为普通 integer，Policy literal_schema enum=[246810]；实测 numeric_value 输出该合成私有字面量，因为原 describe 只识别契约 enum | 与不公开 enum 原文的约定不一致，可能公开操作员策略中的数字字面量 | 不改 witness/策略校验，仅将该输出改为 approved_enum_shape，数值隐藏；契约 enum 继续只给索引 |
| 不支持 Schema、无 ID、排除项、零执行：已实现，补核验 | discovery.py:325–356、486–563；tests/test_discovery.py | 已有真实 3.0/3.1 fixtures；缺 ID、显式排除、未知格式、零分母、稳定 JSON、输出排他创建、Executor/HTTP/Vault/environment 阻断测试 | 未发现绕过 Executor 或输出执行授权的证据 | 不重复实现发现器、不新增 Web 入口、不修改冻结样本 |
| 完整 OAuth/JWT、多用户平台：不适用 | main.py；web-console.md | 工具仅本地只读查询与无持久化契约导入，无业务执行入口 | 当前范围内增加完整认证平台没有必要 | 明确禁止公网/共享/无认证反向代理部署 |

## 修改与关键回归

- `report.py`、`report_files.py`：单 Run ID 非阻塞 OS 写锁（POSIX flock / Windows byte-range lock）、持久锁文件、版本比较、独占唯一临时文件、flush/fsync 后原子替换、原 Windows 5/32/33 有界重试。失败保留旧 JSON 和调用者版本，正常失败清理本次 temp；进程崩溃的孤立 temp 保留但永不发布。
- POSIX 从根逐级 `O_DIRECTORY|O_NOFOLLOW` 打开，读写/替换使用同一目录 fd；Windows 逐级 `CreateFileW` 打开 reparse 自身、拒绝 reparse、持有不共享 DELETE 的祖先/目录句柄，独占创建临时文件。文件要求普通文件且单 hardlink。目录被竞争重命名时不能把操作重定向到攻击者链接目标。
- `readonly_runtime.py` 仅两处语义适配：revision 是发布元数据，不纳入不可变业务摘要，也不随原业务快照恢复；原断言、权限、调查预算、HTTP 和结果不变。第一次集成测试实际暴露版本回退，修复后原只读调查、异常/取消/报告失败不重放测试保持通过。
- `main.py`、`local_access.py`：一个中间件完成精确 Host（含 IPv6）、回环 peer、Origin、cross-site Fetch Metadata 检查，无须叠加第二套 TrustedHost。没有 CORS 放行或登录系统。
- `report_queries.py`：默认隐藏历史数值；匹配操作员独立加载的 RegisteredPolicy 摘要、operation、reviewed/source、public_evidence 与字段断言后，才公开有限数值。敏感 selector 优先隐藏。报告自报 `public_numeric`、仅有可解析类型/摘要不能授权。HTTP 状态要求无 selector、合法码列表及 100–599 actual。
- `discovery.py` / `discovery_schema.py` / `discovery_models.py` / `request_validation.py`：可选正文省略语义、非法约束安全拒绝、完整边界依据、覆盖细分、Policy enum 数字/布尔字面量仅给形状。旧发现产物可解析，新增字段缺失为 null/未知；candidate_id 仍绑定原摘要与完整候选证据，依据/输出改变的候选 ID 随之改变。
- `serve_console_e2e.py` / `console.spec.ts`：复用原临时目录与 showcase，13 条有效 + 1 条坏 JSON + 1 条缺文件，其中一条旧报告缺四态元数据。检查 12/1 分页、前后按钮、筛选从第二页重置、空筛选、桌面/移动相同 ID 和顺序、缺报告/非法 ID。截图仍只在忽略的 `web/test-results/screenshots/`。
- `.github/workflows/web.yml`：保留独立 Web CI，增加 Windows/Linux 托管 Chromium E2E；原 Python Windows/Linux × base/agent 矩阵保留。API schema 和 TS 仅增加可选 revision，未改前端业务页面。

回归包含线程/多进程同 ID 与不同 ID、旧版本拒绝、发布失败完整性、真实 Windows reader sharing lock、5/32/33 重试、进程 os._exit 后锁释放和孤立文件、非法 ID、旧报告无 revision、根/祖先/run/report/lock/旧 temp/唯一 temp 链接、Windows 无需 symlink 权限的 junction、目录竞争替换、hardlink、POSIX FIFO。

## 修复前最小复现

本轮实际运行了基线代码，而非相信旧审查结论：

```text
stale_overwrites_completed: True
legacy_numeric_exposed: 13800138000              # 明确合成测试值
save_accepts_read_rejects_hyphen_id: True
baseline_controlled_same_id_race: ['saved', 'FileNotFoundError']
old_writer_published_new_snapshot: True
baseline_preplanted_junction_writes_outside: True
baseline_optional_body: BLOCKED POLICY_ARGUMENT_MISMATCH
operator_enum_literal_exported: 246810          # 明确合成私有 enum 测试值
Playwright 旧断言：Expected 13 / Received 12 / 1 failed
```

同 ID 静态最小复现：复制同一 report 为 old；将 report.completed 设 true 并 save；再 save(old)。基线回退为 false，修复后第二次抛版本冲突。并发实验控制两份 `report.tmp` 写完后先由旧调用者 replace、再由新调用者 replace；不涉及 Executor/HTTP。
链接实验仅在自建临时目录内预置 junction。分页旧断言仅保存在忽略的测试产物中，正式 E2E 不保留预期失败用例。

## 验收记录

最终命令输出与平台差异汇总在 [progress.md 最新检查点](progress.md)。本轮已实际执行修复后 Windows 全量 pytest、Ruff、pip check、all showcase、真实回环 smoke、前端 typecheck/Vitest/build/Edge E2E；后续新增纯文件组件测试也纳入最终全量复验。

Linux Ubuntu 24.04 / Python 3.12.3 原生 `/tmp` 的无第三方依赖文件组件验收 `7 tests / OK`：独占创建、原子发布、同 ID 锁冲突/异常释放、不同 ID、硬链接、预置链接、目录替换、FIFO 拒绝。
首次直接在 WSL `/mnt/d`（DrvFS）测试时 FIFO 不支持、目录被重命名后的 openat 返回 ENXIO，安全失败且没有写到外部目标；改用原生临时文件系统后全部通过。报告根应放普通本地文件系统，不承诺 DrvFS、网络盘、同步盘的完整锁/持久化语义。

独立 Windows Base 另在忽略目录用现有缓存和项目 Python 自带 wheels 离线安装原 requirements-dev.lock，确认没有 langgraph/mcp：**585 passed、29 skipped、1 warning**。16 项为可选功能缺失，余 13 为权限/平台专项；警告为既有 Starlette TestClient 弃用提醒。Base 的 Ruff、pip check、正常/故障 showcase 也通过，主 Agent 环境与依赖锁均未修改。

完整 Linux base 环境安装尝试失败：PyPI 读取超时/连接重置，最终锁定 `anyio==4.15.1` 无法解析；Windows 缓存缺 Linux 原生二进制包。没有降级、修改锁文件或安装全局包。**完整 Linux pytest/Agent/E2E、修改后的远端 Actions 尚未验证**；不能用组件验收或旧 Actions 成绩替代。npm 依赖已存在且锁文件未改，本轮复用现有安装，npm ci 无需重复，不列作已执行。

## 剩余边界与下一准确里程碑

1. 目录信任根仍是本地操作员及 OS 权限，不抵抗管理员、裸盘写入或恶意修改同一报告的已批准策略。锁为协作单写者规则，冲突不自动重试整个运行；永久发布错误需操作员处理文件权限/占用，不能重发业务请求。fsync 临时文件保障完整文件内容，不承诺断电后的目录项持久化。
2. 只允许回环启动并禁用代理头：`uvicorn agenttest.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers`。应用检查 peer 不能代替服务器 socket 绑定、防火墙或生产认证；禁止公网/共享/SSH 隧道无认证暴露及无认证反向代理。非默认本地 Origin 使用 create_app(allowed_origins=...) 显式提供完整集合。
3. 想保留公开统计字段时，启动前显式设置 `AGENTTEST_PUBLIC_POLICIES` 为操作员审阅的 Registry JSON 路径；只用本地可信文件，API 不接受策略授权。动态实例化策略摘要无法复核时仍隐藏；旧报告的不可复核公开资格不会被恢复。
4. STATIC_VALIDATED 只是静态预检；review_status 永为 NEEDS_REVIEW。负例永为 STATIC_ONLY_NEGATIVE，无执行授权；不扩展 Executor 负例能力，不将候选数称为缺陷检出率。本轮 discovery 的 HTTP/模型/执行仍为 0。
5. Agent Eval v2 的代码整改前置问题已处理，Windows Base/Agent 已实测；**完整跨平台发布门禁尚欠 Linux Base/Agent、Linux Chromium 与远端 CI**。下一步先在依赖可获得环境验证现有矩阵与 E2E，再另立、人工标注并冻结 C 系列任务及 requested/attempted 分母；本轮回归不能作独立泛化成绩。未开始 Eval v2、RAG、多 Agent、Web 执行或真实模型调查。

实现参考：[Python os 的目录相对 I/O](https://docs.python.org/3/library/os.html)、[Windows CreateFile 的 reparse 与共享模式](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)、[Starlette Host 防护](https://www.starlette.io/middleware/)。
