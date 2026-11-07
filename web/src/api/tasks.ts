/** Task requests are same-origin, session-bound, and never accept target or credential fields. */
export interface ResourceLifecycle {
  create_step: string; cleanup_step: string; owner_run_id: string;
  status: string; resource_id: string | number | null; cleanup_result: string;
}
export interface CreationReceipt {
  create_step: string; operation_id: string; owner_run_id: string; role: string | null;
  status: string; resource_id: string | number | null; basis: string; cleanup_result: string;
}
export interface FailureExplanation {
  mismatch: string; basis?: string; confirmed_observation: string;
  cause_confirmed: boolean; uncertainty: string; next_action: string;
}
export interface GoalTask {
  id: string;
  project_id: string;
  project_identity: string;
  request_sha256: string;
  status: string;
  plan_version: number;
  created_at: number;
  summary: {
    source_run_id?: string;
    source_blocked_operations?: string[];
    pending_negative_values?: {policy_id: string; operation_id: string; field: string;
      requested_value: unknown; reason: string}[];
    display_title?: string;
    user_request?: string;
    requirement_sources?: {source_id: string; text: string; start: number; end: number}[];
    suggestions?: string[];
    requirement_ledger?: {
      name: string; value: unknown; source_quote: string; start: number; end: number;
      deferred: boolean; candidate_operations: string[]; matched_operations?: string[];
      status: "ACCOUNTED" | "UNACCOUNTED" | "UNKNOWN_FIELD" | "DEFERRED" | "NEEDS_REVIEW";
    }[];
    http_status_requirements?: WitnessedRequirement[];
    response_requirements?: WitnessedRequirement[];
    inventory?: ProjectInventory;
    questions?: {
      confirmed_statuses?: number[];
      id: string;
      operation_id: string | null;
      question: string;
      requested_behavior: string;
      kind?: "clarification" | "permission";
      role?: string | null;
      status_choices?: number[];
      input_choices?: string[];
      input_label?: string;
    }[];
    not_covered?: string[];
    timings?: {total_ms: number; planning_ms: number; execution_ms: number};
    untested_operations?: ProjectInventory["operations"];
    design_records?: {
      group_id: string;
      status: string;
      interpretation?: string;
      reason?: string;
      model_call?: {
        calls: number;
        input_tokens: number | null;
        output_tokens: number | null;
        total_tokens: number | null;
        elapsed_ms: number;
        model_name: string | null;
      };
    }[];
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
    task_satisfied?: boolean | null;
    model_source?: string;
    execution_target?: string;
    exports?: Record<string, { available: boolean; reason: string }>;
    mode?: string;
    fixed_regression_passed?: boolean;
    resource_lifecycle?: ResourceLifecycle[];
    created_resources?: CreationReceipt[];
    cleanup?: string;
    complete?: boolean;
    checks_passed?: boolean;
    focus?: {
      mode: "selected" | "all_ready" | "needs_information" | "no_current_action";
      interpretation: string;
      operation_ids: string[];
      normal_operation_ids?: string[];
      operation_sources?: Record<string,
        "USER_REQUIREMENT" | "TRUSTED_PREREQUISITE" | "PENDING_PERMISSION">;
      requirements?: {
        source_id: string; source_quote: string;
        disposition: "required" | "needs_review" | "deferred" | "constraint";
        actions: {operation_id: string; role: string | null}[];
        before: {earlier: string; later: string}[];
        same_resource: {producer: string; consumer: string}[];
        response_checks?: string[];
        forbidden_operation_ids?: string[];
        blocked_operation_ids?: string[];
      }[];
      question: string | null;
      expectation_questions?: {
        operation_id: string;
        kind?: "business_rule" | "permission";
        role?: string | null;
        status_choices?: number[];
        question: string;
        requested_behavior: string;
        source_quote: string;
      }[];
    };
    budget_preview?: {
      required_business_requests: number;
      required_planning_calls: number;
      derived_cases?: number;
      normal_cases?: number;
      approved_negative_cases?: number;
      boundary_or_enum_cases?: number;
      optional_readonly_requests: number;
      optional_model_calls: number;
      planning_token_upper_bound: number;
      complete_project_within_budget: boolean;
      executable_groups: string[];
      excluded_groups: { case_id: string; reason: string | null }[];
    };
    goal_spec?: {
      semantic_verified: false;
      structure_valid?: boolean;
      source_accounted?: boolean;
      proposed_goals?: { goal_id: string; start: number; end: number }[];
      unaccounted_characters?: number;
      reason?: string;
      question?: string;
    };
    cases?: {
      title?: string;
      case_id: string;
      operation_id: string | null;
      status: string;
      reason?: string;
      question?: string | null;
      max_business_requests: number;
      planned_steps?: number;
      executed_steps?: number;
      run_id?: string;
      outcome?: string;
      task_satisfied?: boolean | null;
      evidence?: {
        step_id?: string;
        role?: string | null;
        executed?: boolean;
        operation_id: string;
        method: string;
        path: string;
        outcome: string;
        request?: {
          role?: string | null;
          values?: Record<string, unknown>;
          source?: string;
          omitted?: string;
        } | null;
        checks: {
          kind: string;
          selector: string | null;
          expected: unknown;
          actual: unknown;
          passed: boolean;
        }[];
      }[];
      investigations?: {
        role?: string | null;
        operation_id: string;
        method: string;
        path: string;
        outcome: string;
        requests: number;
        reason: string;
        cause_confirmed: false;
        checks: {
          selector?: string | null;
          expected: unknown;
          actual: unknown;
          passed: boolean;
        }[];
      }[];
      resource_lifecycle?: ResourceLifecycle[];
      created_resources?: CreationReceipt[];
      cleanup?: string;
      failure_explanation?: FailureExplanation | null;
      kinds?: string[];
      approved_negative_steps?: number;
      negative_rejections_passed?: number;
      base_structure_id?: string | null;
      plan_source?: string;
      structural_validation_passed?: boolean;
      base_failure?: { run_id: string; diagnostic: string };
      plan_derivation?: {
        base_run_id: string;
        base_plan_sha256: string;
      } | null;
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
      started_scenarios?: number;
      completed_scenarios?: number;
      requested_check_denominator?: number;
      executed_requested_checks?: number;
      approved_check_denominator?: number;
      attempted_approved_checks?: number;
      not_executed?: number;
      planned_scenarios?: number;
      assertion_passed_scenarios?: number;
      assertions_evaluated?: number;
      assertions_passed?: number;
    };
    context_usage?: {
      requests: number;
      model_calls: number;
      total_tokens: number | null;
      usage_known: boolean;
    };
    uncovered_negative_checks?: {
      policy_id: string;
      operation_id: string;
      field: string;
      kind: string;
      example?: string | null;
      reason: string;
    }[];
  };
}

