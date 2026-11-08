# 第三轮缺陷修复证据（2026-11-08）

本目录只保存**离线**验证记录；第三轮真实模型 A/B 的证据仍在
`docs/evidence/mixed-planning-round3-ab-20261107/`，本轮未改动、未重跑。

| 文件 | 内容 |
| --- | --- |
| `pre-fix-failures.txt` | 用 `git archive 8111a37` 导出的修复前源码树运行新反例的原始输出（19 失败 / 2 通过） |
| `pre-fix-junit.xml` | 同一次运行的 JUnit 明细 |
| `full-offline-result.json` | 修复后完整离线回归结果（2435 collected / 2420 passed / 0 failed / 0 errors / 15 skipped） |
| `full-offline-source-archive.json` | 该次回归 `source.zip` 的快照哈希 |
| `full-offline-source-hashes.json` | 该次回归 `source/` `tests/` `scripts/` 的逐文件 SHA-256，以及整体 `source_sha256` |

## 复现步骤

```powershell
# 修复前：只读导出 master 8111a37 的源码树，不建分支、不改动工作区
git archive --format=zip HEAD -o runs\prefix-head.zip
Expand-Archive runs\prefix-head.zip -DestinationPath runs\prefix-head
Copy-Item tests\test_mixed_planning_round3.py runs\prefix-head\tests\
cd runs\prefix-head
$env:PYTHONPATH = "$PWD\src"
..\..\.venv\Scripts\python.exe -m pytest tests/test_mixed_planning_round3.py -q --tb=line `
  --junitxml=..\..\docs\evidence\mixed-planning-round3-fixes-20261108\pre-fix-junit.xml

# 修复后：完整离线回归（4 个独立进程，冻结当前源码）
cd ..\..
.venv\Scripts\python.exe scripts\check_full.py --jobs 4 --output runs/full-checks/fixes-final
```

运行环境：Windows 11、Python 3.11、仓库内 `.venv`；无网络、无模型调用、无外部目标、无业务写入。
