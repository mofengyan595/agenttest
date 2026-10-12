# Eval v2 真实 A/B 实验协议准备

本轮只准备协议，真实模型调用为 0。当前缺少独立人工留出集；整改代码还须按实际新 SHA 核对 CI，不能使用基线成绩替代。`protocol` 命令不读取密钥、不调用模型、不执行业务请求，输出始终 `operator_authorized=false`、`ready_to_run=false`，供操作员审阅后另行授权。

## 固定实验条件

| 条件 | A 与 B 的要求 |
| --- | --- |
| 数据 | 同一个独立人工审核 Suite、版本锁、任务与标签摘要；不使用 C 开发草案冒充留出集 |
| 模型 | 同一个精确 model 和 HTTPS API endpoint、公开模型配置、输出格式、超时；关闭重试 |
| 权限 | 同一个 mini fixture、契约、Policy、目标配置和 Intent Validator；不连接 Java 或业务数据库 |
| 调用预算 | 每组相同最大调用数、总 Token 上限、单次输入/输出预留；另列双组总授权上限 |
| 执行预算 | 每 Workflow 相同 HTTP 上限、耗时和工具调用预算；正常/故障配对另计 |
| 上下文 | A 为原 planning_context；B 只增加有来源和字节上限的静态批准候选 |
| 输出 | 同一新实验编号下 A、B 独立新目录；失败、中断或预算不足不在原目录重跑 |

Candidate Discovery 不改变可执行授权，不包含负例请求、隐藏标签或故障开关。STATIC_VALIDATED 不作执行许可。B 输入变长导致的实际 Token 和延迟必须单独记录，不能只报告成功率变化。

## 离线生成协议

```powershell
# 未提供审核集/模型/预算时也可生成阻塞清单；不会运行实验。
.\.venv\Scripts\python.exe scripts/evaluate_v2.py protocol --output runs/ab-protocol-pending

# 人工审核完成后，用真实获准值准备具体协议，仍然不会调用模型。
.\.venv\Scripts\python.exe scripts/evaluate_v2.py protocol `
  --suite <operator-reviewed-suite.json> --review-lock <registry/version.json> `
  --allow-model <approved-model> --allow-endpoint <approved-https-endpoint> `
  --max-calls 8 --max-total-tokens 176384 --max-input-tokens 20000 `
  --max-output-tokens 2048 --timeout 30 --max-http-requests 40 `
  --max-workflow-seconds 90 --candidate-max-bytes 6000 `
  --output runs/ab-protocol-reviewed-001
```

以上预算仅示范 **8 个预定调用任务**的最坏预留：每次 20000+2048=22048，单组 8 次/176384，双组最多 16 次/352768。它不是实际 usage、计费 Token 或价格估计。若实际审核集有 9 个支持/拒绝任务，此示例预算不足，应重新拟定协议并授权；review/future 不占预定模型调用。

协议包括代码与工作区、Suite/锁/可信输入/A/B context 摘要、模型公开配置、预算、阻塞原因和两组命令数组。模型配置根据既有适配器协议固定；环境文件必须与审核的 model/endpoint 对应。最终运行冻结记录实际环境与参数，比较工具拒绝不一致的 A/B。

每组业务请求理论上限为 `(支持任务数 + 2 × 预定 Case/缺陷机制对数) × 每 Workflow HTTP 上限`；只有原任务端到端成功才执行配对，因此实际请求通常更少。默认每 Workflow 90 秒、40 次 HTTP、1 次工具调用，mini ASGI 执行权限保持不变。

## 人工授权前检查

1. 独立人员完成原始来源、开发接触、标签、拒绝依据与审核记录；新版本锁通过 formal validate。
2. 整改代码按用户授权提交后，核对同一个新 SHA 的 Windows/Linux × Base/Agent CI；旧绿色 SHA 不替代。
3. 操作员确认精确模型、endpoint、私有凭据来源、单组及双组调用/Token 上限、时间/HTTP 预算和新输出目录。
4. 明确授权这次收费 A/B 实验。协议文件本身、配置字段或工具生成的命令都不构成人工授权。

获准后才执行 protocol.json 中 A、B 命令模板，用项目解释器、实际审核集和版本锁替换占位项；两组参数相同，仅 variant 和输出目录不同。执行期间保持代码、任务、标签、Prompt、Policy、环境和预算不变。usage 缺失、预算耗尽、冻结漂移或配置错误时停止，无自动重试提分。结果封存后用 compare 和 rescore 审计；归档重评分不算新实验。

## 观察与结论

分别报告预定/条件计划合法率、独立意图率、端到端率、主动拒绝与安全门禁拦截、缺陷配对、未执行与异常类别。按同 Case 输出 A→B 分类迁移，同时保留两组 input/output/total tokens、模型与 Workflow 延迟、HTTP 数和上下文大小。缺失 usage 为 null，不估算真实收费 Token。

4–6 个支持、2–3 个拒绝任务只能作初步可行性观察。可以描述配对变化，不能直接声称统计显著、通用泛化或 Candidate Discovery 提升真实模型成功率。当前 Fake/Mock 验证只证明离线路径、评分和隔离工作；本轮没有新增真实模型成绩。
