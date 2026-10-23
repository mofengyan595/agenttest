import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { taskApi, taskTerminal, type GoalTask } from "../api/tasks";
import { Badge, Notice, PageTitle, Panel } from "../components/ui";

const states: Record<string, string> = {
  QUEUED: "等待执行",
  RUNNING: "正在执行",
  WAITING_REVIEW: "等待策略要求的审阅",
  FINISHED: "已结束",
  NEEDS_REVIEW: "需要澄清",
  CANCELLED: "已取消",
  INTERRUPTED: "已中断",
};

export function GoalTasks() {
  const [csrf, setCsrf] = useState("");
  const [projects, setProjects] = useState<{ id: string; identity: string }[]>(
    [],
  );
  const [project, setProject] = useState("");
  const [text, setText] = useState("");
  const [suite, setSuite] = useState(false);
  const [tasks, setTasks] = useState<GoalTask[]>([]);
  const [selected, setSelected] = useState<GoalTask | null>(null);
  const [parent, setParent] = useState<string | undefined>();
  const [replacePending, setReplacePending] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [phase, setPhase] = useState("");
  const submission = useRef<{ key: string; id: string } | null>(null);

  function submit() {
    const key = JSON.stringify([project, text, suite, parent, replacePending]);
    if (submission.current?.key !== key)
      submission.current = {
        key,
        id: crypto.randomUUID().replaceAll("-", ""),
      };
    return taskApi.submit(
      csrf,
      project,
      text,
      suite,
      parent,
      replacePending,
      submission.current.id,
    );
  }

  useEffect(() => {
    setPhase("");
    if (
      !selected ||
      taskTerminal(selected.status) ||
      typeof EventSource === "undefined"
    )
      return;
    const source = new EventSource(`/api/tasks/${selected.id}/events`);
    const phases: Record<string, string> = {
      QUEUED: "等待执行",
      PLANNING: "生成计划",
      VALIDATING: "检查授权与完整性",
      EXECUTING: "验证接口",
      REPORT: "保存报告",
      FINISHED: "已结束",
    };
    source.onmessage = (event) => {
      try {
        const value = JSON.parse(event.data) as { phase: string };
        if (phases[value.phase]) setPhase(phases[value.phase]);
      } catch {
        /* Polling stays available. */
      }
    };
    return () => source.close();
  }, [selected?.id, selected?.status]);

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const session = await taskApi.session();
        if (!active) return;
        const [catalog, history] = await Promise.all([
          taskApi.projects(),
          taskApi.list(),
        ]);
        if (active) {
          setCsrf(session.csrf_token);
          setProjects(catalog.projects);
          setProject(catalog.projects[0]?.id || "");
          setTasks(history.tasks);
        }
      } catch (e) {
        if (active)
          setError(e instanceof Error ? e.message : "任务服务未连接。");
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (
      !selected ||
      taskTerminal(selected.status) ||
      selected.status === "WAITING_REVIEW"
    )
      return;
    let active = true;
    const timer = setInterval(() => {
      taskApi
        .read(selected.id)
        .then((task) => {
          if (!active) return;
          setSelected(task);
          setTasks((items) => items.map((t) => (t.id === task.id ? task : t)));
        })
        .catch(() => {
          if (active) setError("任务进度暂时不可读，请刷新历史记录。");
        });
    }, 1000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [selected]);

  async function act(action: () => Promise<GoalTask>) {
    setBusy(true);
    setError("");
    try {
      const task = await action();
      setSelected(task);
      setTasks((items) => [task, ...items.filter((t) => t.id !== task.id)]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "任务操作未完成。");
    } finally {
      setBusy(false);
    }
  }

  async function download(runId: string) {
    if (!selected) return;
    setBusy(true);
    setError("");
    try {
      const data = await taskApi.export(csrf, selected.id, runId);
      const url = URL.createObjectURL(data);
      const link = document.createElement("a");
      link.href = url;
      link.download = "agenttest-regression.zip";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch {
      setError(
        "回归包仅可从配置未变化的已通过任务导出。服务重启后请通过 CLI 导出。",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageTitle
        eyebrow="WORKSPACE / 受控测试任务"
        title="描述需求，查看计划与证据。"
        description="复用已注册项目的授权与断言，支持单接口、跨接口与项目范围测试。"
      />
      <div className="work-grid">
        <div>
          <Panel
            title="发起测试"
            subtitle={
              parent ? "补充生成新任务，原计划与证据保留" : "使用项目已有授权"
            }
          >
            <label htmlFor="goal-project">已注册项目</label>
            <select
              id="goal-project"
              value={project}
              disabled={busy || !!parent}
              onChange={(e) => setProject(e.target.value)}
            >
              <option value="">请选择项目</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.id}
                </option>
              ))}
            </select>
            <label htmlFor="goal-demand">测试需求</label>
            <textarea
              id="goal-demand"
              className="task-input"
              maxLength={2000}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="例如：测试创建工单接口。"
            />
            <label className="task-option">
              <input
                type="checkbox"
                checked={suite}
                onChange={(e) => setSuite(e.target.checked)}
              />
              包含 Schema 用例候选
            </label>
            <Notice>
              已有授权范围内自动执行。未知预期列为待审；取消不会自动回滚已发送的请求。
            </Notice>
            <div className="button-row">
              <button
                className="primary"
                disabled={!csrf || !project || !text.trim() || busy}
                onClick={() => void act(submit)}
              >
                {busy ? "正在提交…" : parent ? "提交补充需求" : "开始测试"}
              </button>
              {(parent || selected) && (
                <button
                  onClick={() => {
                    setParent(undefined);
                    setReplacePending(false);
                    setText("");
                    submission.current = null;
                  }}
                >
                  新建独立任务
                </button>
              )}
            </div>
            {error && <Notice error>{error}</Notice>}
          </Panel>
          <Panel title="任务历史" subtitle="仅显示当前本地会话的任务">
            <div className="task-history">
              {tasks.map((task) => (
                <button
                  key={task.id}
                  onClick={() => setSelected(task)}
                  aria-pressed={selected?.id === task.id}
                >
                  <strong>{task.project_id}</strong>
                  <span>{states[task.status] || task.status}</span>
                  <small>{task.id.slice(0, 12)}</small>
                </button>
              ))}
            </div>
            {!tasks.length && <p className="muted">尚无测试任务。</p>}
          </Panel>
        </div>
        <Panel
          title="计划、预算与结果"
          action={
            selected && (
              <Badge>{states[selected.status] || selected.status}</Badge>
            )
          }
        >
          {!selected ? (
            <p className="muted">提交或选择任务后查看执行范围与结果。</p>
          ) : (
            <>
              <p>
                项目 {selected.project_id} · 计划版本 {selected.plan_version}
              </p>
              {phase && <p role="status">当前进度：{phase}</p>}
              <p className="caption">任务 {selected.id}</p>
              {selected.summary.requested_goals && (
                <p>目标：{selected.summary.requested_goals.join("、")}</p>
              )}
              {selected.summary.required_steps !== undefined && (
                <p>必要步骤：{selected.summary.required_steps}</p>
              )}
              {selected.summary.required_plan?.map((step) => (
                <div className="task-case" key={step.key}>
                  <strong>{step.operation_id}</strong>
                  <span>
                    角色 {step.role || "匿名"} · {step.policy_id}
                  </span>
                </div>
              ))}
              {selected.summary.budget && (
                <p>
                  总预算：{selected.summary.budget.max_requests} 请求 ·
                  {selected.summary.budget.max_model_calls} 模型调用 ·{" "}
                  {selected.summary.budget.max_total_tokens} Token
                </p>
              )}
              {selected.summary.budget_preview && (
                <div className="task-case" aria-label="分组预算预览">
                  <strong>分组预算</strong>
                  <p>
                    必需业务请求{" "}
                    {selected.summary.budget_preview.required_business_requests}
                    {" · "}至少{" "}
                    {selected.summary.budget_preview.required_planning_calls}{" "}
                    次规划
                  </p>
                  <p>
                    可选只读额度{" "}
                    {selected.summary.budget_preview.optional_readonly_requests}
                    {" · "}可选模型额度{" "}
                    {selected.summary.budget_preview.optional_model_calls}
                  </p>
                  <p>
                    可执行{" "}
                    {selected.summary.budget_preview.executable_groups.length}{" "}
                    组{" · "}排除{" "}
                    {selected.summary.budget_preview.excluded_groups.length} 组
                  </p>
                  <p>
                    {selected.summary.budget_preview
                      .complete_project_within_budget
                      ? "完整范围在预算上界内。"
                      : "预算或待审项限制完整执行。"}
                  </p>
                  <small>
                    规划 Token 保留上界{" "}
                    {selected.summary.budget_preview.planning_token_upper_bound}
                    ； 实际用量另计，可选调查不占用后续业务组的必需额度。
                  </small>
                </div>
              )}
              {selected.summary.goal_spec && (
                <Notice>
                  <strong>模型目标草案：语义待核验</strong>
                  <p>结构校验不代表业务语义已经确认；当前没有据此执行接口。</p>
                  {selected.summary.goal_spec.proposed_goals?.map((goal, i) => (
                    <p key={i}>
                      建议目标 {goal.goal_id} · 原文位置 {goal.start}–{goal.end}
                    </p>
                  ))}
                  {!!selected.summary.goal_spec.unaccounted_characters && (
                    <p>
                      仍有 {selected.summary.goal_spec.unaccounted_characters}{" "}
                      个非空白字符未被解释。
                    </p>
                  )}
                  <p>
                    {selected.summary.goal_spec.question ||
                      "请补充明确的接口行为和已批准预期。"}
                  </p>
                </Notice>
              )}
              {selected.summary.question && (
                <Notice>{selected.summary.question}</Notice>
              )}
              {selected.summary.reason && (
                <p className="caption">
                  待审或停止原因：{selected.summary.reason}
                </p>
              )}
              {selected.summary.outcome && (
                <p>
                  业务结果：<strong>{selected.summary.outcome}</strong> ·
                  完整需求
                  {selected.summary.task_satisfied ? "已满足" : "未证实满足"}
                </p>
              )}
              {selected.summary.cases?.map((item) => (
                <div className="task-case" key={item.case_id}>
                  <strong>{item.operation_id || "待澄清目标"}</strong>
                  <span>{item.outcome || item.status}</span>
                  {(!!item.approved_negative_steps ||
                    item.kinds?.some((kind) =>
                      kind.startsWith("approved_negative:"),
                    )) && (
                    <p>
                      {item.approved_negative_steps
                        ? `批准负例 · 按预期拒绝 ${item.negative_rejections_passed ?? 0}/${item.approved_negative_steps} 个已构造输入`
                        : "批准负例 · 尚无负例验证结果"}
                    </p>
                  )}
                  {item.status === "NOT_EXECUTED" && (
                    <p>本组未执行，保留在覆盖分母中。</p>
                  )}
                  {item.reason && <small>{item.reason}</small>}
                  {item.question && <p>{item.question}</p>}
                  {item.run_id && (
                    <Link className="inline-link" to={`/runs/${item.run_id}`}>
                      查看分层证据
                    </Link>
                  )}
                  {item.run_id && item.task_satisfied && (
                    <button
                      disabled={busy}
                      onClick={() => void download(item.run_id!)}
                    >
                      导出该用例 pytest
                    </button>
                  )}
                </div>
              ))}
              {selected.summary.coverage && (
                <div>
                  <p>
                    已执行接口{" "}
                    {selected.summary.coverage.executed_operations || 0}/{" "}
                    {selected.summary.coverage.operation_denominator} ·
                    已执行场景{" "}
                    {selected.summary.coverage.executed_scenarios || 0}/{" "}
                    {selected.summary.coverage.scenario_denominator}
                  </p>
                  {selected.summary.coverage.planned_scenarios !==
                    undefined && (
                    <p>
                      计划合格场景 {selected.summary.coverage.planned_scenarios}
                      /{selected.summary.coverage.scenario_denominator} ·
                      断言通过场景{" "}
                      {selected.summary.coverage.assertion_passed_scenarios ??
                        0}
                      /{selected.summary.coverage.scenario_denominator} · 未执行{" "}
                      {selected.summary.coverage.not_executed ?? 0}
                    </p>
                  )}
                  {selected.summary.coverage.assertions_evaluated !==
                    undefined && (
                    <p>
                      实际断言通过{" "}
                      {selected.summary.coverage.assertions_passed ?? 0}/
                      {selected.summary.coverage.assertions_evaluated}
                    </p>
                  )}
                </div>
              )}
              {selected.summary.context_usage && (
                <p>
                  累计用量：{selected.summary.context_usage.requests} 请求 ·
                  {selected.summary.context_usage.model_calls} 模型调用 ·
                  {selected.summary.context_usage.usage_known
                    ? `${selected.summary.context_usage.total_tokens} Token`
                    : "Token 未知，后续停止"}
                </p>
              )}
              {selected.summary.run_id && (
                <Link
                  className="inline-link"
                  to={`/runs/${selected.summary.run_id}`}
                >
                  查看业务、模型与规则证据
                </Link>
              )}
              <div className="button-row">
                {selected.summary.model_source === "SIMULATED" && (
                  <span className="caption">
                    本次为模拟规划，未调用付费模型。
                  </span>
                )}
                {selected.status === "FINISHED" &&
                  selected.summary.task_satisfied &&
                  selected.summary.run_id && (
                    <button
                      disabled={busy}
                      onClick={() => void download(selected.summary.run_id!)}
                    >
                      导出 pytest 回归包
                    </button>
                  )}
                {selected.status === "WAITING_REVIEW" && (
                  <button
                    disabled={busy}
                    onClick={() =>
                      void act(() => taskApi.start(csrf, selected))
                    }
                  >
                    确认此计划并执行
                  </button>
                )}
                {!taskTerminal(selected.status) && (
                  <button
                    disabled={busy}
                    onClick={() =>
                      void act(() => taskApi.cancel(csrf, selected.id))
                    }
                  >
                    取消后续动作
                  </button>
                )}
                {taskTerminal(selected.status) && (
                  <button
                    onClick={() => {
                      setParent(selected.id);
                      setProject(selected.project_id);
                      setReplacePending(selected.status === "NEEDS_REVIEW");
                      setText("");
                    }}
                  >
                    {selected.status === "NEEDS_REVIEW"
                      ? "补充澄清"
                      : "补充测试需求"}
                  </button>
                )}
              </div>
            </>
          )}
        </Panel>
      </div>
    </>
  );
}
