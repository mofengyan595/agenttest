import type { StepResult } from "../api/types";
import { OutcomeBadge } from "./ui";
import { metric } from "../status";

function evidenceValue(value: unknown, kind = "") {
  if (value == null) return "未记录";
  // Defense in depth: string and structured assertion values are never rendered.
  if (typeof value === "boolean" || typeof value === "number")
    return String(value);
  if (
    kind === "http_status" &&
    Array.isArray(value) &&
    value.length <= 100 &&
    value.every((code) => Number.isInteger(code) && code >= 100 && code <= 599)
  )
    return value.join(", ");
  return "[REDACTED]";
}
export function StepEvidence({
  step,
  index,
}: {
  step: StepResult;
  index: number;
}) {
  return (
    <details className="step">
      <summary>
        <span className="step-number">
          {String(index + 1).padStart(2, "0")}
        </span>
        <div className="step-main">
          <strong>
            {step.step_id}
            <span className="muted"> / {step.operation_id}</span>
          </strong>
          <div>
            <span className={`method ${step.method.toLowerCase()}`}>
              {step.method.toUpperCase()}
            </span>
            <code>{step.path_template}</code>
          </div>
        </div>
        <OutcomeBadge value={step.outcome} />
        <span className="expand-label">证据 ↓</span>
      </summary>
      <div className="step-body">
        <p className="mono muted">{step.reason || "未记录原因"}</p>
        {step.input_validation === "NORMAL_VALIDATED" && (
          <p>正常输入已通过完整校验。</p>
        )}
        {step.input_validation === "APPROVED_NEGATIVE" && (
          <div className="task-case">
            <strong>已批准的非法输入</strong>
            <p>
              {step.negative_rejected_as_expected === true
                ? "目标拒绝符合批准预期。"
                : step.negative_rejected_as_expected === false
                  ? "目标响应违反批准预期。"
                  : "尚无完整负例断言结果。"}
            </p>
            <small>
              执行器从完整校验后的正常请求构造指定变异，其他门禁继续生效。
            </small>
          </div>
        )}
        <div className="evidence-meta">
          <span>
            Step Run ID <code>{step.step_run_id || "未记录"}</code>
          </span>
          <span>
            Policy SHA-256 <code>{step.policy_sha256 || "未记录"}</code>
          </span>
        </div>
        <h3>CheckResult · 批准断言</h3>
        {!step.checks?.length ? (
          <p className="muted">未记录断言检查。</p>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>类型 / Selector</th>
                  <th>预期（脱敏）</th>
                  <th>实际（脱敏）</th>
                  <th>结论</th>
                </tr>
              </thead>
              <tbody>
                {step.checks.map((check, i) => (
                  <tr key={i}>
                    <td>
                      {check.kind}
                      <small className="mono">{check.selector || "—"}</small>
                    </td>
                    <td>{evidenceValue(check.expected, check.kind)}</td>
                    <td>{evidenceValue(check.actual)}</td>
                    <td
                      className={check.passed ? "success-text" : "danger-text"}
                    >
                      {check.passed ? "通过" : "未通过"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <h3>AttemptEvidence · HTTP 请求尝试</h3>
        {!step.attempts?.length ? (
          <p className="muted">没有 HTTP 请求证据。</p>
        ) : (
          <div className="attempts">
            {step.attempts.map((attempt, i) => (
              <div key={i} className="attempt">
                <div className="section-line">
                  <strong>Attempt {attempt.attempt}</strong>
                  <span>HTTP {attempt.status_code ?? "未记录"}</span>
                  <span>{metric(attempt.elapsed_ms)} ms</span>
                  <span>{metric(attempt.response_bytes)} bytes</span>
                </div>
                <dl>
                  <dt>Attempt ID</dt>
                  <dd>{attempt.attempt_id || "未记录"}</dd>
                  <dt>响应 SHA-256</dt>
                  <dd>{attempt.response_sha256 || "未记录"}</dd>
                  <dt>错误代码</dt>
                  <dd>{attempt.error_code || "未记录"}</dd>
                </dl>
              </div>
            ))}
          </div>
        )}
        <p className="caption">
          仅展示报告已有脱敏证据；不提供原始请求、响应正文或凭据。
        </p>
      </div>
    </details>
  );
}
