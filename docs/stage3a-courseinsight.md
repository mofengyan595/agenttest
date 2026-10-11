# 阶段 3A：CourseInsight 三步适配

范围：人工契约、固定 DSL、操作员目标配置与可信策略，复用原 Executor / Verifier /
CredentialVault。关闭 LLM、调查、MCP、跨用户测试。默认目标禁止写入。

## approved-expectations

经用户批准的独立本地 demo 链路：

| 操作 ID | 实际接口 | HTTP | 可信断言 / 绑定 |
| --- | --- | --- | --- |
| ciLogin | POST /api/auth/login | 200 | code=0、ADMIN、Bearer；Vault 读取 $.data.accessToken |
| ciCreateCourse | POST /api/courses | 201 | code=0；$.data 是动态课程 ID |
| ciGetCourse | GET /api/courses/{id} | 200 | code=0；$.data.id 等于本次创建 ID，code/name/teacherName 等于创建输入 |

创建使用 `AT3A` + 24 位随机十六进制代码（28 个 ASCII 字符），其余是固定合成文本。
Registry 在每个 run 将整个创建输入固定为操作员样本；查询只接受同角色、同 run、
`ci_create_course_v1` 的 `$.data`。不修改预期状态码、不重试写操作、不补造 ID。
登录与创建各一次、查询一次，总请求上限 3。未知策略与静态 ID 在 HTTP 前拒绝。

源码核对：本地 CourseInsight `430c23eec8bc31023d8fb43d767bf9d8de164591` 的
AuthController、CourseController、SecurityConfig、ApiResponse、UserLoginResponse、
CourseCreateRequest、CourseDetailResponse、CourseService、application-demo.yaml、compose.demo.yaml
及 scripts/demo.ps1；远端 HEAD `d69023a30693f8142c0432182ace3706bc8bee19`。
本地版本领先远端，相关三步接口已重新读取；CourseInsight 工作区已有 .gitignore 修改，保留。
没有 springdoc，契约是人工整理的三操作子集，不代表全部 Java API。

## 校验支持边界

- 支持字段名、基本类型、required、长度上限，以及 path id 的正数/int64 范围。
- Java `@NotBlank` 的 Unicode 空白语义未实现；`minLength: 1` 不是完整替代。
  本轮只批准固定非空 ASCII 样本，不开放任意创建参数。密码来自本地 demo 配置，保留 Java 校验。
- Java `@Size` 使用 UTF-16 单元，Python 长度按码点；本轮 ASCII 输入不存在差异。
- 请求 description 可省略或 null，但本轮固定为合成字符串。复杂 Schema / pattern
  仍按原验证器进入 NEEDS_REVIEW，不静默忽略。
- 响应 OpenAPI Schema 用于文档；现有 Verifier 按批准字段断言，未提供通用全响应 Schema 验证。
  创建返回值在绑定到 GET 参数时再次验证正整数/int64，错误类型不得发送查询。
- GET 课程详情没有归属权限限制且会访问缓存；不沿用 mini target 的跨用户 403
  预期，也不将它自动加入只读调查能力目录。

## 证据与清理

运行保留 `runs/<run_id>/report.json`、仅合成数据的 `fixture.json`，包含契约、计划、
策略摘要、动态依赖和请求 attempt ID。报告不记录 JWT、凭据、HTTP 正文或实际 ID。
按 fixture 的一次性 code 追溯课程；不自动删除课程、数据库或卷，不自动恢复旧 run。
真实 Java 验收及隔离检查结果在完成环境门禁后追加，离线 MockTransport 通过不代表真实验收。

## 环境准备与运行

本轮 CourseInsight 所有接入路径统一为 `D:\Desktop\project\courseinsight-cloud`。
已有 demo 容器保留了迁移前的 Compose 路径；用户明确批准重新创建容器。按当前
`demo.env` / `compose.demo.yaml` 重新创建五个依赖及未启动的卷初始化容器，未删除、
重建数据卷；五个卷的 Name/CreatedAt/Driver/Mountpoint/Options/Scope/Labels 前后完全一致。

运行需要现有 Python 3.11 .venv、JDK 17、Docker Engine、健康的原 demo 依赖及完整本地
Maven 缓存。使用 CourseInsight 自带脚本准备依赖，不运行 `clean` 或完整 `smoke`：

```powershell
# 在 CourseInsight 根目录；首次启动前须确认 demo 专用数据库与卷
.\scripts\demo.ps1 start

# 在 AgentTest 根目录；默认仅检查，零业务 HTTP、不启动 Java
.\.venv\Scripts\python.exe scripts/run_courseinsight_http.py `
  --source "D:\Desktop\project\courseinsight-cloud"

# 显式单次验收：启动自有 Java demo 进程，执行三个业务请求，最后关闭自有进程
.\.venv\Scripts\python.exe scripts/run_courseinsight_http.py `
  --source "D:\Desktop\project\courseinsight-cloud" --execute
```