interface WitnessedRequirement {
  source_id: string;
  source_quote: string;
  selector?: string | null;
  status: "NEEDS_REVIEW" | "NOT_EXECUTED" | "PLANNED" | "EXECUTED" | "PASS" | "ASSERTION_FAILED";
  reason?: string;
  witnesses: {case_id: string; step_id: string; operation_id: string; role: string | null;
    run_id?: string; status: string}[];
}

export const taskTerminal = (state: string) =>
  ["FINISHED", "NEEDS_REVIEW", "CANCELLED", "INTERRUPTED"].includes(state);

export async function call<T>(
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
    signal: AbortSignal.timeout(path === "/api/project-intake" || path === "/api/projects"
      ? 60000 : 15000),
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
    if (response.status === 422) {
      const payload = await response.json().catch(() => null);
      const messages: Record<string, string> = {
        SELECT_UNIQUE_OPERATIONS: "请选择不同的测试接口；接口较多时会分组安排。",
        ISOLATED_ENVIRONMENT_CONFIRMATION_REQUIRED:
          "请先确认这是已授权的隔离测试环境。",
        TARGET_AUTHORIZATION_REQUIRED:
          "请填写不含路径的本机测试地址；私网 IP 需在服务端明确授权。",
        WRITE_AUTHORIZATION_REQUIRED:
          "所选接口包含写入或登录，请明确允许它在此隔离环境执行。",
        ROLE_NOT_CONFIGURED: "接口选择了不存在的身份，请重新选择已配置身份。",
        PROTECTED_OPERATION_REQUIRES_ROLE: "受保护的接口需要选择测试身份。",
        LOGIN_IDENTITY_MISMATCH: "登录接口也要选择与受保护接口相同的测试身份。",
        LOGIN_CONFIGURATION_REQUIRED:
          "请选中登录接口，并配置账号、密码环境变量和 Token 位置。",
        RESOURCE_SOURCE_NOT_SELECTED:
          "参数引用的来源接口尚未选中，请检查接口依赖。",
        RESOURCE_DEPENDENCY_CYCLE: "接口取值形成循环，请修改输入来源。",
        RESOURCE_ROLE_RELATION_REQUIRED:
          "请核对参数来源接口与当前接口的身份关系。",
        REQUIRED_ARGUMENT_MISSING: "有必填输入未配置，请检查所选接口的参数。",
        ARGUMENT_NOT_IN_CONTRACT: "输入字段与文档不符，请重新读取文档并核对。",
        PROJECT_CREDENTIAL_LITERAL:
          "填写内容包含已配置的凭据，请移除密钥或密码；页面只填写环境变量名称。",
        OPENAPI_VALUE_TYPE: "输入类型不符合文档，请核对数字、文本和布尔值。",
        OPENAPI_VALUE_ENUM: "输入不在文档允许的枚举值内。",
        OPENAPI_VALUE_LENGTH: "输入长度不符合文档限制。",
        OPENAPI_VALUE_BOUND: "输入数值超出了文档限制。",
      };
      const code = payload?.detail?.code;
      if ((code === "CSV_HEADER_EVIDENCE_REQUIRED" || code === "CSV_ASSERTION_COLUMN_UNKNOWN") &&
          Array.isArray(payload.detail.fields) && payload.detail.fields.length === 2) {
        const [operation, selector] = payload.detail.fields;
        const reason = code === "CSV_ASSERTION_COLUMN_UNKNOWN"
          ? "断言列名不在已核对的 CSV 表头中。"
          : "缺少已核对的 CSV 表头，请提供 $.columns 的完整列表。";
        throw new Error(`${operation} ${selector}：${reason}`);
      }
      const message =
        typeof code === "string" && Object.hasOwn(messages, code)
          ? messages[code]
          : null;
      if (message) throw new Error(message);
    }
    const errors: Record<number, string> = {
      401: "本地会话已过期，请刷新页面。",
      403: "本地会话校验失败，请刷新页面。",
      409: "计划、项目版本或补充上下文已变化，请重新检查项目并新建独立任务。重复提交不会再次执行。",
      413: "接口文档过大，请使用不超过 1 MB 的 JSON 或 YAML 文件。",
      422: "需求或项目配置需要核对。请检查文档格式、授权配置和服务端账号，再重新检查。",
      503: "测试服务未启用。请运行 scripts/start_web.ps1 启动本地产品；模型设置读取本机 .env，可在网页接入自己的接口。",
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
  projects: () => call<{ projects: ProjectChoice[] }>("/api/projects"),
  list: () => call<{ tasks: GoalTask[] }>("/api/tasks"),
  read: (id: string) => call<GoalTask>(`/api/tasks/${id}`),
  timeline: (id: string) =>
    call<{ events: TaskEvent[] }>(`/api/tasks/${id}/timeline`),
  saved: () => call<{ tests: SavedTest[] }>("/api/saved-tests"),
  repeat: (csrf: string, saved: SavedTest, submission: string) =>
    call<GoalTask>("/api/saved-tests/run", csrf, {
      task_id: saved.task_id,
      run_id: saved.run_id,
      submission_id: submission,
    }),
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

export interface TaskEvent {
  sequence: number;
  phase: string;
  run_id: string | null;
  requests: number;
}
export interface SavedTest {
  title?: string;
  project_name?: string;
  run_id: string;
  task_id: string;
  project_id: string;
  available: boolean;
  reason: string;
}

export interface ProjectChoice {
  id: string;
  identity: string;
  name?: string;
  ready?: boolean;
  missing?: string[];
  profile?: IntakeProfile | null;
  inventory?: ProjectInventory;
  testing_mode?: "MODEL_DESIGNED" | "REGISTERED_GOALS";
}
export interface ProjectInventory {
  pending_checks?: {
    operation_id: string;
    role: string;
    confirmed_rule: string;
    reason: string;
    next_step: string;
  }[];
  total: number;
  counts: Record<
    "READY" | "NEEDS_INPUT" | "UNAUTHORIZED" | "UNSUPPORTED",
    number
  >;
  operations: {
    operation_id: string | null;
    method: string;
    path: string;
    summary?: string | null;
    group: string;
    state: string;
    reason: string;
    next_step: string;
    response_basis?: "OPERATOR_RULE" | "DOCUMENT_FIELDS" | "STATUS_ONLY" | "UNCONFIGURED";
  }[];
}
export interface IntakeProfile {
  id: string;
  identity: string;
  name: string;
  environment: string;
  address: string | null;
  identities: { alias: string; ready: boolean; instruction: string }[];
  rules: {
    id: string;
    behavior: string;
    role: string | null;
    operation: string;
    permitted: boolean;
    statuses: number[];
    fields: { field: string; operator: string; expected: unknown }[];
    source: string;
  }[];
  examples: string[];
  require_review: boolean;
}
export interface IntakeProject extends ProjectChoice {
  business_notes?: string;
  name: string;
  version: number;
  state: string;
  ready: boolean;
  missing: string[];
  profile_id: string | null;
  generic?: GenericSetup;
  confirmation: {
    source: string;
    configuration_version: string;
    at: number;
  } | null;
}
export interface IntakeCatalog {
  projects: IntakeProject[];
  profiles: IntakeProfile[];
  model_source: string;
  execution_target: string;
  generic_intake?: boolean;
  model_usage_blocked?: boolean;
  registered_projects?: ProjectChoice[];
}
export interface IntakePayload {
  business_notes?: string;
  name: string;
  document: string;
  profile_id: string | null;
  profile_identity: string | null;
  confirmed: boolean;
  version: number;
  generic?: GenericSetup | null;
}
export interface GenericArgument {
  required?: boolean;
  location: "body" | "path_parameters" | "query";
  name: string;
  value: unknown;
  source_operation: string | null;
  selector: string;
  value_type?: string;
  generated?: boolean;
  negative_enabled?: boolean;
  needs_input?: boolean;
  schema?: Record<string, unknown>;
  basis?: string;
  dependency_basis?: string | null;
}
export interface GenericOperation {
  after_operations?: string[];
  check_source?: string | null;
  public_evidence?: string[];
  creation?: {id_selector: string; fresh_resource_basis: string} | null;
  credential_inputs?: Record<string, string>;
  upload?: {field: string; filename: string; content: string} | null;
  poll?: {select: string; terminal_values: string[]; progress_values: string[];
    progress_fields?: {select: string; operator: "equals" | "exists" | "count" | "in";
      expected: unknown}[]; max_attempts: number; interval: number} | null;
  operation_id: string;
  alias: string;
  enabled: boolean;
  role: string | null;
  statuses: number[];
  arguments: GenericArgument[];
  checks: {
    select: string;
    operator: "exists" | "equals" | "count" | "in";
    expected: unknown;
  }[];
  access_checks?: { role: string | null; statuses: number[]; rule: string }[];
}
export interface GenericRole {
  alias: string;
  bearer_env?: string | null;
  login_operation?: string | null;
  username_env?: string | null;
  password_env?: string | null;
  username_field?: string;
  password_field?: string;
  token_selector?: string;
}
export interface GenericSetup {
  resource_lifecycles?: {create_operation: string; delete_operation: string;
    id_selector: string; path_parameter: string; fresh_resource_basis: string}[];
  base_url: string;
  isolated_confirmed: boolean;
  allow_mutations: boolean;
  max_requests: number;
  run_timeout?: number;
  read_timeout?: number;
  roles: GenericRole[];
  operations: GenericOperation[];
  assisted?: boolean;
  approve_negative_tests?: boolean;
  allow_negative_value_variants?: boolean;
  default_role?: string | null;
}
export interface ContractOperation extends GenericOperation {
  upload_field?: string;
  method: string;
  path: string;
  summary?: string | null;
  supported: boolean;
  reason?: string | null;
  authentication_required: boolean;
  basis?: string;
  response_fields?: { selector: string; label: string; value_type: string }[];
}
export const intakeApi = {
  answerPermission: (
    csrf: string,
    task: string,
    question: string,
    status: number,
    version: number,
  ) =>
    call<IntakeProject>(`/api/tasks/${task}/permission-answer`, csrf, {
      question_id: question,
      status,
      project_version: version,
    }),
  analyze: (csrf: string, document: string, project_id?: string, generic?: GenericSetup) =>
    call<{ operations: ContractOperation[]; inventory?: ProjectInventory }>(
      "/api/project-intake/analyze",
      csrf,
      { document, ...(project_id ? {project_id} : {}), ...(generic ? {generic} : {}) },
    ),
  read: () => call<IntakeCatalog>("/api/project-intake"),
  save: (csrf: string, payload: IntakePayload, id?: string) =>
    call<IntakeProject>(
      `/api/project-intake${id ? `/${id}` : ""}`,
      csrf,
      payload,
    ),
};
