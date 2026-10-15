# 陌生第三方 REST 项目验证准备（2026-10-14）

后续状态（2026-10-15）：操作员已明确批准隔离方案，[两条真实模型只读实测均通过](petclinic-readonly-20261015.md)，目标已关闭。下文保留准备时的未审核／未启动状态，完整实际结果和原始文档校验问题见实测记录。

## 目标与固定来源

选定 [Spring Petclinic REST](https://github.com/spring-petclinic/spring-petclinic-rest)，上游提交固定为 `afc8fc1d3b8ec8de69414482b3bd70a1da78cb95`。这是独立开源宠物诊所服务，首轮只测两个 GET 场景，复用现有 generic 规划、TaskContract、Workflow、Executor 和 Verifier，不新增执行底座，也不修改上游业务代码。

已下载固定提交的 OpenAPI、控制器、官方测试、应用配置和 H2 初始化 SQL，逐文件 SHA-256 保存在忽略目录 `runs/third-party-preparation-20261014/preparation.json`。项目本机具备 Java 17.0.20、Maven 3.9.9；Docker Linux 服务未运行。没有运行 Maven 构建或下载构建依赖，也没有启动目标。

## 已核对的边界

- [默认配置](https://github.com/spring-petclinic/spring-petclinic-rest/blob/afc8fc1d3b8ec8de69414482b3bd70a1da78cb95/src/main/resources/application.properties)选择 H2 与 Spring Data JPA，关闭鉴权；启动会初始化数据库。下一阶段必须使用专用临时目录、仅绑定 127.0.0.1、全新进程内 H2，禁止连接 MySQL／PostgreSQL或已有实例。
- [静态 OpenAPI](https://github.com/spring-petclinic/spring-petclinic-rest/blob/afc8fc1d3b8ec8de69414482b3bd70a1da78cb95/src/main/resources/openapi.yml)的原 YAML 响应码键被解析为整数，当前导入器要求字符串键，完整原文件以 OPENAPI_DOCUMENT_KEYS_MUST_BE_UNIQUE_STRINGS 拒绝。准备文件只选两条 GET 及其传递引用，将响应码键编码为 JSON 字符串，保留响应定义；没有修改上游或放宽导入器。
- 执行器的目标配置只接受 origin。按应用 context path 与控制器映射，将准备契约的路径明确挂载在 `/petclinic/api`，原 Operation ID 保留；原 servers 不用于授予目的地权限。
- [宠物类型控制器](https://github.com/spring-petclinic/spring-petclinic-rest/blob/afc8fc1d3b8ec8de69414482b3bd70a1da78cb95/src/main/java/org/springframework/samples/petclinic/rest/controller/v1/PetTypeRestControllerV1.java)的读取返回 200，缺失返回 404。其创建返回 201，静态 OpenAPI 声明 200；这是静态不一致，尚未验证运行时，因此首轮排除全部写接口。启用 Basic 认证的配置也不在首轮支持范围。

## 具体准备结果

| 准备项 | 内容 |
| --- | --- |
| 隔离目标草案 | `http://127.0.0.1:18886`，allow_mutations=false，仅允许 listPetTypes／getPetType，最多 4 次请求 |
| 任务一 | 读取类型 ID 1，预期 ID 1、名称 cat；依据全新 H2 初始化 SQL，启动后须核对实际种子状态 |
| 任务二 | 读取 ID 2147483647，预期 404；只适用于全新、核对过的隔离数据库 |
| 参数边界 | 每题仅批准指定 petTypeId 字面值；不让模型猜 ID、调整断言或扩展接口 |
| 静态核查 | 两条 GET 契约解析、目标校验、DSL 校验、TaskContract 绑定与需求一致性检查通过 |
| 当前批准状态 | policies.UNREVIEWED.json 中 reviewed=false，批准策略数为 0，不能直接执行 |

准备文件均在忽略目录，包含 readonly.openapi.json、readonly.target.json、policies.UNREVIEWED.json、cases.UNREVIEWED.json 和来源哈希。目标启动、HTTP、模型调用、数据库写入与依赖安装全部为 0。静态准备不能证明陌生项目运行效果。

## 下一项受控动作

获得操作员对新 Java 数据库初始化与构建依赖下载的明确许可后，在专用临时 Maven 仓库中构建固定版本，启动仅本机可访问的全新 H2 实例。先核对真实契约、响应与种子状态，再审核策略并重新绑定 TaskContract；不直接把未审核草案改为可执行配置。先做固定计划真实 HTTP 验收，随后冻结准确出站输入并最多调用 ecnu-plus 两次，每题一次、不重试，累计保留上限 48,192 Token、模型等待 60 秒。

成功与失败都保存完整分母和首次记录；异常停止，不自动修补计划或预期。结束后关闭临时服务，H2 随进程销毁，不清理用户已有数据库或改全局环境。结果仍是 DEVELOPMENT_ONLY／UNREVIEWED，不冒充独立评测。

项目 [AGENTS.md](../AGENTS.md) 明确要求：`New real Java database writes require explicit operator approval.` 启动 H2 会写入初始化数据，因此当前只完成可审阅的准备，没有越过这一要求。
