import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import {
  intakeApi,
  taskApi,
  taskTerminal,
  type GoalTask,
  type ProjectChoice,
  type IntakeCatalog,
  type SavedTest,
  type TaskEvent,
} from "../api/tasks";
import { outcomes } from "../status";
import type { Outcome } from "../api/types";
import { Badge, Notice, PageTitle, Panel } from "../components/ui";
import { ResultBrief } from "../components/ResultBrief";
import { ProductResult } from "../components/ProductResult";
import { LifecycleStatus } from "../components/LifecycleStatus";

const phases: Record<string, string> = {
  QUEUED: "已进入队列",
  RUNNING: "正在处理任务",
  WAITING_REVIEW: "等待项目策略要求的审阅",
  PLANNING: "正在安排测试步骤",
  VALIDATING: "正在检查计划、授权与需求范围",
  EXECUTING: "正在执行接口并检查结果",
  REPORT: "正在整理结果",
  FINISHED: "流程已结束",
  CANCELLED: "已取消后续动作",
  INTERRUPTED: "执行已中断",
  NEEDS_REVIEW: "需要补充信息",
};

const states: Record<string, string> = {
  QUEUED: "等待执行",
  RUNNING: "正在执行",
  WAITING_REVIEW: "等待策略要求的审阅",
  FINISHED: "已结束",
  NEEDS_REVIEW: "需要澄清",
  CANCELLED: "已取消",
  INTERRUPTED: "已中断",
};

