# 批准负例：现状、边界与离线证据

本轮目标见[操作员目标](trusted-testing-goal.md)。起点为
`aef1206450a1c6cb9ccf9dda24eed075affa901c`，工作区干净、与本地 origin/master 一致；
通过 GitHub 连接器核对该 SHA 的 Python `39392748521`、Web `39392748497` 均 success。
旧三轮完整计划和执行各 23/36、完整任务 15/36；单接口 2/18、流程 12/12、项目 1/6，
指定故障 8/9、追加 1/7，以及 NE_NEW01 的原证据全部保留，不能跨协议计算提升。

## 根因与实现

原 `goal_suite._single_candidates` 对所有非法候选统一输出
`EXPECTED_ERROR_BEHAVIOR_NOT_APPROVED`。`discovery` 只生成静态负例描述；
`dsl.validate_plan` 校验字面量，`Executor._step` 再完整校验解析后的请求。
原错误响应没有批准断言，因此不能通过简单放宽 Schema 安全地执行。

新增策略字段 `negative` 引用合法基线策略、目标字段、变异种类、固定取值和精确违例代码集合。
它仍是操作员选定的项目配置，模型的 DSL 只能引用策略 ID；不能提交负例规则或任意非法值。
负例策略必须保留基线的接口、角色、业务范围、全部绑定与资源来源，并有已审查的 4xx 断言来源。
具体非法值由执行器从批准配置构造。固定值的批准范围是一个单点，不代表任意区间均获授权。

流程：完整 DSL/Policy/Intent 校验 → 解析动态来源 → 完整正常请求校验 →
校验指定变异确实产生批准的全部违例 → 只改变一个 body/query 字段 →
原请求大小/地址/操作/凭据门禁 → 原 Verifier。没有 skip_validation 开关，没有更改正常校验。
类型与枚举同时违反时必须同时批准；未批准的其他字段错误仍在发送前被阻断。
支持当前校验子集中的必填缺失、类型不匹配、枚举外值、长度/数值越界；套件自动发现仍沿用原标量子集。
认证、路径、文件和动态资源来源不参与变异，负例响应也不能作为后续资源生产者。
整个请求体缺失、嵌套字段的逐路径发现、未知业务错误预期仍保持待审；未自动扩展支持面。

报告单独记录 `NORMAL_VALIDATED`、`APPROVED_NEGATIVE`、批准违例及是否按可信预期拒绝。
PASS 负例表示目标正确拒绝输入；错误接受或错误正文为 ASSERTION_FAILED；超时和大小门禁
仍为 TEST_INFRA_ERROR，未知策略/错误预期保持 NEEDS_REVIEW。基础设施失败不会获得“负例通过”。

## 专用合成规则

仅在 `/validation-records` 的专用 ASGI 目标使用。message 为 2–8 字符，category 为 a/b，
count 为 1–5 的整数；正常返回 201 / created=true，非法输入返回 422 / code=INVALID_INPUT。
这些是本合成目标的操作员规则，不从 OpenAPI 的输入约束推断业务错误响应。
8 个批准变异使用独立策略，原两域配置未修改。

`negative_demo` 用独立处理函数判断输入；accept_invalid 故障错误接受输入，wrong_error 故障
返回错误的业务错误码。开发固定 Fake 提案、规则与真实模型能力分别归因。

## 已运行验证

- 针对批准变异、未批准输入/角色/操作/来源、精确违例、大小与超时：28 passed。
- 请求校验、执行器、原套件、导出、旧自然语言验收关联：123 passed / 56.05s。
- 新负例、候选发现、DSL/授权安全和全部历史/新增固定回归：155 passed / 16.84s。
- Ruff 全仓和 git diff --check 通过。
- `scripts/check_approved_inputs.py --output runs/ttg-stage1-demo --export examples/regressions/approved-inputs`：
  正常与负例各 PASS，两个目标故障各 ASSERTION_FAILED，共 4 次 ASGI 请求；真实模型和外部业务请求 0。
  通过的两项使用原模板导出，回归 fixture 陷阱禁用所有模型和真实 HTTP。

初次沙箱 pytest 在 Windows asyncio 的本地 socketpair/accept 阶段阻塞，未进入异步测试；
保留诊断日志 `runs/ttg-negative-diagnostic.log`，精确结束本轮阻塞进程后，正常本地环境重验通过。
没有移除默认断网夹具，也没有修改 Python 或全局环境。

可复制的离线演示（输出使用新目录）：

```powershell
.\.venv\Scripts\python.exe scripts/check_approved_inputs.py --output runs/approved-inputs-demo
.\.venv\Scripts\python.exe -m pytest examples/regressions/approved-inputs -q
```

以上均为 DEVELOPMENT_ONLY / UNREVIEWED，独立人审未完成，没有人工计时。
下一准确里程碑：基于旧保存计划审计项目分组失败，限制每组公开规划上下文，预览必需与可选预算并验证足额/不足退出。
