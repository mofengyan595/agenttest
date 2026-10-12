import type { RunReport } from "../api/types";
import { Check, CircleDashed, Eye, X } from "lucide-react";
import { evidenceLabels, workflowStages } from "../workflowStatus";
import { Badge } from "./ui";
export function WorkflowEvidence({ run }: { run: RunReport }) {
  const nodes = run.workflow_nodes || [];
  const stages = workflowStages(run);
  const icons = {
    success: Check,
    failed: X,
    observed: Eye,
    unknown: CircleDashed,
  };
  return (
    <>
      <div className="flow">
        {stages.map((stage, i) => {
          const Icon = icons[stage.state];
          return (
            <div key={stage.title} className={`flow-${stage.state}`}>
              <div className="flow-icon">
                <Icon size={17} aria-hidden="true" />
              </div>
              <strong>{stage.title}</strong>
              <small className="flow-state">
                {evidenceLabels[stage.state]}
              </small>
              <small>{stage.evidence}</small>
              {i < 5 && (
                <span className="flow-arrow" aria-hidden="true">
                  →
                </span>
              )}
            </div>
          );
        })}
      </div>
      <p className="caption">
        眼睛表示已进入 / 已观测，不表示成功；缺少证据统一显示未执行 /
        未记录。Verifier 在执行器内部进行。NEEDS_REVIEW 不作为 HTTP 失败。
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
