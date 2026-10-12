import { useCallback } from "react";
import { Link, useParams } from "react-router";
import { ArrowLeft, FileCheck2, RefreshCw } from "lucide-react";
import { api, validRunId } from "../api/client";
import { useQuery } from "../useQuery";
import {
  dateLabel,
  metric,
  outcomes,
  taskState,
  workflowLabel,
} from "../status";
import {
  Badge,
  Empty,
  Failure,
  Loading,
  Notice,
  OutcomeBadge,
  PageTitle,
  Panel,
} from "../components/ui";
import { ValidationLayers } from "../components/ValidationLayers";
import { WorkflowEvidence } from "../components/WorkflowEvidence";
import { StepEvidence } from "../components/StepEvidence";
import { RunLookup } from "./History";
import type { RunReport } from "../api/types";

export function Inspect() {
  return (
    <>
      <PageTitle
        eyebrow="TRACE / 运行详情"
        title="沿着证据，理解一次测试。"
        description="输入真实 Run ID 查看校验、执行步骤和失败调查。"
      />
      <Panel title="打开运行报告">
        <RunLookup />
        <Empty title="等待选择报告">
          <p>也可以从历史测试报告进入已有运行。</p>
          <Link className="inline-link" to="/runs">
            前往历史报告 →
          </Link>
        </Empty>
      </Panel>
    </>
  );
}
export function RunDetail() {
  const { runId = "" } = useParams();
  const query = useQuery(
    useCallback((signal: AbortSignal) => api.run(runId, signal), [runId]),
  );
  if (!validRunId(runId))
    return (
      <>
        <PageTitle
          eyebrow="TRACE / 运行详情"
          title="Run ID 格式无效"
          description="应为 32 位小写十六进制标识，不接受路径或 URL。"
        />
        <RunLookup />
      </>
    );
  if (query.loading) return <Loading />;
  if (query.error || !query.data)
    return (
      <>
        <Link className="inline-link" to="/runs">
          <ArrowLeft size={15} />
          返回历史报告
        </Link>
        <Failure message={query.error || "未找到报告"} retry={query.retry} />
      </>
    );
  return <ReportView run={query.data} refresh={query.retry} />;
}
export function ReportView({
  run,
  refresh,
}: {
  run: RunReport;
  refresh: () => void;
}) {
  const steps = run.steps || [];
  const investigations = run.investigations || [];
  const task = taskState(run);
  const businessRequests =
    run.steps && steps.every((step) => Array.isArray(step.attempts))
      ? steps.reduce((n, s) => n + (s.attempts?.length || 0), 0)
      : "未记录";
  const investigationRequests =
    run.investigations &&
    investigations.every((step) => Array.isArray(step.attempts))
      ? investigations.reduce((n, s) => n + (s.attempts?.length || 0), 0)
      : "未记录";
  return (
    <>
      <Link className="back-link" to="/runs">
        <ArrowLeft size={15} />
        历史测试报告
      </Link>
      <PageTitle
        eyebrow="TRACE / 运行详情"
        title="一次运行，一条证据链。"
        description="业务结果、工作流完成与任务满足分别呈现。"
      >
        <button onClick={refresh}>
          <RefreshCw size={16} />
          刷新
        </button>
      </PageTitle>
      <section className="run-overview">
        <div className="run-heading">
          <div>
            <span className="eyebrow">RUN ID</span>
            <h2 className="mono">{run.run_id}</h2>
            <p>
              {dateLabel(run.started_at)}
              <span className="divider">/</span>
              {run.environment || "未记录环境"}
            </p>
          </div>
          <OutcomeBadge value={run.outcome} />
        </div>
        <p>
          {run.outcome
            ? outcomes[run.outcome]?.description
            : "未记录业务测试结果。"}
        </p>
        <div className="overview-footer">
          <Badge tone={task.tone}>{task.label}</Badge>
          <span>Workflow · {workflowLabel(run.workflow_completed)}</span>
          <span>
            模式 ·{" "}
            {run.task_mode === "operator_plan"
              ? "操作员固定计划"
              : run.task_mode === "natural_language"
                ? "自然语言任务"
                : "未记录"}
          </span>
        </div>
      </section>
      {run.task_mode === "operator_plan" && (
        <Notice>
          operator_plan 的 PASS
          表示批准计划执行通过，不表示自然语言目标已经验证。
        </Notice>
      )}
      <div className="metric-grid">
        <div>
          <span>执行步骤</span>
          <strong>
            {run.steps ? steps.length : "未记录"}
            <small> steps</small>
          </strong>
        </div>
        <div>
          <span>业务 HTTP 尝试</span>
          <strong>
            {businessRequests}
            <small> requests</small>
          </strong>
        </div>
        <div>
          <span>工具调用 · Executor</span>
          <strong>
            {run.tool_calls == null ? "未记录" : metric(run.tool_calls)}
          </strong>
        </div>
        <div>
          <span>规划器调用</span>
          <strong>
            {run.planner_calls == null ? "未记录" : metric(run.planner_calls)}
            <small> 非付费调用证明</small>
          </strong>
        </div>
      </div>
      <Panel
        title="四层校验结果"
        subtitle="每一层回答不同的问题；缺失字段不会被当作失败或通过"
      >
        <ValidationLayers run={run} />
      </Panel>
      <Panel
        title="Agent 流程与证据"
        subtitle="只根据实际节点及报告证据映射，不补造执行进度"
      >
        <WorkflowEvidence run={run} />
      </Panel>
      <div className="detail-grid">
        <div>
          <Panel
            title="执行步骤"
            subtitle={`${run.steps ? steps.length + " 个步骤" : "未记录步骤"} · 展开查看断言与 HTTP 证据`}
            action={<FileCheck2 size={20} className="muted" />}
          >
            {steps.length ? (
              steps.map((step, i) => (
                <StepEvidence
                  step={step}
                  index={i}
                  key={step.step_run_id || i}
                />
              ))
            ) : (
              <Empty title="没有执行步骤">
                <p>可能在预检、授权或意图验证阶段停止，请查看诊断。</p>
              </Empty>
            )}
          </Panel>
          <Panel
            title="失败后的只读调查"
            subtitle="调查不会修改原始业务测试结果"
          >
            <div className="section-line">
              <Badge>
                {run.investigations
                  ? investigations.length + " 次调查"
                  : "未记录调查"}
              </Badge>
              <span className="caption">
                {investigationRequests} 次调查 HTTP 尝试
              </span>
            </div>
            {investigations.length ? (
              investigations.map((item, i) => (
                <div className="investigation" key={i}>
                  <dl>
                    <dt>触发步骤</dt>
                    <dd>{item.parent_step_id}</dd>
                    <dt>批准能力</dt>
                    <dd>{item.capability_id || "未记录"}</dd>
                    <dt>操作 / 角色</dt>
                    <dd>
                      {item.operation_id} / {item.role || "未记录"}
                    </dd>
                    <dt>请求 / 轮次</dt>
                    <dd>
                      {metric(item.requests_used)} / {metric(item.rounds_used)}
                    </dd>
                    <dt>耗时</dt>
                    <dd>{metric(item.elapsed_ms)} ms</dd>
                  </dl>
                  <StepEvidence step={item} index={i} />
                </div>
              ))
            ) : (
              <p className="muted">该报告未记录只读调查。</p>
            )}
            {run.investigation_error && (
              <Notice error>调查诊断：{run.investigation_error}</Notice>
            )}
          </Panel>
        </div>
        <aside>
          <Panel title="诊断与运行信息">
            <dl className="stacked">
              <dt>失败 / 结束原因</dt>
              <dd>{run.reason || "未记录"}</dd>
              <dt>诊断码</dt>
              <dd>{run.diagnostic?.code || "未记录"}</dd>
              <dt>诊断层</dt>
              <dd>{run.diagnostic?.layer || "未记录"}</dd>
              <dt>分析码</dt>
              <dd>{run.analysis_code || "未记录"}</dd>
              <dt>校验问题</dt>
              <dd>{run.validation_issue || "未记录"}</dd>
              <dt>批准策略 ID</dt>
              <dd>{run.selected_policy_ids?.join(" · ") || "未记录"}</dd>
            </dl>
          </Panel>
          <Panel title="模型与性能" subtitle="仅展示报告记录的 ModelCallStats">
            {run.model_call ? (
              <>
                <dl className="stacked">
                  <dt>模型名称</dt>
                  <dd>{run.model_call.model_name || "未记录"}</dd>
                  <dt>模型调用次数</dt>
                  <dd>{metric(run.model_call.calls)}</dd>
                  <dt>Input / Output Tokens</dt>
                  <dd>
                    {metric(run.model_call.input_tokens)} /{" "}
                    {metric(run.model_call.output_tokens)}
                  </dd>
                  <dt>Total Tokens</dt>
                  <dd>{metric(run.model_call.total_tokens)}</dd>
                  <dt>模型耗时</dt>
                  <dd>{metric(run.model_call.elapsed_ms)} ms</dd>
                  <dt>固定错误分类</dt>
                  <dd>
                    {run.model_call.diagnostic?.code ||
                      run.model_call.reason ||
                      "未记录"}
                  </dd>
                </dl>
                <p className="caption">
                  Token 为 null / 缺失时显示未知，不从字节数估算。
                </p>
              </>
            ) : (
              <p className="muted">
                未记录模型统计。Planner 节点或 FakeLLM 调用不代表真实模型用量。
              </p>
            )}
          </Panel>
          <div className="evidence-note">
            <FileCheck2 size={20} />
            <h3>只读、脱敏、可追溯</h3>
            <p>
              批准策略与断言不由页面修改。报告不包含原始 HTTP 正文、JWT
              或模型密钥。
            </p>
          </div>
        </aside>
      </div>
    </>
  );
}
