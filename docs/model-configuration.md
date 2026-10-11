# 阶段 2A 模型配置与调用边界

模型仅生成不可信规划，不持有 Executor、CredentialVault、目标地址或测试授权。
本模块使用现有 HTTPX 0.28.1 REST 客户端，无 OpenAI SDK 或 LangChain-OpenAI 新依赖；LangGraph 继续固定 1.2.14。

## 本地设置

从 .env.example 复制 .env，在本机填写三个变量：OPENAI_API_KEY、OPENAI_BASE_URL、AGENTTEST_MODEL。
不得将值发到聊天、文档或 Git。仅 `--model-env .env` 显式读取；不自动加载。
环境文件支持 UTF-8/BOM、注释、可选 export 和成对引号；不支持变量展开或可执行语法。
只读取这三个变量，已有进程环境优先。目标登录账号不从此入口读取。

学校服务的公开 Base URL 为 `https://chat.ecnu.edu.cn/open/api/v1`，当前用户配置使用 ecnu-plus；代码不以它们作为默认值。以本机配置及学校模型列表实际结果为准。
学校端点会选择 chatecnu 协议：POST BaseURL/chat/completions，Bearer 认证，max_tokens、json_schema、thinking disabled。
其他显式配置的 HTTPS 兼容端点使用 max_completion_tokens、strict json_schema、store false；不支持时明确失败，不自动降级为自由文本或 JSON mode。

## 调用与预算

1. `python scripts/probe_model.py --model-env .env` 只请求 GET models，不产生模型生成 Token。认证失败、模型不存在时先核对配置及密钥来源，禁止反复试错。
2. `python -m agenttest agent-demo --real --model-env .env --user-task '...'` 每次最多一次生成，默认 30 秒、2048 输出 Token、12000 总 Token。可显式设置 --model-timeout、--max-output-tokens、--max-total-tokens；Pydantic 校验上限。
3. 评测必须有已人工批准任务集及 --max-live-calls / --max-total-tokens 两个正预算。顺序调用，下一请求前预留输入上限和输出上限；用真实 usage 记账。usage 缺失停止后续调用，401/403/404 停止整组。

输入 Token 采用 UTF-8 字节数加固定包络余量的保守上界；不把它当作精确 tokenizer 结果。学校 usage 自身是估计值，统计按其返回值记录。超时后可能无法得知服务端实际用量，必须标为未知，不能记成 0 或继续消耗后续预算。
关闭环境代理和 HTTP 重定向，保持 TLS 证书校验。只向操作员选择的模型服务发送公共规划上下文；被测 HTTP 目标仍受原有固定 IP、隔离写入和白名单限制。

## 输出与失败

严格 PlannerDraft 使用封闭对象、枚举、标量参数和显式引用，再转换为现有 Scenario，继续经过 Vault、OpenAPI、Policy Registry 和预算预检。
没有已批准路径时允许模型返回空草案，直接归为 NEEDS_REVIEW / MODEL_NO_APPROVED_PLAN；不会转成可执行空 Scenario，更不会 PASS。实际 DSL 仍要求至少一个步骤。
保留 FakeLLM；默认 pytest 主动阻断 DNS/网络，CI 四组无需 Key，不运行真实评测。
当前真实模型格式不支持 CSV、嵌套对象、轮询或多次调查；不通过临时放宽 DSL 或策略来假装支持。

报告仅保存模型名称、调用数、HTTP 状态、Token usage、耗时、字节数、哈希和审核后结构；不保存 API Key、端点配置、原始 prompt、响应文本或 reasoning_content。
错误分为 MODEL_API_ERROR、MODEL_SERVICE_ERROR、MODEL_TIMEOUT、MODEL_CONNECTION_ERROR、MODEL_INVALID_RESPONSE、MODEL_OUTPUT_TRUNCATED、MODEL_TOKEN_BUDGET、MODEL_RESPONSE_LIMIT、MODEL_REFUSAL 等；后续执行沿用现有五类 Outcome。
保留模型输出摘要可以关联失败，但无法逐字回放历史非法输出。若需要原文审计，应另行设计人工批准的脱敏留存机制，不能直接开启 SDK debug 日志。

## 官方依据（2026-10-11 核对）

- [学校模型与 Base URL](https://developer.ecnu.edu.cn/vitepress/llm/model.html)
- [学校 Chat Completions](https://developer.ecnu.edu.cn/vitepress/llm/api/completions.html)
- [学校结构化输出](https://developer.ecnu.edu.cn/vitepress/llm/api/structuredoutput.html)
- [学校模型列表](https://developer.ecnu.edu.cn/vitepress/llm/api/models.html)
- [OpenAI strict 结构化输出](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat)
- [OpenAI Chat Completions 参数](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
- [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)
