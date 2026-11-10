# 修复单位置冻结门禁误报（第 8 轮）

日期：2026-11-10。基线：`58b0d0654f8d026a05f967ee8b461b34b51cfd8d`。
本轮只改代码与离线测试，不执行真实 M2/F1，不创建授权，不调用模型。

## 1. 根因与最小修复

`acceptance.guard()` 通过 `current_view()` 构造冻结比对视图，而 `current_view()` 使用
`cases()`；`cases()` 又被 `--cases` 过滤。于是：

- 冻结记录：`requirements.cases = [M2, F1]`，`requirements.digest = f2ba78ab754c…`
- `--cases M2` 的视图：只有 `[M2]`，摘要 `74984a42bb7b…`
- `compare()` 因此报 `CASES_CHANGED: ["F1"]`——这是“选择范围”污染了“冻结比对范围”。

修复（`scripts/rw_lifecycle/acceptance.py`）：

```python
def current_view(identity, initial=None):
    ...
    return execution_gate.snapshot(
        ROOT, plan=PLAN, cases=CASES, consumed=CONSUMED_POSITIONS, ...)
```

`CASES` 是原冻结计划的完整集合且保持原顺序；`SELECTED` 只决定 `cases()`（本次实际执行
位置）。`CASES_CHANGED` 校验本身保留，其他冻结规则未放宽。

同时补一个同类完整性缺口（`scripts/rw_lifecycle/execution_gate.py`）：比对前先验证冻结记录
自身的 `requirements.digest` 是否等于 `digest(requirements.cases)`；若有人在保留旧摘要的
情况下改写题目，也会报 `CASES_CHANGED`，而不是被静默接受。该检查只增加拒绝条件，不改变
任何评分标准。

## 2. 修复前后的完整冻结摘要对照

对真实 `freeze.json`（只读，不启动 fixture）计算：

| `--cases` | 修复前视图 case 集 | 修复前摘要 | 修复后视图 case 集 | 修复后摘要 | 修复后 blocking |
| --- | --- | --- | --- | --- | --- |
| `M2` | `[M2]` | `74984a42bb7b…`（误报） | `[M2, F1]` | `f2ba78ab754c…` | 无（`CASES_CHANGED` 消失） |
| `F1` | `[F1]` | 其他子集摘要（误报） | `[M2, F1]` | `f2ba78ab754c…` | 无 |
| 不选择 | `[M2, F1]` | `f2ba78ab754c…` | `[M2, F1]` | `f2ba78ab754c…` | 无 |

实测命令输出（真实冻结目录，离线）：

```
['M2']       view cases: ['M2','F1'] digest: f2ba78ab754c blocking: ['INITIAL_STATE_CHANGED'（因离线测试用假 initial）]
['F1']       view cases: ['M2','F1'] digest: f2ba78ab754c ...
['M2','F1']  view cases: ['M2','F1'] digest: f2ba78ab754c ...
```

`INITIAL_STATE_CHANGED` 仅因离线检查使用了假的 initial 状态；真实预检会重置 fixture 后取得
真实初始状态。

## 3. 真实 guard 集成测试

新增 [test_rw_acceptance_guard_integration.py](../tests/test_rw_acceptance_guard_integration.py)
**13 passed**，全部调用真实 `acceptance.guard()`（不再 monkeypatch 门禁），使用临时冻结目录
和离线身份：

1. 完整集合选择 M2：通过；
2. 完整集合选择 F1：通过；
3. 不选择单独位置：通过；
4. 修改 M2 题目（保留旧摘要）：`CASES_CHANGED` 拒绝；
5. 只选择 M2、修改未选择的 F1 题目：`CASES_CHANGED` 拒绝（本轮回归点）；
6. 修改 rubric：`RUBRIC_CHANGED` 拒绝；
7. 修改预算：`BUDGETS_CHANGED` 拒绝；
8. 修改模型身份：`MODEL_IDENTITY_DRIFT` 拒绝；
9. 工作树不干净（运行模式）：`EXECUTION_REQUIRES_CLEAN_TREE` 拒绝；
10. 只执行 M2：`positions/M2` 完成，`F1` 仍 `UNCONSUMED` 且无目录；
11. 只执行 F1：`positions/F1` 完成，`M2` 仍 `UNCONSUMED` 且无目录；
12. 门禁失败（rubric 改变）：不产生任何 `positions/`；
13. 无独立授权：`RUN_AUTHORIZATION_MISSING:M2`，零消费。

