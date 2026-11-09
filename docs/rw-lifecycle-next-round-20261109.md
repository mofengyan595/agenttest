# 下一次冻结验收的准备（配置就绪，未启动）

状态：**只准备，未执行**。未调用模型、未启动夹具、未发送任何业务请求；
`WRITE_AUTHORITY["operator_approval"]` 仍为 `NOT_GRANTED`。

## 已批准的运行参数（写入授权副本与冻结记录）

| 参数 | 值 | 位置 |
| --- | --- | --- |
| 单次模型超时 | **45 s**（上限 120 s，超时不重试） | `authority.WRITE_AUTHORITY["model_timeout"]` |
| 单用例总预算 | **150 s** | `budgets.max_seconds` |
| 每用例模型调用 | ≤ **2** | `budgets.max_model_calls` |
| 每用例目标 HTTP | ≤ **12** | `budgets.max_requests` |
| 命名空间 | 每次运行 `e2e-<case>-<run_id>` | `namespace_rule` |
| 清理 | 每次创建配对 DELETE + 404 回读 | `cleanup_rule` |

不变的安全闸门：新增 `UNKNOWN` 立即停止，超时不重放；`startable()` 仍要求显式
`operator_approval="GRANTED"` 副本，且现在同时校验"45 s 在 (0,120] 内"与
"150 s ≥ 2 × 45 s"，任一不满足即拒绝。

## 位置与不可重跑约束

- 已消费：**T1、T2**（第二次验收各执行一次，证据见 `docs/evidence/rw-lifecycle-20261109-v2/`）；
- 可执行：**T3**（上次未执行、未被消费）；
- 驱动新增 `--cases` 选择器，硬拒绝已消费位置：
  `--cases T1` → `CONSUMED_POSITION_REFUSED:T1`，未知键 → `UNKNOWN_CASES:…`，均在夹具启动前退出；
- 默认（不传 `--cases`）仍按计划顺序包含三个位置，因此下一次运行必须显式写 `--cases T3`。

## 冻结流程（待写入授权后执行）

```
# 前提：工作树干净；操作员已就"新验收范围"给出写入授权并登记到授权副本
python scripts/rw_lifecycle/acceptance.py freeze runs/rw-acceptance-v3-<date> --cases T3
python scripts/rw_lifecycle/preflight.py          # 10 项生命周期控制 + 3 项变体控制
python scripts/rw_lifecycle/acceptance.py dry runs/rw-acceptance-v3-<date> --cases T3
python scripts/rw_lifecycle/acceptance.py run runs/rw-acceptance-v3-<date> --cases T3
```

冻结记录必须包含：源码与 `HEAD`、需求原文与 rubric、批准配置摘要、`model.timeout=45`、
`budgets`、目标初态哈希、`consumed_positions`、`cases=[T3]`。每次运行只执行一次；
不重跑、不换题、不调分。

## 需要操作员另行给出的授权

1. **写入授权**：新验收范围内的隔离写入（`operator_approval="GRANTED"` 的运行副本）；
2. 若希望把 T3 与任何新增位置一起执行，需要新的位置定义与冻结（不复用 T1/T2）。

## 预算与停止条件（沿用）

- 单用例：≤12 次目标 HTTP、≤150 s、≤2 次模型调用；
- 任一 `UNKNOWN`、越权、配置漂移或不可控资源状态 → 立即停止并保留证据；
- 结束后恢复 `NOT_GRANTED`、停止夹具、核对 19097 端口无监听。

## 已就绪的确定性检查

- `tests/test_rw_lifecycle_next_round.py`：拒绝已消费位置与未知键、`dry` 记录
  `budgets/model_timeout/cases/consumed_positions`；
- `tests/test_usage_exception_scope.py`：例外只覆盖单次调用、其他 `UNKNOWN` 仍阻断、
  45 s/150 s 预算可 `startable()` 且越界即拒绝；
- `tests/test_model_timeout_budget.py`：`AGENTTEST_MODEL_TIMEOUT` 解析与 `(0,120]` 边界。