不得提前另开 Java 占用 18080：验收脚本拒绝接管未知监听进程。
Java 在 `courseinsight-server` 工作目录以 Maven **离线模式**启动，demo profile 仍可导入
`../demo.env`；外部参数将地址/端口固定为 127.0.0.1:18080，与 DemoSmoke 的
`DEMO_BASE_URL` 覆盖机制兼容。固定 MySQL 的 courseinsight_demo、Redis 6380、MQ 9876
和 AI Stub 8002，关闭 outbox 发布与租约恢复后台任务，避免本轮无关后台处理。
不执行完整 DemoSmoke（其中包含额外写入与异步操作）。

凭据仅在内存中从当前 demo.env 读取，经 environ 交给 Vault；AgentTest 不复制此文件。
不读取学校模型配置，不继承模型 Key、Spring/Java/Maven 环境覆盖或代理配置。
Java 输出不保存，避免异常日志泄露配置；如启动失败，在操作员本地诊断 Maven 缓存与配置，
不自动联网安装、不重试业务写入。Maven 可产生 CourseInsight 忽略的 target 构建产物，
业务源码与跟踪文件不改动，原有 .gitignore 修改保留。

门禁检查：固定源码版本与已审查文件摘要、相关源码工作区无漂移、demo 专用容器/卷标签、
全回环端口、五个健康状态、数据库名和必要凭据匹配、卷不被其他容器共享、Compose 来源目录。
配置或源码改变须重新人工核对，不通过编辑断言绕过。版本/文件摘要只支持已核对的本地
`430c23e...`；不是对任意 CourseInsight 版本的通用兼容承诺。

| 固定预检错误码 | 操作员处理 |
| --- | --- |
| INSPECTION_FAILED / DOCKER_UNAVAILABLE | 启动 Docker 并确认当前用户有权限；不发送业务请求 |
| DEMO_COMPOSE_SOURCE_MISMATCH | 从当前目录重新配置 demo 容器，保留全部卷；不要直接改标签充当证明 |
| DEMO_VOLUME_SCOPE / DEMO_VOLUME_SHARED / DEMO_DATABASE_SCOPE | 核实独立 demo 卷、数据库和凭据，停止写入；不删除数据绕过 |
| SOURCE_VERSION_UNREVIEWED / SOURCE_WORKTREE_DRIFT / SOURCE_FILE_DRIFT | 重新审查实际 Java 版本与接口，不自动覆盖本地源码 |
| TARGET_PORT_OCCUPIED | 由操作员检查占用进程；不自动杀死未知服务 |
| JAVA_START_FAILED / JAVA_HEALTH_TIMEOUT | 检查 JDK 17、离线 Maven 缓存和 demo 配置；无业务重试 |

就绪探测仅访问固定 `/actuator/health`，最多 60 次、总时限 120 秒、正文 64 KiB，拒绝重定向。
该基础设施预算独立于三次业务请求上限。调查和模型调用始终为零。

## 实际验收（2026-10-11）

原始脱敏统计：[stage3a-http-results.json](stage3a-http-results.json)。
执行代码版本 `d558df1`，run `d1becff562a149f68452861c95423164`。
后续 `bb8860b` 只加强就绪探测的大小/总时限/重定向限制，未再次创建真实课程。

| 步骤 | HTTP | 请求次数 | 耗时 ms | 可信检查 |
| --- | --- | --- | --- | --- |
| 登录 | 200 | 1 | 518.687 | 5/5 PASS（含 code、角色、Token 字段） |
| 创建 | 201 | 1 | 69.665 | 3/3 PASS |
| 查询本次 ID | 200 | 1 | 34.583 | 6/6 PASS（含 ID/code/name/teacherName） |

- 三次业务请求、一次创建、无创建重试；就绪探测 15 次尝试（含连接尚未建立的尝试）。
- 三个响应 code 均为 0；14/14 检查通过。动态 ID 未写死，查询路径来自本次 `create:$.data`。
- 合成课程 code `AT3Ae2ecc268599b46d3b9464dbd`，保留在 demo 数据库，未自动清理。
- JWT/密码/原始响应正文未写入报告或发布统计，实际 ID 在报告中脱敏。
- 完整本地证据：`runs/d1becff562a149f68452861c95423164/{report.json,fixture.json}`；
  环境审计 `runs/courseinsight-b880ac4e1859449bb6c3e659ccaa9de1/isolation.json`（均忽略、不提交）。
- Java 自有进程已停止，demo 基础容器和原数据卷保留；CourseInsight 仍只有原有 .gitignore 修改。
- 原基础真实回环 8 步/9 请求 PASS；FakeLLM 两类路径 3/3、5/5 PASS；调查回环
  正常零调查、ownership 一次调查且原 ASSERTION_FAILED 保留，各创建一次。真实模型调用均 0。

范围结束于三步链路。尚未实现跨用户、上传/异步、自动清理、MCP 或 Stage 3B。

最终本地 **452 passed**（保留 402 项、新增 50 项），Ruff 与 pip check 通过。
[四组 CI 38141751635](https://github.com/mofengyan595/agenttest/actions/runs/38141751635)
对应 `2d9f1d3` 全部 success：agent 各 452 passed；base 各 381 passed、5 skipped、1 warning。
该版本有界健康探测另经真实 Java 验证 PASS（8 次尝试、0 业务请求、0 创建），未增加合成课程。
