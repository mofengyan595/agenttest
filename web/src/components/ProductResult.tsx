import { Link } from "react-router";
import { useState } from "react";
import type { GoalTask } from "../api/tasks";
import { ProjectInventory } from "./ProjectInventory";

const kinds: Record<string, string> = {
  normal: "正常请求",
  boundary: "参数边界",
  negative: "异常参数",
  permission: "身份权限",
  flow: "跨接口流程",
};
const value = (v: unknown) => (v === undefined ? "未记录" : JSON.stringify(v));
const negativeKinds: Record<string, string> = {
  required_missing: "必填项缺失", type_mismatch: "参数类型",
  boundary_outside: "参数边界", enum_outside: "枚举范围",
};

export function ProductResult({
  task,
  busy,
  save,
  answer,
  chooseInput,
}: {
  task: GoalTask;
  busy: boolean;
  save: (run: string) => void;
  answer: (question: string, status: number) => void;
  chooseInput?: (label: string, value: string) => void;
}) {
  const result = task.summary;
  const [negativePage, setNegativePage] = useState({task: task.id, page: 0});
  const remaining = result.uncovered_negative_checks || [];
  const page = Math.min(negativePage.task === task.id ? negativePage.page : 0,
    Math.max(0, Math.ceil(remaining.length / 20) - 1));
  const cases = result.cases || [];
  const completed = result.coverage?.completed_scenarios ?? cases.filter((c) =>
    c.status === "FINISHED" && ["PASS", "ASSERTION_FAILED"].includes(c.outcome || "") &&
    (c.planned_steps === undefined || c.executed_steps === c.planned_steps)).length;
  const pending =
    result.questions?.filter((q) => !q.confirmed_statuses?.length) || [];
  const saveable = cases.some(
    (c) => c.run_id && result.exports?.[c.run_id]?.available &&
      !(c.kinds?.includes("negative") && c.approved_negative_steps === 0),
  );
  const failures = cases.flatMap((c) =>
    (c.evidence || []).flatMap((e) =>
      e.checks
        .filter((k) => !k.passed)
        .map((check) => ({ e, check, title: c.title })),
    ),
  );
  return (
    <section className="product-result" aria-label="本轮测试结果">
      {result.reason && (
        <p className="finding-card">本轮停止原因：{result.reason}</p>
      )}
      {result.user_request && (
        <details className="request-focus">
          <summary>本次测试重点</summary>
          <p>{result.user_request}</p>
        </details>
      )}
      {result.focus?.mode === "selected" && (
        <p>
          按本次重点安排 {result.focus.operation_ids.length}{" "}
          个接口，必要的前置步骤另计。
        </p>
      )}
      {result.coverage && (
        <div className="coverage-summary">
          <strong>
            文档接口：已请求 {result.coverage.executed_operations}/
            {result.coverage.operation_denominator}
          </strong>
          <span>
            本轮场景：完整执行 {completed}/
            {result.coverage.scenario_denominator}， 检查通过{" "}
            {result.coverage.assertion_passed_scenarios}
          </span>
          {!!result.coverage.requested_check_denominator && <span>
            明确指定的异常检查：已执行 {result.coverage.executed_requested_checks ?? 0}/
            {result.coverage.requested_check_denominator}
          </span>}
          {!!result.coverage.approved_check_denominator && <span>
            本轮尝试的已批准异常检查：{result.coverage.attempted_approved_checks ?? 0}/
            {result.coverage.approved_check_denominator} 项（项目总数）
          </span>}
          <small>接口请求过不代表场景完整；通过只表示符合已有检查规则。</small>
        </div>
      )}
      <h3>发现的问题与依据</h3>
      {failures.length ? (
        failures.map(({ e, check, title }, i) => (
          <div className="finding-card" key={i}>
            <strong>
              {title} · {e.method} {e.path}
            </strong>
            <p>
              {check.selector
                ? `响应字段 ${check.selector.replace(/^\$\.?/, "")}`
                : "响应状态"}
              ： 预期 {value(check.expected)}，实际 {value(check.actual)}。
            </p>
            <p className="caption">
              依据为项目中已确认的检查规则；接口原因需结合调查证据判断。
            </p>
          </div>
        ))
      ) : (
        <p>
          {cases.some((c) => c.status === "CANCELLED")
            ? "本轮已取消，部分请求可能已经完成；保留证据，未自动回滚或重新发送。"
            : cases.some((c) => c.outcome === "TEST_INFRA_ERROR")
            ? "存在环境或传输故障，不能据此认定接口有缺陷。"
            : cases.some((c) => c.status === "REJECTED")
              ? "部分模型方案未通过执行校验，相关请求没有发送。"
              : cases.some((c) => c.outcome === "PASS")
                ? "已执行的检查尚未发现与已有规则不符的结果。"
                : "尚无足够的执行证据。"}
        </p>
      )}
      {!!result.questions?.length && (
        <div className="pending-questions">
          <h3>需要补充的信息</h3>
          {result.questions.map((q) => (
            <div key={q.id}>
              <p>{q.question}</p>
              {!!q.input_choices?.length && chooseInput && (
                <div className="button-row">
                  {q.input_choices.map((choice) => (
                    <button key={choice} disabled={busy}
                      onClick={() => chooseInput(q.input_label || "输入", choice)}>
                      选择“{choice}”并补充
                    </button>
                  ))}
                </div>
              )}
              {q.confirmed_statuses?.length ? (
                <p>
                  已保存预期：返回 {q.confirmed_statuses.join(" / ")}
                  。原始结果保留，后续测试采用新规则。
                </p>
              ) : (
                q.kind === "permission" && (
                  <>
                    <p>
                      确认范围：身份 {q.role} · {q.requested_behavior}
                    </p>
                    <div className="button-row">
                      {q.status_choices?.map((status) => (
                        <button
                          key={status}
                          disabled={busy}
                          onClick={() => answer(q.id, status)}
                        >
                          确认应返回 {status}
                        </button>
                      ))}
                    </div>
                  </>
                )
              )}
            </div>
          ))}
        </div>
      )}
      <h3>实际测试的接口与场景</h3>
      {!cases.length && <p>{["QUEUED", "RUNNING"].includes(task.status)
        ? "正在设计测试场景。" : "本轮尚无可执行场景，请先查看需要补充的信息与停止原因。"}</p>}
      {cases.map((c) => (
        <article className="scenario-card" key={c.case_id}>
          <div className="scenario-heading">
            <strong>{c.title || "测试场景"}</strong>
            <span>
              {c.status === "CANCELLED"
                ? "已停止"
                : c.status === "INTERRUPTED"
                  ? "执行中断"
                  : c.status === "RUNNING"
                    ? "正在执行"
                    : c.outcome === "PASS"
                      ? c.kinds?.includes("negative") && c.approved_negative_steps === 0
                        ? "异常检查未执行"
                        : "检查通过"
                      : c.outcome === "ASSERTION_FAILED"
                        ? "结果不符合预期"
                        : c.outcome === "TEST_INFRA_ERROR"
                          ? "环境异常"
                          : c.status === "REJECTED"
                            ? "方案未获执行"
                            : c.status === "FINISHED"
                              ? "需核对"
                              : "未执行"}
            </span>
          </div>
          {(!!c.kinds?.length || !!c.approved_negative_steps) && <p className="caption">
            {!!c.kinds?.length && <>模型方案类型：{c.kinds.map((k) => kinds[k] || k).join("、")}</>}
            {!!c.approved_negative_steps && <> · 受控异常检查通过 {c.negative_rejections_passed ?? 0}
              /{c.approved_negative_steps}</>}
          </p>}
          <ul>
            {c.evidence?.map((e, i) => (
              <li key={i}>
                {e.method} {e.path}
                <details>
                  <summary>请求与检查证据</summary>
                  <p>执行身份：{e.request?.role || "匿名"}</p>
                  {e.request?.values &&
                    Object.entries(e.request.values).map(([k, v]) => (
                      <p key={k}>
                        {(
                          {
                            path_parameters: "路径参数",
                            query: "查询参数",
                            body: "请求内容",
                          } as Record<string, string>
                        )[k] || k}
                        ：{value(v)}
                      </p>
                    ))}
                  {e.request?.omitted && <p>{e.request.omitted}</p>}
                  {e.checks.map((check, j) => (
                    <p key={j}>
                      {check.passed ? "通过" : "失败"} ·
                      {check.selector || "响应状态"} · 预期{" "}
                      {value(check.expected)} · 实际 {value(check.actual)}
                    </p>
                  ))}
                  {e.request?.source && (
                    <details>
                      <summary>规则版本与来源</summary>
                      {e.request.source}
                    </details>
                  )}
                </details>
              </li>
            ))}
          </ul>
          {c.reason && <p>{c.reason}</p>}
          {c.run_id && result.exports?.[c.run_id]?.reason && (
            <p className="caption">{result.exports[c.run_id].reason}</p>
          )}
          {c.investigations?.map((probe, i) => (
            <div key={i} className="read-probe">
              <strong>只读复核</strong>
              <p>
                {probe.method} {probe.path} · 实际请求 {probe.requests} 次。
              </p>
              <p>
                {probe.requests === 0
                  ? "复核未能执行，未增加判断依据。"
                  : probe.outcome === "ASSERTION_FAILED"
                    ? "相同身份与资源的只读复核再次出现不符结果，内部原因尚未确认。"
                    : probe.outcome === "PASS"
                      ? "只读复核符合原规则；原始失败仍然保留，暂不能确认差异原因。"
                      : "只读复核也未完成，需检查环境或账号；目前不能确认原因。"}
              </p>
            </div>
          ))}
          <div className="button-row">
            {c.run_id && <Link to={`/runs/${c.run_id}`}>查看详细记录</Link>}
            {c.run_id && result.exports?.[c.run_id]?.available &&
              !(c.kinds?.includes("negative") && c.approved_negative_steps === 0) && (
              <button disabled={busy} onClick={() => save(c.run_id!)}>
                保存测试
              </button>
            )}
          </div>
        </article>
      ))}
      <h3>没有测试的部分</h3>
      {!!remaining.length && <details>
        <summary>项目中本轮未尝试的已批准异常检查（{remaining.length} 项）</summary>
        <p>这些检查已获批准，但不一定属于本次重点；一个检查能力也不代表所有取值都已测试。</p>
        {remaining.slice(page * 20, (page + 1) * 20).map((entry) => <p key={entry.policy_id}>
          {result.inventory?.operations.find((o) => o.operation_id === entry.operation_id)?.summary
            || entry.operation_id} · {entry.field}：{negativeKinds[entry.kind] || entry.kind}
          {entry.example ? `（已批准示例：${entry.example}）` : ""}。{entry.reason}
        </p>)}
        {remaining.length > 20 && <div className="button-row">
          <button disabled={page === 0}
            onClick={() => setNegativePage({task: task.id, page: page - 1})}>上一组检查</button>
          <span>{page + 1}/{Math.ceil(remaining.length / 20)} 组</span>
          <button disabled={(page + 1) * 20 >= remaining.length}
            onClick={() => setNegativePage({task: task.id, page: page + 1})}>下一组检查</button>
        </div>}
      </details>}
      {!!result.not_covered?.length && <details>
        <summary>方案中的未覆盖说明</summary>
        {result.not_covered.map((n, i) => <p key={i}>{n}</p>)}
      </details>}
      <details>
        <summary>
          本轮未请求的接口（{result.untested_operations?.length ?? 0}）
        </summary>
        {result.untested_operations?.map((o, i) => (
          <p key={i}>
            {o.method} {o.path}：{o.reason} {o.next_step}
          </p>
        ))}
      </details>
      {result.inventory && <ProjectInventory value={result.inventory} />}
      <h3>下一步</h3>
      <p>
        {pending.length
          ? "补充上面的问题，确认预期后再安排受影响的测试。已完成的证据会保留。"
          : result.questions?.length
            ? "确认信息已保存。可以提交补充需求，沿用原重点安排尚未完成的检查。原结果保持不变。"
            : failures.length
              ? "根据请求、预期和实际结果复核问题；已有断言保持原样。"
              : saveable
                ? "可以保存通过的受支持场景，在已保存测试中按当前运行条件再次运行。"
                : "先核对每项场景的执行结果和复测条件；当前没有可保存的受支持场景。"}
      </p>
      <details>
        <summary>模型建议与真实用量</summary>
        <p>需求理解和方案来自模型；检查依据来自已确认规则，结果来自实际执行。</p>
        {result.design_records?.map((r, i) => (
          <div key={i}>
            <p>{r.interpretation || r.reason || "方案处理中"}</p>
            {r.model_call && (
              <p>
                {r.model_call.model_name} · {r.model_call.calls} 次调用 · 输入{" "}
                {r.model_call.input_tokens ?? "未知"} · 输出{" "}
                {r.model_call.output_tokens ?? "未知"} · 总计{" "}
                {r.model_call.total_tokens ?? "未知"} Token
              </p>
            )}
          </div>
        ))}
      </details>
    </section>
  );
}
