import type { Outcome, RunReport } from "./api/types";
export const outcomes: Record<
  Outcome,
  { label: string; tone: string; description: string }
> = {
  PASS: {
    label: "策略通过",
    tone: "success",
    description:
      "批准策略的断言通过；自然语言任务是否完成，另看 Task Satisfaction。",
  },
  ASSERTION_FAILED: {
    label: "断言失败",
    tone: "danger",
    description: "可信断言与业务响应不一致，请查看步骤证据。",
  },
  TEST_INFRA_ERROR: {
    label: "环境错误",
    tone: "warning",
    description: "环境、传输或预算错误，不能据此认定业务缺陷。",
  },
  TOOL_ERROR: {
    label: "工具错误",
    tone: "danger",
    description: "计划、依赖或工具执行错误。",
  },
  NEEDS_REVIEW: {
    label: "需要审阅",
    tone: "review",
    description: "策略、约束或意图尚不能可靠确认，不等同于 HTTP 失败。",
  },
};
export function triState(value: boolean | null | undefined, task = false) {
  if (value === true)
    return { label: task ? "任务已满足" : "已通过", tone: "success" };
  if (value === false)
    return { label: task ? "任务未满足" : "未通过", tone: "danger" };
  return {
    label: value === null ? "不适用 / 未确认" : "未记录",
    tone: "neutral",
  };
}
export function taskState(run: RunReport) {
  if (run.task_mode === "operator_plan")
    return { label: "不适用 · 固定计划", tone: "neutral" };
  return triState(run.task_satisfied, true);
}
export const workflowLabel = (value?: boolean | null) =>
  value === true ? "已完整结束" : value === false ? "未完整结束" : "未记录";
export const dateLabel = (value?: string | null) =>
  value && !Number.isNaN(Date.parse(value))
    ? new Intl.DateTimeFormat("zh-CN", {
        dateStyle: "medium",
        timeStyle: "medium",
      }).format(new Date(value))
    : "未记录";
export const metric = (value?: number | null) =>
  value == null ? "未知" : value.toLocaleString("zh-CN");