export function GoalTasks({
  recordsOnly = false,
  savedOnly = false,
}: {
  recordsOnly?: boolean;
  savedOnly?: boolean;
}) {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const [savedTests, setSavedTests] = useState<SavedTest[]>([]);
  const [savedLoading, setSavedLoading] = useState(true);
  const [savedError, setSavedError] = useState("");
  const [timeline, setTimeline] = useState<TaskEvent[]>([]);
  const resultRef = useRef<HTMLDivElement>(null);
  const [runtime, setRuntime] = useState<IntakeCatalog | null>(null);
  const [csrf, setCsrf] = useState("");
  const [projects, setProjects] = useState<ProjectChoice[]>([]);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [project, setProject] = useState("");
  const [text, setText] = useState("");
  const [suite, setSuite] = useState(false);
  const [tasks, setTasks] = useState<GoalTask[]>([]);
  const [selected, setSelected] = useState<GoalTask | null>(null);
  const [resultLoading, setResultLoading] = useState(!!params.get("task"));
  const [parent, setParent] = useState<string | undefined>();
  const [replacePending, setReplacePending] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [phase, setPhase] = useState("");
  const submission = useRef<{ key: string; id: string } | null>(null);
  const repeatSubmission = useRef<Record<string, string>>({});
  const assisted =
    projects.find((p) => p.id === project)?.testing_mode === "MODEL_DESIGNED";
  const missingNegativeChecks = selected?.summary.cases?.filter((c) =>
    c.kinds?.includes("negative") && c.approved_negative_steps === 0).length || 0;

  useEffect(() => {
    if (selected && window.matchMedia?.("(max-width: 900px)").matches) {
      resultRef.current?.scrollIntoView({ behavior: "auto", block: "start" });
    }
  }, [selected?.id]);

  useEffect(() => {
    setTimeline([]);
    if (!selected) return;
    let active = true;
    taskApi
      .timeline(selected.id)
      .then((result) => {
        if (active && Array.isArray(result.events)) setTimeline(result.events);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [selected?.id, selected?.status]);

  useEffect(() => {
    if (!selected || !taskTerminal(selected.status) ||
        selected.summary.context_usage?.usage_known !== false) return;
    let active = true;
    intakeApi.read().then((info) => {
      if (active && info.profiles) setRuntime(info);
    }).catch(() => {});
    return () => { active = false; };
  }, [selected?.id, selected?.status]);

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
    source.onmessage = (event) => {
      try {
        const value = JSON.parse(event.data) as TaskEvent;
        if (phases[value.phase]) setPhase(phases[value.phase]);
        setTimeline((items) =>
          items.some((i) => i.sequence === value.sequence)
            ? items
            : [...items, value],
        );
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
        setCsrf(session.csrf_token);
        const requestedId = params.get("task");
        if (requestedId) {
          try {
            const requested = await taskApi.read(requestedId);
            if (!active) return;
            setSelected(requested);
            setProject(params.get("project") || requested.project_id);
            if (params.get("clarify") === "1") {
              setParent(requested.id);
              setReplacePending(requested.status === "NEEDS_REVIEW");
            }
          } catch (e) {
            if (active) setError(e instanceof Error ? e.message :
              "当前本地会话无法读取此任务。请从测试记录选择任务；系统不会重放原请求。");
          } finally {
            if (active) setResultLoading(false);
          }
        }
        const assetsRequest = taskApi.saved().then((assets) => {
          if (active && Array.isArray(assets.tests)) setSavedTests(assets.tests);
        }).catch(() => {
          if (active) setSavedError("已保存测试暂时无法读取，请刷新重试。");
        }).finally(() => {
          if (active) setSavedLoading(false);
        });
        if (savedOnly) await assetsRequest;
        if (!active) return;
        const historyRequest = taskApi.list().then((history) => {
          if (!active) return;
          setTasks(history.tasks);

        }).catch((e) => {
          if (active) setError(e instanceof Error ? e.message : "测试记录暂时无法读取。");
        });
        const catalogRequest = taskApi.projects().then(async (catalog) => {
          if (!active) return;
          setProjects(catalog.projects);
          setProject((current) => current || params.get("project") ||
            catalog.projects.find((p) => p.ready !== false)?.id || "");
          setCatalogLoading(false);
          try {
            const info = await intakeApi.read();
            if (active && info.profiles) setRuntime(info);
          } catch {
            /* Basic registered entries remain usable. */
          }
        }).catch((e) => {
          if (active) setError((current) => current ||
            (e instanceof Error ? e.message : "项目列表暂时无法读取。已有测试记录仍可查看。"));
        }).finally(() => {
          if (active) setCatalogLoading(false);
        });
        await Promise.all([assetsRequest, historyRequest, catalogRequest]);
      } catch (e) {
        if (active)
          setError(e instanceof Error ? e.message : "任务服务未连接。");
      } finally {
        if (active) {
          setCatalogLoading(false);
          setResultLoading(false);
        }
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
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const task = await action();
      setSelected(task);
      setParams({ project: task.project_id, task: task.id }, { replace: true });
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
      const assets = await taskApi.saved();
      if (Array.isArray(assets.tests)) setSavedTests(assets.tests);
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : "回归包仅可从配置未变化的已通过任务导出。请重新检查项目配置及详细报告。",
      );
    } finally {
      setBusy(false);
    }
  }

  async function answerPermission(question: string, status: number) {
    if (!selected || busy) return;
    setBusy(true);
    setError("");
    try {
      const catalog = await intakeApi.read();
      const current = catalog.projects.find(
        (p) => p.id === selected.project_id,
      );
      if (!current) throw new Error("项目已变化，请重新检查。");
      await intakeApi.answerPermission(
        csrf,
        selected.id,
        question,
        status,
        current.version,
      );
      setRuntime(await intakeApi.read());
      setProjects((await taskApi.projects()).projects);
      setParent(selected.id);
      setProject(selected.project_id);
      setText("");
      setReplacePending(false);
      submission.current = null;
      setSelected(await taskApi.read(selected.id));
      setNotice(
        "规则已保存。原结果保持不变；点击提交补充需求，会按新规则安排独立测试。",
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "规则未能保存。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageTitle
        eyebrow={savedOnly ? "已保存测试" : recordsOnly ? "结果" : "当前测试"}
        title={
          savedOnly ? "已保存测试" : recordsOnly ? "测试结果" : "开始一轮测试"
        }
        description={savedOnly ? "按保存的检查规则再次运行。" : recordsOnly
          ? "查看已执行的测试、问题和未覆盖部分。"
          : "选择项目即可开始，也可以交代这次想重点检查的内容。"}
      />
      {runtime && runtime.model_source !== "CONFIGURED_MODEL" && (
        <Notice>
          规划来源：
          {runtime.model_source === "SIMULATED"
            ? "模拟模型（只验证工程流程）"
            : runtime.model_source === "CONFIGURED_MODEL"
              ? "真实模型（服务端配置）"
              : runtime.model_source === "UNAVAILABLE"
                ? "模型未配置，请操作员设置模型环境后重启服务"
                : "由服务端规划器决定，以执行记录为准"}{" "}
          · 执行目标：
          {runtime.execution_target === "SYNTHETIC_MEMORY"
            ? "内存模拟目标，每次任务隔离"
            : "服务端授权测试环境"}
        </Notice>
      )}
      {error && (
        <Notice error>
          {error}
          <button onClick={() => window.location.reload()}>重新连接</button>
        </Notice>
      )}
      {notice && <Notice>{notice}</Notice>}
      {runtime?.model_usage_blocked && (
        <Notice error>
          上一次模型请求未返回可核对的用量，已停止新的模型调用。请先核对服务端记录。
          现有结果和受支持的无模型固定测试仍可查看、复测；系统不会自动重试。
        </Notice>
      )}
      {!savedOnly && !recordsOnly && !catalogLoading && !projects.length && csrf && (
        <Notice>
          还没有可选项目。请先<Link to="/">接入项目</Link>
          ，准备文档、环境、身份和业务预期。
        </Notice>
      )}
      <div className={`work-grid ${selected ? "has-result" : ""}`}>
        <div>
          {savedOnly && (
            <Panel
              title="已保存的固定测试"
              subtitle="按保存时的检查规则独立运行，模型禁用；每项显示运行条件。"
            >
              {savedLoading && <p>正在读取已保存测试…</p>}
              {savedError && <Notice error>{savedError}</Notice>}
              {!savedLoading && !savedError && !savedTests.length && (
                <p>
                  尚未保存固定测试。先完成受支持测试，再在结果中下载并保存回归包。
                </p>
              )}
              {savedTests.map((asset) => (
                <div className="task-case" key={asset.run_id}>
                  <strong>
                    {asset.title || asset.project_name || projects.find((p) => p.id === asset.project_id)?.name ||
                      "已保存测试"}
                  </strong>
                  {asset.title && asset.project_name && asset.title !== asset.project_name &&
                    <p className="caption">{asset.project_name}</p>}
                  <p>{asset.reason}</p>
                  <button
                    disabled={busy || !csrf || !asset.available}
                    onClick={() => {
                      const key = asset.run_id;
                      repeatSubmission.current[key] ||= crypto
                        .randomUUID()
                        .replaceAll("-", "");
                      void act(() =>
                        taskApi
                          .repeat(csrf, asset, repeatSubmission.current[key])
                          .then((task) => {
                            delete repeatSubmission.current[key];
                            return task;
                          }),
                      );
                    }}
                  >
                    再次运行固定测试
                  </button>
                  <details>
                    <summary>来源记录</summary>
                    <code>{asset.run_id}</code>
                  </details>
                </div>
              ))}
            </Panel>
          )}
          {!recordsOnly && (
            <>
              <Panel
                title="发起测试"
                subtitle={
                  parent
                    ? "补充生成新任务，原计划与证据保留"
                    : "使用项目已有授权"
                }
              >
                <label htmlFor="goal-project">选择项目</label>
                <select
                  id="goal-project"
                  value={project}
                  disabled={busy || !!parent}
                  onChange={(e) => setProject(e.target.value)}
                >
                  <option value="">请选择项目</option>
                  {projects.map((p) => (
                    <option
                      key={p.id}
                      value={p.id}
                      disabled={p.ready === false}
                    >
                      {p.name || p.id}
                      {p.ready === false ? "（待补充）" : ""}
                    </option>
                  ))}
                </select>
                <label htmlFor="goal-demand">
                  {assisted ? "测试重点（可选）" : "测试需求"}
                </label>
                <textarea
                  id="goal-demand"
                  className="task-input"
                  maxLength={2000}
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  placeholder={
                    projects.find((p) => p.id === project)?.profile
                      ?.examples?.[0] || "选择项目后，描述你想验证的接口行为。"
                  }
                />
                {projects
                  .find((p) => p.id === project)
                  ?.profile?.examples?.map((example) => (
                    <button
                      className="example-chip"
                      key={example}
                      disabled={busy}
                      onClick={() => setText(example)}
                    >
                      {example}
                    </button>
                  ))}
                {projects.find((p) => p.id === project)?.ready === false && (
                  <Notice error>
                    该项目尚未准备完成，请前往项目页继续配置。
                  </Notice>
                )}
                <details>
                  <summary>更多测试范围</summary>
                  <label className="task-option">
                    <input
                      type="checkbox"
                      checked={suite}
                      onChange={(e) => setSuite(e.target.checked)}
                    />
                    同时检查已支持的参数边界与批准负例
                  </label>
                </details>
                {selected && !taskTerminal(selected.status) && <Notice>
                  已有授权范围内自动执行。未知预期列为待审；取消不会自动回滚已发送的请求。
                </Notice>}
                <div className="button-row">
                  <button
                    className="primary"
                    disabled={
                      !csrf ||
                      !project ||
                      (!assisted && !text.trim()) ||
                      busy ||
                      (!!selected && !taskTerminal(selected.status)) ||
                      projects.find((p) => p.id === project)?.ready === false ||
                      runtime?.model_source === "UNAVAILABLE" ||
                      runtime?.model_usage_blocked
                    }
                    onClick={() => void act(submit)}
                  >
                    {busy
                      ? "正在提交…"
                      : parent
                        ? "提交补充需求"
                        : assisted
                          ? "开始一轮测试"
                          : "开始测试"}
                  </button>
                  {(parent || selected) && (
                    <button
                      onClick={() => {
                        setParent(undefined);
                        setReplacePending(false);
                        setText("");
                        submission.current = null;
                        setSelected(null);
                        setParams(project ? { project } : {}, {
                          replace: true,
                        });
                      }}
                    >
                      新建独立任务
                    </button>
                  )}
                </div>
              </Panel>
              <Link className="inline-link" to="/runs">
                查看测试记录与可复用资产 →
              </Link>
            </>
          )}
          {!savedOnly && (recordsOnly || tasks.length > 0) && (
            <Panel title="任务历史" subtitle="保存在当前浏览器工作空间的测试记录">
              <div className="task-history">
                {tasks.map((task) => (
                  <button
                    key={task.id}
                    onClick={() => {
                      setSelected(task);
                      setParams({ task: task.id }, { replace: true });
                    }}
                    aria-pressed={selected?.id === task.id}
                  >
                    <strong>
                      {projects.find((p) => p.id === task.project_id)?.name ||
                        "接口测试记录"}
                    </strong>
                    <span>{states[task.status] || task.status}</span>
                    {task.summary.display_title && (
                      <span>{task.summary.display_title}</span>
                    )}
                    <small>
                      {new Date(task.created_at * 1000).toLocaleString("zh-CN")}
                    </small>
                  </button>
                ))}
              </div>
              {!tasks.length && <p className="muted">尚无测试任务。</p>}
            </Panel>
          )}
        </div>
        <div ref={resultRef} className="current-result">
          <Panel
            title="执行过程与测试结论"
            action={
              selected && (
                <Badge>{states[selected.status] || selected.status}</Badge>
              )
            }
          >
            {!selected ? (
              <p className="muted" role={resultLoading ? "status" : undefined}>
                {resultLoading ? "正在读取本轮结果…" : "提交或选择任务后查看执行范围与结果。"}
              </p>
            ) : (
              <>
                <div
                  className={`result-summary ${
                    selected.summary.mode === "MODEL_DESIGNED" &&
                    taskTerminal(selected.status) &&
                    (selected.summary.complete !== true || missingNegativeChecks > 0 ||
                      selected.summary.checks_passed === false)
                      ? "needs-attention"
                      : ""
                  }`}
                  role="status"
                >
                  {!taskTerminal(selected.status) && phase && <p>{phase}</p>}
                  <h3>
                    {!taskTerminal(selected.status)
                      ? states[selected.status] || selected.status
                      : selected.summary.mode === "MODEL_DESIGNED"
                        ? selected.summary.complete && missingNegativeChecks === 0
                          ? selected.summary.checks_passed === false
                            ? "本轮已执行，发现不符合预期的结果"
                            : "本轮方案执行完成"
                          : "本次未全部完成"
                        : selected.summary.mode === "FIXED_REGRESSION"
                          ? selected.summary.fixed_regression_passed
                            ? "固定回归检查通过"
                            : "固定回归未通过或未完成"
                          : selected.summary.complete === true ||
                              selected.summary.task_satisfied === true
                            ? "本次需求已完成"
                            : selected.status === "NEEDS_REVIEW"
                              ? "需要补充信息，需求尚未完成"
                              : selected.status === "CANCELLED"
                                ? "已取消后续动作"
                                : selected.status === "INTERRUPTED"
                                  ? "执行中断，结果尚未确认"
                                  : "本次需求未全部完成"}
                  </h3>
                  <p>
                    {selected.summary.mode === "MODEL_DESIGNED"
                      ? `${selected.summary.cases?.filter((c) => c.outcome === "ASSERTION_FAILED").length || 0} 个场景不符合规则，${selected.summary.questions?.filter((q) => !q.confirmed_statuses?.length).length || 0} 项信息待补充。`
                      : selected.summary.mode === "FIXED_REGRESSION"
                        ? "本次独立执行已保存的固定计划与原检查规则，模型已禁用。"
                        : selected.summary.complete === true ||
                            selected.summary.task_satisfied === true
                          ? "本次已确认的测试范围均已完成，相关检查通过。"
                          : selected.summary.outcome
                            ? outcomes[selected.summary.outcome as Outcome]
                                ?.description || "请查看详细记录。"
                            : selected.summary.complete === false
                              ? "部分检查未通过或未执行；下方保留全部范围与原因。"
                              : "以实际执行记录为准，流程结束不等于整个需求通过。"}
                  </p>
                  {missingNegativeChecks > 0 && <p>
                    {missingNegativeChecks} 项方案标为异常场景，但记录中没有实际受控异常检查，不能算作完成。
                  </p>}
                  {selected.summary.mode === "FIXED_REGRESSION" &&
                    <LifecycleStatus resources={selected.summary.resource_lifecycle} />}
                  {selected.summary.outcome === "ASSERTION_FAILED" && (
                    <p>
                      发现响应与已确认预期不一致。请查看业务证据中的实际响应、预期值和请求记录，再定位问题。
                    </p>
                  )}
                  {selected.status === "INTERRUPTED" && (
                    <p>
                      请核对服务、账号和项目版本。已发送请求可能完成，系统不会自动重试或重放写入。
                    </p>
                  )}
                  {selected.status === "CANCELLED" && (
                    <p>已发送请求可能完成；取消不会回滚或重新发送请求。</p>
                  )}
                  {selected.summary.execution_target && selected.summary.mode !== "MODEL_DESIGNED" && (
                    <p>
                      执行目标：
                      {selected.summary.execution_target === "SYNTHETIC_MEMORY"
                        ? "内存模拟目标"
                        : "授权测试环境"}
                    </p>
                  )}
                  {selected.summary.model_source && selected.summary.mode !== "MODEL_DESIGNED" && (
                    <p>
                      规划来源：
                      {selected.summary.model_source === "DISABLED"
                        ? "已保存固定计划，模型禁用"
                        : selected.summary.model_source === "SIMULATED"
                          ? "模拟模型"
                          : "真实模型"}
                    </p>
                  )}
                </div>
                {selected.summary.mode === "MODEL_DESIGNED" && (
                  <ProductResult
                    task={selected}
                    busy={busy}
                    save={(run) => void download(run)}
                    answer={(q, status) => void answerPermission(q, status)}
                    chooseInput={(label, value) => {
                      setParent(selected.id);
                      setProject(selected.project_id);
                      setText(`${label} 明确为“${value}”。其余要求沿用上一轮；请安排尚未完成的检查。`);
                      setReplacePending(false);
                      submission.current = null;
                      setNotice("已填入补充信息，点击提交补充需求后开始新的独立测试。");
                    }}
                  />
                )}
                {timeline.length > 0 && (
                  <details>
                    <summary>实际执行过程（{timeline.length} 条记录）</summary>
                    <ol className="timeline">
                      {timeline.map((e) => (
                        <li key={e.sequence}>
                          {phases[e.phase] || e.phase}
                          {e.run_id && (
                            <small> · 本记录请求数 {e.requests}</small>
                          )}
                        </li>
                      ))}
                    </ol>
                  </details>
                )}
                {selected.summary.run_id && (
                  <ResultBrief id={selected.summary.run_id} />
                )}
                <details open={selected.status === "WAITING_REVIEW"}>
                  <summary>本次测试范围与预算</summary>
                  <p>
                    项目 {selected.project_id} · 计划版本{" "}
                    {selected.plan_version}
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
                        {
                          selected.summary.budget_preview
                            .required_business_requests
                        }
                        {" · "}至少{" "}
                        {
                          selected.summary.budget_preview
                            .required_planning_calls
                        }{" "}
                        次规划
                      </p>
                      <p>
                        可选只读额度{" "}
                        {
                          selected.summary.budget_preview
                            .optional_readonly_requests
                        }
                        {" · "}可选模型额度{" "}
                        {selected.summary.budget_preview.optional_model_calls}
                      </p>
                      {selected.summary.budget_preview.derived_cases !==
                        undefined && (
                        <p>
                          正常结构{" "}
                          {selected.summary.budget_preview.normal_cases ?? 0}
                          {" · "}规则派生{" "}
                          {selected.summary.budget_preview.derived_cases}
                          {" · "}边界或枚举{" "}
                          {selected.summary.budget_preview
                            .boundary_or_enum_cases ?? 0}
                          {" · "}批准负例{" "}
                          {selected.summary.budget_preview
                            .approved_negative_cases ?? 0}
                        </p>
                      )}
                      <p>
                        可执行{" "}
                        {
                          selected.summary.budget_preview.executable_groups
                            .length
                        }{" "}
                        组{" · "}排除{" "}
                        {selected.summary.budget_preview.excluded_groups.length}{" "}
                        组
                      </p>
                      <p>
                        {selected.summary.budget_preview
                          .complete_project_within_budget
                          ? "完整范围在预算上界内。"
                          : "预算或待审项限制完整执行。"}
                      </p>
                      <small>
                        规划 Token 保留上界{" "}
                        {
                          selected.summary.budget_preview
                            .planning_token_upper_bound
                        }
                        ； 实际用量另计，可选调查不占用后续业务组的必需额度。
                      </small>
                    </div>
                  )}
                </details>
                {selected.summary.goal_spec && (
                  <Notice>
                    <strong>模型目标草案：语义待核验</strong>
                    <p>
                      结构校验不代表业务语义已经确认；当前没有据此执行接口。
                    </p>
                    {selected.summary.goal_spec.proposed_goals?.map(
                      (goal, i) => (
                        <p key={i}>
                          建议目标 {goal.goal_id} · 原文位置 {goal.start}–
                          {goal.end}
                        </p>
                      ),
                    )}
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
                  <details>
                    <summary>停止原因详情</summary>
                    {selected.summary.reason}
                  </details>
                )}
                {selected.summary.outcome && (
                  <p>
                    业务结果：
                    <strong>
                      {outcomes[selected.summary.outcome as Outcome]?.label ||
                        selected.summary.outcome}
                    </strong>{" "}
                    · 完整需求
                    {selected.summary.task_satisfied ? "已满足" : "未证实满足"}
                  </p>
                )}
                {selected.summary.mode !== "MODEL_DESIGNED" &&
                selected.summary.cases?.length ? (
                  <details>
                    <summary>
                      逐项检查与未完成原因（{selected.summary.cases.length}）
                    </summary>
                    {selected.summary.cases.map((item) => (
                      <div className="task-case" key={item.case_id}>
                        <strong>{item.operation_id || "待澄清目标"}</strong>
                        <span>
                          {outcomes[item.outcome as Outcome]?.label ||
                            (
                              {
                                NOT_EXECUTED: "未执行",
                                READY: "可以执行",
                                NEEDS_REVIEW: "需要补充信息",
                              } as Record<string, string>
                            )[item.status] ||
                            item.status}
                        </span>
                        {item.plan_source === "DETERMINISTIC_RULE" && (
                          <p>
                            来源：合法基础计划的规则派生，规划模型调用 0 次。
                          </p>
                        )}
                        {item.plan_derivation && (
                          <p>
                            <Link
                              className="inline-link"
                              to={`/runs/${item.plan_derivation.base_run_id}`}
                            >
                              查看基础计划
                            </Link>
                            {" · "}指纹{" "}
                            {item.plan_derivation.base_plan_sha256.slice(0, 12)}
                          </p>
                        )}
                        {item.structural_validation_passed !== undefined && (
                          <p>
                            结构校验
                            {item.structural_validation_passed
                              ? "通过"
                              : "未通过"}
                            {" · "}业务目标
                            {item.task_satisfied ? "已满足" : "未证实满足"}
                          </p>
                        )}
                        {item.base_failure && (
                          <p>基础计划失败：{item.base_failure.diagnostic}</p>
                        )}
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
                        {item.reason && (
                          <details>
                            <summary>未完成原因详情</summary>
                            <small>{item.reason}</small>
                          </details>
                        )}
                        {item.question && <p>{item.question}</p>}
                        {item.run_id && (
                          <Link
                            className="inline-link"
                            to={`/runs/${item.run_id}`}
                          >
                            查看分层证据
                          </Link>
                        )}
                        {item.run_id &&
                          selected.summary.exports?.[item.run_id]
                            ?.available && (
                            <button
                              disabled={busy}
                              onClick={() => void download(item.run_id!)}
                            >
                              导出该用例 pytest
                            </button>
                          )}
                        {item.run_id &&
                          selected.summary.exports?.[item.run_id]?.reason && (
                            <p className="caption">
                              {selected.summary.exports[item.run_id].reason}
                            </p>
                          )}
                      </div>
                    ))}
                  </details>
                ) : null}
                {selected.summary.mode !== "MODEL_DESIGNED" &&
                  selected.summary.coverage && (
                    <div>
                      {!taskTerminal(selected.status) ? (
                        <p>
                          本次范围：
                          {selected.summary.coverage.operation_denominator}{" "}
                          个接口、
                          {selected.summary.coverage.scenario_denominator}{" "}
                          个场景。执行尚未结束，覆盖结果将在完成后汇总。
                        </p>
                      ) : (
                        <>
                          <p>
                            已执行接口{" "}
                            {selected.summary.coverage.executed_operations || 0}
                            / {selected.summary.coverage.operation_denominator}{" "}
                            · 已执行场景{" "}
                            {selected.summary.coverage.executed_scenarios || 0}/{" "}
                            {selected.summary.coverage.scenario_denominator}
                          </p>
                          {selected.summary.coverage.planned_scenarios !==
                            undefined && (
                            <p>
                              计划合格场景{" "}
                              {selected.summary.coverage.planned_scenarios}/
                              {selected.summary.coverage.scenario_denominator} ·
                              断言通过场景{" "}
                              {selected.summary.coverage
                                .assertion_passed_scenarios ?? 0}
                              /{selected.summary.coverage.scenario_denominator}{" "}
                              · 未执行{" "}
                              {selected.summary.coverage.not_executed ?? 0}
                            </p>
                          )}
                          {selected.summary.coverage.assertions_evaluated !==
                            undefined && (
                            <p>
                              实际断言通过{" "}
                              {selected.summary.coverage.assertions_passed ?? 0}
                              /{selected.summary.coverage.assertions_evaluated}
                            </p>
                          )}
                        </>
                      )}
                    </div>
                  )}
                {selected.summary.context_usage && (
                  <details>
                    <summary>请求与模型用量详情</summary>
                    <p>
                      累计用量：{selected.summary.context_usage.requests} 请求 ·
                      {selected.summary.context_usage.model_calls} 模型调用 ·
                      {selected.summary.context_usage.usage_known
                        ? `${selected.summary.context_usage.total_tokens} Token`
                        : "Token 未知，后续停止"}
                    </p>
                  </details>
                )}
                <details>
                  <summary>记录编号与原始状态</summary>
                  <p>
                    {selected.id} ·{" "}
                    {selected.summary.outcome || selected.status}
                  </p>
                  {selected.summary.reason}
                </details>
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
                    selected.summary.exports?.[selected.summary.run_id || ""]
                      ?.available &&
                    selected.summary.run_id && (
                      <button
                        disabled={busy}
                        onClick={() => void download(selected.summary.run_id!)}
                      >
                        导出 pytest 回归包
                      </button>
                    )}
                  {selected.summary.run_id &&
                    selected.summary.exports?.[selected.summary.run_id]
                      ?.reason && (
                      <p>
                        {selected.summary.mode === "FIXED_REGRESSION"
                          ? "本次是固定测试的运行记录；请在“已保存的固定测试”中查看原资产和运行条件。"
                          : selected.summary.exports[selected.summary.run_id]
                              .reason}
                      </p>
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
                        if (selected.summary.mode === "FIXED_REGRESSION") {
                          setParent(undefined);
                          setReplacePending(false);
                          setText("");
                          setSelected(null);
                          submission.current = null;
                          if (recordsOnly)
                            navigate(`/tasks?project=${selected.project_id}`);
                          else
                            setParams(
                              { project: selected.project_id },
                              { replace: true },
                            );
                          return;
                        }
                        if (recordsOnly) {
                          navigate(`/tasks?task=${selected.id}&clarify=1`);
                          return;
                        }
                        setParent(selected.id);
                        setProject(selected.project_id);
                        setReplacePending(selected.status === "NEEDS_REVIEW");
                        setText("");
                      }}
                    >
                      {selected.summary.mode === "FIXED_REGRESSION"
                        ? "新建测试需求"
                        : selected.status === "NEEDS_REVIEW"
                          ? "补充澄清"
                          : "补充测试需求"}
                    </button>
                  )}
                </div>
                <details>
                  <summary>保存与复测的支持范围</summary>
                  <p className="caption">
                    通过的受支持普通 JSON
                    场景可下载并保存固定回归包，不含账号密钥。在测试记录中，支持的只读真实接口和专用合成环境可以独立复测，模型禁用；本机服务重启后保留已保存资产。命令行运行需由操作员按包内说明提供配置与运行
                    fixture。失败、待审、上传或轮询场景不支持导出。新模型设计模式会保存可导出的通过场景，重启后仍可选择保存。
                  </p>
                </details>
              </>
            )}
          </Panel>
        </div>
      </div>
    </>
  );
}
