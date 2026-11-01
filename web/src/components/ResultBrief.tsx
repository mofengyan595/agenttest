import { useCallback } from "react";
import { api } from "../api/client";
import type { RunReport } from "../api/types";
import { useQuery } from "../useQuery";

export function EvidenceBrief({ run }: { run: RunReport }) {
  const steps = Array.isArray(run.steps) ? run.steps : [];
  const failures = steps.flatMap((s) =>
    (s.checks || []).filter((c) => c.passed === false).map((c) => ({ s, c })),
  );
  const planSize = run.plan_structure?.length || undefined;
  return (
    <div className="evidence-brief">
      <h3>实际测试了什么</h3>
      {steps.length ? (
        <ul>
          {steps.map((s) => (
            <li key={s.step_id}>
              {s.method} {s.path_template}：
              {s.outcome === "PASS"
                ? "检查通过"
                : s.outcome === "ASSERTION_FAILED"
                  ? "结果不符合预期"
                  : s.outcome === "TEST_INFRA_ERROR"
                    ? "测试环境异常"
                    : "未能确认"}
              （实际请求 {s.attempts?.length ?? 0} 次）
            </li>
          ))}
        </ul>
      ) : (
        <p>尚无已执行的接口记录；不能据此判断业务通过。</p>
      )}
      {planSize !== undefined && (
        <p>
          已记录步骤 {steps.length}/{planSize}；未执行{" "}
          {Math.max(0, planSize - steps.length)}。整个需求还需结合任务范围判断。
        </p>
      )}
      {planSize === undefined && (
        <p>此报告未记录完整计划的步骤总数；不能用已记录步骤数推定覆盖完整。</p>
      )}
      {failures.length > 0 && (
        <>
          <h3>问题依据</h3>
          <ul>
            {failures.map(({ s, c }, i) => (
              <li key={i}>
                {s.operation_id} · {c.selector || "响应状态"}：预期{" "}
                {JSON.stringify(c.expected)}，实际 {JSON.stringify(c.actual)}
                。这是确定性检查结果。
              </li>
            ))}
          </ul>
        </>
      )}
      <p>
        {run.outcome === "TEST_INFRA_ERROR"
          ? "环境或传输异常，不能据此认定接口有缺陷。请检查环境与身份配置。"
          : run.outcome === "TOOL_ERROR"
            ? "计划或工具执行异常。请查看详细错误和未执行步骤。"
            : failures.length
              ? "下一步：结合详细请求记录复核不符合预期的响应。模型诊断仅为建议。"
              : "本场景符合已有检查规则；可以保存受支持的测试，再核对本轮覆盖与遗漏。"}
      </p>
    </div>
  );
}

export function ResultBrief({ id }: { id: string }) {
  const result = useQuery(
    useCallback((signal: AbortSignal) => api.run(id, signal), [id]),
  );
  if (result.error) return <p>详细证据暂时无法读取，请打开报告后重试。</p>;
  return result.data ? (
    <EvidenceBrief run={result.data} />
  ) : (
    <p>正在读取实际测试记录…</p>
  );
}
