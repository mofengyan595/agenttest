# 可信测试 Goal：已有成果与可复制演示

工程与冻结验证已交付，真实质量目标未达到：12/36、明确足额12/30、足额项目0/6。
[最终结果](trusted-testing-final-results.md) · [原失败](trusted-testing-results.md) · [冻结协议](trusted-testing-reference-amendment.md) ·
[接入成本](trusted-testing-approved-projects.md) · [第三项目](trusted-testing-laboratory.md) ·
[简历/面试](trusted-testing-resume-interview.md) · [已批准里程碑记录](trusted-testing-next-milestone.md)。
[逐项交付核对](trusted-testing-completion-audit.md)。

以下从仓库根目录执行，沿用项目虚拟环境，不安装依赖。输出目录必须尚不存在；不覆盖历史输出。
全部使用固定Fake/Mock或既有pytest模板，无真实模型、Docker、外部业务系统或真实数据库写入。

## 单接口：正常、批准负例和目标故障

```powershell
.\.venv\Scripts\python.exe scripts/check_approved_inputs.py --output runs/trusted-inputs-demo
.\.venv\Scripts\python.exe -m pytest examples/regressions/approved-inputs -q
.\.venv\Scripts\python.exe -m pytest examples/regressions/TV_NEW01 -q
```

第一条实际发送四个ASGI请求：正常PASS、批准非法输入按预期拒绝PASS、错误接受非法输入和错误正文
各ASSERTION_FAILED。后两项是目标故障演示，不能修改断言让它们通过。
第二条执行已导出的正常/负例固定模板，模型/真实HTTP陷阱保持生效；是operator_plan回归，
不是第二次自然语言规划，也不保持原HTTP会话或重放原写请求。

## 跨接口与项目：同一核心三条路径

```powershell
.\.venv\Scripts\python.exe scripts/check_laboratory.py --output runs/trusted-laboratory-demo --core-sha HEAD
.\.venv\Scripts\python.exe scripts/check_trusted_profiles.py --output runs/trusted-profiles-demo
```

实验室脚本先后验证单接口、双独立生产者→装载→查询/角色拒绝、完整项目；
分别11/1/20个场景，11/5/36个请求，项目15个批准负例。原88c9配置复用收据保留；
新里程碑精简了通用提示，以上显式选当前已提交核心，执行前后核对该核心不变，不能说从88c9起核心始终未改。
这是固定Fake的配置复用演示，不代表真实模型生成了标准计划。
原两域脚本复用相同引擎，展示足额课程/工单项目及请求8不足变体；预算不足保留17场景分母。
项目配置目录未包含逐题TaskContract，需求约束由注册目标、批准关系和原文派生。
评测的人工参考矩阵位于隔离评分器，不能注入产品。

## 仅冷核对本轮冻结结果

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_trusted_testing.py --verify runs/ttg-validation-mock
```

该命令不调用模型、不运行目标，只重算已有清单、排期、用量、评分与停止规则。
36位置仍全部保留；实际尝试8，TT08输入上界超限后28位置未执行。
这是原v1失败的冷核对，不替换后来的排期。当前真实排期与首次TV_NEW01已完成：

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_trusted_testing.py --verify runs/ttg-validation-amended-real
.\.venv\Scripts\python.exe scripts/trusted_first_demand.py --verify runs/ttg-amended-first-demand
```

这两条只读已有归档，不调用模型或目标。公开逐位置收据和失败明细见最终结果，完整原档在私有runs目录。

## 工程核对与尚未完成

冻结前85项关联回归+1项付费停止门禁、Ruff/diff通过；阶段4前端36项测试、类型检查、构建和
5项浏览器交互验收通过。准确提交的远程CI状态另见进度，失败或取消的旧workflow均保留。
最终本地归档脚手架11项+全部固定回归8项：19 passed/32.47s；提示输入预算1 passed/2.88s。
Ruff、pip check、diff检查通过；四份排期及首次需求归档冷核对、公开收据/原档一致、模板字节/哈希、
文档本地链接、私密值排查和核心树未变检查通过，核对新增模型/目标调用均0。
原归档和固定资产没有覆盖。原15/36、追加1/7、NE_NEW01不改分，也不与本轮跨协议计算提升。

真实36排期和首次TV_NEW01均完成，质量目标未达到；正式独立人审和人工计时未知。
新增需求首次导出成功，原CLI封印失败经仅元数据修正后冷核对通过，未重跑模型/原请求。
当前固定排期结束，不继续补跑追分；新的质量工程须另立里程碑。