测试中的执行路径只桩掉 fixture 与模型服务，真实经过 `guard`、授权校验、锁、claim、
证据与完成标记；不接触真实 `runs/rw-acceptance-v4b-20261109`。

## 4. 选择范围耦合审查

| 位置 | 应使用的集合 | 现状 | 结论 |
| --- | --- | --- | --- |
| `current_view()` / `guard()` | 完整冻结集合 | 改为 `CASES` | 已修复 |
| `cases()` | 本次执行集合 | 保持 `SELECTED` 过滤 | 仅用于执行与预检范围 |
| `run()` | 单位置 | `only_position()` + `SELECTED` | 保持一次一位置 |
| `claim/evidence/completed` | 所选位置 | 只写 `positions/<SELECTED>` | 证据准确记录所选位置 |
| `status_report()` | 完整集合 + 已消费位置 | 已用 `CASES` | 无需改 |
| `preflight_report()` 的门禁 | 完整集合 | 经 `guard` 已修复 | 修复 |
| `preflight_report()` 的清单/汇总 | 所选位置 | 仍用 `cases()` | 有意保留：预检报告只覆盖本次选择的就绪项 |
| `freeze()` | 冻结时的完整计划 | 仍用 `cases()`（`--cases` 可冻结子集） | 有意保留：冻结集合由冻结命令定义；本轮不重新冻结，也不改变既有语义 |
| 消费判定 | 每位置独立 | `positions/<case>` 独立 | 保持 |

审查未发现其他因 `--cases` 改变冻结摘要的路径。

## 5. 离线验证

- `python -m ruff check .`：通过（0 错误）。
- `python -m pytest tests/test_rw_acceptance_guard_integration.py -q`：13 passed。
- `python -m pytest tests/test_rw_acceptance_preflight.py -q`：10 passed。
- `python -m pytest tests/test_rw_acceptance_authorization.py -q`：15 passed。
- `python -m pytest tests/test_rw_acceptance_consumption.py -q`：30 passed。
- `python -m pytest examples/regressions -q`：10 passed。
- `python scripts/check_full.py --jobs 4`：**待填**。

全部测试离线、零模型调用、零真实位置消费、零真实授权写入。

## 6. 预检（提交后，无付费）

在干净提交上执行：

```bash
python scripts/rw_lifecycle/acceptance.py preflight runs/rw-acceptance-v4b-20261109 --plan plan_v4
```

结果：**待填**（M2 READY / F1 READY / `blocking=[]`，完整冻结摘要 `f2ba78ab754c…`）。

## 7. 最终消费状态

- `M1 = HISTORICAL_CONSUMED`
- `M2 = UNCONSUMED`（第 7 轮在 claim 前被拒绝，未消费）
- `F1 = UNCONSUMED`

没有真实 `positions/M2`、`positions/F1` 或新的真实 `authorizations/` 记录；旧授权
`authorize-M2-de9a869.json` 原样保留，未迁移到新 HEAD。

## 8. 是否具备再次请求真实 M2 授权的条件

代码层面具备：单位置执行不再产生虚假 `CASES_CHANGED`，任何真实冻结内容变化仍被拒绝，
M2/F1 仍独立消费。

但按授权规则，第 7 轮的操作者授权绑定 `de9a869`，且该命令在 claim 前已被拒绝；本轮修复会
产生新提交。**需要用户针对最终新 HEAD 再次明确授权一次 M2**，并重新确认 12 请求 / 150 秒 /
2 次模型调用 / 45 秒超时；不得复用旧授权、不得自动迁移。F1 继续不授权。

## 9. 改动文件

- `scripts/rw_lifecycle/acceptance.py`：`current_view` 使用完整 `CASES`。
- `scripts/rw_lifecycle/execution_gate.py`：冻结记录自身 case 摘要一致性检查。
- 新增 `tests/test_rw_acceptance_guard_integration.py`（13 项真实 guard 集成测试）。
- `tests/test_rw_acceptance_consumption.py`：原有门禁用例的冻结记录改为完整 case 集。
- 本报告与 `docs/progress.md`。
