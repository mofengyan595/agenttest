import type { RunReport } from "../api/types";
import { taskState, triState } from "../status";
import { Badge } from "./ui";
export function ValidationLayers({ run }: { run: RunReport }) {
  const layers = [
    {
      title: "计划检查",
      label: "计划结构、接口与目标预检",
      state: triState(run.dsl_valid),
      detail: "计划结构与目标预检结果",
    },
    {
      title: "操作授权",
      label: "是否符合已批准的测试范围",
      state: triState(run.policy_authorized),
      detail: "仅执行项目中已确认的接口和检查规则",
    },
    {
      title: "需求覆盖核验",
      label: "是否独立核验漏测或需求误解",
      state:
        run.task_mode === "operator_plan"
          ? { label: "不适用 · 固定计划", tone: "neutral" }
          : run.task_mode === "model_designed"
            ? { label: "未独立核验", tone: "neutral" }
            : triState(run.intent_verified),
      detail: "模型理解与规划不能替代独立的需求核验",
    },
    {
      title: "需求完成确认",
      label: "区分接口检查通过与整个需求完成",
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
