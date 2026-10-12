import type { RunPage } from "../api/types";
import { dateLabel, workflowLabel } from "../status";
import { OutcomeBadge, RunLink } from "./ui";
export function RunTable({ items }: { items: RunPage["items"] }) {
  return (
    <div className="run-records">
      <div className="run-cards">
        {items.map((run) => (
          <article className="run-card" key={run.run_id}>
            <RunLink id={run.run_id} />
            <small>{dateLabel(run.started_at)}</small>
            <OutcomeBadge value={run.outcome} />
            <dl>
              <div>
                <dt>测试环境</dt>
                <dd>{run.environment}</dd>
              </div>
              <div>
                <dt>步骤</dt>
                <dd>{run.steps ?? "未记录"}</dd>
              </div>
              <div>
                <dt>Workflow</dt>
                <dd>{workflowLabel(run.workflow_completed)}</dd>
              </div>
            </dl>
          </article>
        ))}
      </div>
      <div className="table-scroll run-table">
        <table>
          <thead>
            <tr>
              <th>Run ID / 开始时间</th>
              <th>测试环境</th>
              <th>Outcome</th>
              <th>步骤</th>
              <th>Workflow</th>
            </tr>
          </thead>
          <tbody>
            {items.map((run) => (
              <tr key={run.run_id}>
                <td>
                  <RunLink id={run.run_id} />
                  <small>{dateLabel(run.started_at)}</small>
                </td>
                <td>
                  <span className="env-label">{run.environment}</span>
                </td>
                <td>
                  <OutcomeBadge value={run.outcome} />
                </td>
                <td>{run.steps ?? "未记录"}</td>
                <td>{workflowLabel(run.workflow_completed)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
