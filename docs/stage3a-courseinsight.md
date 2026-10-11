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
