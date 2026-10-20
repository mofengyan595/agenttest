/** Task requests are same-origin, session-bound, and never accept target or credential fields. */
export interface GoalTask {
  id: string;
  project_id: string;
  project_identity: string;
  request_sha256: string;
  status: string;
  plan_version: number;
  created_at: number;
  summary: {
    scope?: string;
    requested_goals?: string[];
    required_steps?: number;
    required_plan?: {
      key: string;
      operation_id: string;
      role: string | null;
      policy_id: string;
    }[];
    reason?: string;
    question?: string;
    run_id?: string;
    outcome?: string;
    task_satisfied?: boolean;
    model_source?: string;
    cases?: {
      case_id: string;
      operation_id: string | null;
      status: string;
      reason?: string;
      question?: string | null;
      max_business_requests: number;
      run_id?: string;
      outcome?: string;
      task_satisfied?: boolean;
    }[];
    budget?: {
      max_requests: number;
      max_model_calls: number;
      max_total_tokens: number;
    };
    coverage?: {
      operation_denominator: number;
      scenario_denominator: number;
      executed_operations?: number;
      executed_scenarios?: number;
      not_executed?: number;
    };
    context_usage?: {
      requests: number;
      model_calls: number;
      total_tokens: number | null;
      usage_known: boolean;
    };
  };
}

export const taskTerminal = (state: string) =>
  ["FINISHED", "NEEDS_REVIEW", "CANCELLED", "INTERRUPTED"].includes(state);

async function call<T>(
  path: string,
  csrf?: string,
  body?: unknown,
  archive = false,
): Promise<T> {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    credentials: "same-origin",
    redirect: "error",
    cache: "no-store",
    signal: AbortSignal.timeout(15000),
    headers:
      body === undefined
        ? {}
        : {
            "Content-Type": "application/json",
            "x-agenttest-csrf": csrf || "",
          },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  }).catch(() => {
    throw new Error("无法连接任务服务。");
  });
  if (!response.ok) {
    const errors: Record<number, string> = {
      401: "本地会话已过期，请刷新页面。",
      403: "本地会话校验失败，请刷新页面。",
      409: "计划或补充上下文已变化，请刷新后重试。",
      422: "需求或项目配置需要核对。",
      503: "任务服务未启用，请由操作员配置已注册项目。",
    };
    throw new Error(errors[response.status] || "任务服务暂不可用。");
  }
  if (archive) return (await response.blob()) as T;
  return (await response.json().catch(() => {
    throw new Error("任务返回数据格式无效。");
  })) as T;
}

let pendingSession: Promise<{ csrf_token: string }> | null = null;
export const taskApi = {
  session: () => {
    if (!pendingSession)
      pendingSession = call<{ csrf_token: string }>(
        "/api/task-session",
      ).finally(() => {
        pendingSession = null;
      });
    return pendingSession;
  },
  projects: () =>
    call<{ projects: { id: string; identity: string }[] }>("/api/projects"),
  list: () => call<{ tasks: GoalTask[] }>("/api/tasks"),
  read: (id: string) => call<GoalTask>(`/api/tasks/${id}`),
  submit: (
    csrf: string,
    project: string,
    text: string,
    suite: boolean,
    parent?: string,
    replacePending = false,
    submissionId?: string,
  ) =>
    call<GoalTask>("/api/tasks", csrf, {
      project_id: project,
      user_task: text,
      submission_id: submissionId || crypto.randomUUID().replaceAll("-", ""),
      suite,
      parent_task_id: parent || null,
      replace_pending: replacePending,
    }),
  start: (csrf: string, task: GoalTask) =>
    call<GoalTask>(`/api/tasks/${task.id}/start`, csrf, {
      plan_version: task.plan_version,
    }),
  cancel: (csrf: string, id: string) =>
    call<GoalTask>(`/api/tasks/${id}/cancel`, csrf, {}),
  export: (csrf: string, id: string, runId: string) =>
    call<Blob>(`/api/tasks/${id}/regression`, csrf, { run_id: runId }, true),
};
