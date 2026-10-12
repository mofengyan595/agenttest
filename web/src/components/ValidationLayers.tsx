import type { RunReport } from "../api/types";
import { taskState, triState } from "../status";
import { Badge } from "./ui";
export function ValidationLayers({ run }: { run: RunReport }) {
  const layers = [
    {
      title: "DSL Validity",
      label: "DSL / 契约 / 目标预检",
      state: triState(run.dsl_valid),
      detail: "计划结构与目标预检结果",
    },
    {
      title: "Policy Authorization",
      label: "可信策略授权",
      state: triState(run.policy_authorized),
      detail: "操作员批准的 Registry 策略",
    },
    {
      title: "Intent Verification",
      label: "自然语言意图验证",
      state:
        run.task_mode === "operator_plan"
          ? { label: "不适用 · 固定计划", tone: "neutral" }
          : triState(run.intent_verified),
      detail: "检查计划是否覆盖受控任务意图",
    },
    {
      title: "Task Satisfaction",
      label: "最终任务满足",
      state: taskState(run),
      detail: "仅 true 表示经验证的自然语言任务完成",
    },
  ];
  return (
    <div className="validation-grid">
      {layers.map((layer, i) => (
        <div
          className={`validation-card ${layer.state.tone}`}
          key={layer.title}
        >
          <span className="layer-index">L{i + 1}</span>
          <h3>{layer.title}</h3>
          <p>{layer.label}</p>
          <Badge tone={layer.state.tone}>{layer.state.label}</Badge>
          <small>{layer.detail}</small>
        </div>
      ))}
    </div>
  );
}
