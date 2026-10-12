import type { RunReport } from "../api/types";
import { Check, CircleDashed } from "lucide-react";
import { Badge } from "./ui";
export function WorkflowEvidence({ run }: { run: RunReport }) {
  const nodes = run.workflow_nodes || [];
  const stages = [
    {
      title: "用户目标",
      evidence: run.user_task_sha256
        ? "已记录目标摘要"
        : run.task_mode === "operator_plan"
          ? "操作员固定计划"
          : "未记录目标",
      seen: !!run.user_task_sha256 || run.task_mode === "operator_plan",
    },
    {
      title: "Planner",
      evidence: nodes.includes("plan") ? "plan 节点已进入" : "未记录规划节点",
      seen: nodes.includes("plan"),
    },
    {
      title: "Validator",
      evidence: nodes.includes("validate")
        ? "validate 节点已进入"
        : "未记录校验节点",
      seen: nodes.includes("validate"),
    },
    {
      title: "Executor",
      evidence: nodes.includes("execute")
        ? "execute 节点已进入"
        : run.steps?.length
          ? "存在步骤证据"
          : "未记录执行",
      seen: nodes.includes("execute") || !!run.steps?.length,
    },
    {
      title: "Verifier",
      evidence: run.steps?.some((s) => s.checks?.length)
        ? "存在断言检查证据"
        : "未记录检查证据",
      seen: !!run.steps?.some((s) => s.checks?.length),
    },
    { title: "Report", evidence: "当前报告已可读取", seen: true },
  ];
  return (
    <>
      <div className="flow">
        {stages.map((stage, i) => (
          <div key={stage.title} className={stage.seen ? "observed" : ""}>
            <div className="flow-icon">
              {stage.seen ? <Check size={17} /> : <CircleDashed size={17} />}
            </div>
            <strong>{stage.title}</strong>
            <small>{stage.evidence}</small>
            {i < 5 && (
              <span className="flow-arrow" aria-hidden="true">
                →
              </span>
            )}
          </div>
        ))}
      </div>
      <p className="caption">
        流程图表示已记录的节点或证据存在，进入节点不等于校验成功。Verifier
        在执行器内部进行。
      </p>
      <div className="node-list">
        <span>实际 Workflow 节点</span>
        {nodes.length ? (
          nodes.map((node, i) => <Badge key={`${node}-${i}`}>{node}</Badge>)
        ) : (
          <span className="muted">未记录（直接 Executor / 旧报告）</span>
        )}
      </div>
    </>
  );
}
