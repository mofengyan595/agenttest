import type { RunReport } from "./api/types";

export type EvidenceState = "observed" | "success" | "failed" | "unknown";
export const evidenceLabels: Record<EvidenceState, string> = {
  observed: "已观测",
  success: "已成功",
  failed: "已失败",
  unknown: "未执行 / 未记录",
};

export function workflowStages(run: RunReport) {
  const nodes = run.workflow_nodes || [];
  const checks = (run.steps || []).flatMap((s) => s.checks || []);
  const plannerFailed =
    nodes.includes("plan") &&
    !nodes.includes("validate") &&
    (run.diagnostic?.layer === "model" ||
      run.model_call?.diagnostic?.layer === "model");
  const validationObserved =
    nodes.includes("validate") || run.plan_valid === true;
  // False defaults in older/current in-progress reports are not failure evidence.
  const validationFailed = validationObserved && !!run.validation_issue;
  const validationPassed =
    run.dsl_valid === true &&
    run.policy_authorized === true &&
    (run.task_mode === "operator_plan" || run.intent_verified === true);
  const executionObserved = nodes.includes("execute") || !!run.steps?.length;
  const executionFailed = run.steps?.some((s) =>
    ["ASSERTION_FAILED", "TOOL_ERROR", "TEST_INFRA_ERROR"].includes(
      s.outcome || "",
    ),
  );
  const executionPassed =
    run.completed === true &&
    !!run.steps?.length &&
    run.steps.every((s) => s.outcome === "PASS");
  const stage = (title: string, state: EvidenceState, evidence: string) => ({
    title,
    state,
    evidence,
  });
  return [
    stage(
      "用户目标",
      run.user_task_sha256 || run.task_mode === "operator_plan"
        ? "observed"
        : "unknown",
      run.task_mode === "operator_plan"
        ? "操作员固定计划"
        : run.user_task_sha256
          ? "已记录目标摘要"
          : "未记录目标",
    ),
    stage(
      "Planner",
      plannerFailed
        ? "failed"
        : nodes.includes("validate")
          ? "success"
          : nodes.includes("plan")
            ? "observed"
            : "unknown",
      plannerFailed
        ? "报告记录模型层失败，未进入校验"
        : nodes.includes("validate")
          ? "规划已返回并交给校验；不代表计划合法"
          : "仅依据实际节点记录",
    ),
    stage(
      "Validator",
      validationFailed
        ? "failed"
        : validationPassed
          ? "success"
          : validationObserved
            ? "observed"
            : "unknown",
      validationFailed
        ? "报告记录 validation_issue"
        : validationPassed
          ? "DSL、策略及适用意图门禁通过"
          : "缺少完整校验结论",
    ),
    stage(
      "Executor",
      executionFailed
        ? "failed"
        : executionPassed
          ? "success"
          : executionObserved
            ? "observed"
            : "unknown",
      executionPassed
        ? "执行已结束，记录步骤全部 PASS"
        : executionFailed
          ? "记录步骤存在明确失败"
          : "仅依据执行节点或步骤证据",
    ),
    stage(
      "Verifier",
      checks.some((c) => c.passed === false)
        ? "failed"
        : checks.length &&
            run.completed === true &&
            checks.every((c) => c.passed === true)
          ? "success"
          : checks.length
            ? "observed"
            : "unknown",
      "仅表示已记录断言结论，不代表自然语言任务完成",
    ),
    stage("Report", "observed", "当前报告可读取；不推断 Workflow 完成"),
  ];
}
