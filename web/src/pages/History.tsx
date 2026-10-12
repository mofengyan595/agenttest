import { useCallback, useState } from "react";
import { ChevronLeft, ChevronRight, RefreshCw, Search } from "lucide-react";
import { api, validRunId } from "../api/client";
import type { Outcome } from "../api/types";
import { outcomes } from "../status";
import { useQuery } from "../useQuery";
import { useNavigate } from "react-router";
import { RunTable } from "../components/RunTable";
import {
  Empty,
  Failure,
  Loading,
  Notice,
  PageTitle,
  Panel,
} from "../components/ui";

export function RunLookup() {
  const [id, setId] = useState("");
  const [error, setError] = useState(false);
  const navigate = useNavigate();
  return (
    <form
      className="lookup"
      onSubmit={(e) => {
        e.preventDefault();
        if (!validRunId(id.trim())) setError(true);
        else navigate(`/runs/${id.trim()}`);
      }}
    >
      <label htmlFor="run-lookup" className="sr-only">
        查询 Run ID
      </label>
      <Search size={16} />
      <input
        id="run-lookup"
        placeholder="输入完整 Run ID"
        value={id}
        onChange={(e) => {
          setId(e.target.value);
          setError(false);
        }}
      />
      <button type="submit">查看报告</button>
      {error && <span role="alert">请输入 32 位小写十六进制 Run ID。</span>}
    </form>
  );
}
export function History() {
  const [offset, setOffset] = useState(0);
  const [outcome, setOutcome] = useState<Outcome | "">("");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const result = useQuery(
    useCallback(
      (signal: AbortSignal) => api.runs(offset, outcome, order, signal),
      [offset, outcome, order],
    ),
  );
  return (
    <>
      <PageTitle
        eyebrow="EVIDENCE / 历史测试报告"
        title="每一次运行，都有据可查。"
        description="查看批准策略的执行结果，追溯步骤、失败诊断与脱敏证据。"
      >
        <button onClick={result.retry}>
          <RefreshCw size={16} />
          刷新报告
        </button>
      </PageTitle>
      <div className="history-toolbar">
        <RunLookup />
        <div className="filters">
          <label>
            结果
            <select
              aria-label="结果"
              value={outcome}
              onChange={(e) => {
                setOutcome(e.target.value as Outcome | "");
                setOffset(0);
              }}
            >
              <option value="">全部 Outcome</option>
              {Object.entries(outcomes).map(([value, status]) => (
                <option key={value} value={value}>
                  {value} · {status.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            时间
            <select
              aria-label="时间"
              value={order}
              onChange={(e) => {
                setOrder(e.target.value as "asc" | "desc");
                setOffset(0);
              }}
            >
              <option value="desc">最新在前</option>
              <option value="asc">最早在前</option>
            </select>
          </label>
        </div>
      </div>
      <Panel title="运行记录" subtitle="只读查询 · 当前服务端报告目录">
        <div className="catalog-summary">
          <strong>{result.data?.total ?? "—"}</strong>
          <span>
            {outcome ? "条匹配记录" : "条有效记录"} · 当前目录，不含嵌套命名空间
          </span>
        </div>
        {result.loading ? (
          <Loading />
        ) : result.error ? (
          <Failure message={result.error} retry={result.retry} />
        ) : !result.data?.items.length ? (
          <Empty title="没有符合条件的报告">
            <p>调整筛选条件，或先通过 CLI 生成离线演示报告。</p>
          </Empty>
        ) : (
          <RunTable items={result.data.items} />
        )}
        {!!result.data?.skipped && (
          <Notice>
            {result.data.skipped} 个无效、不完整或不可读取的报告已跳过。
          </Notice>
        )}
        <div className="pagination">
          <span>
            {result.data?.total
              ? `${offset + 1}–${Math.min(offset + 12, result.data.total)} / ${result.data.total}`
              : "0 条记录"}
          </span>
          <div>
            <button
              aria-label="上一页"
              disabled={result.loading || offset === 0}
              onClick={() => setOffset(Math.max(0, offset - 12))}
            >
              <ChevronLeft size={16} />
            </button>
            <button
              aria-label="下一页"
              disabled={
                result.loading ||
                !result.data ||
                offset + 12 >= result.data.total
              }
              onClick={() => setOffset(offset + 12)}
            >
              <ChevronRight size={16} />
            </button>
          </div>
        </div>
      </Panel>
      <p className="caption">
        总数来自有界目录扫描后的有效匹配报告，不使用当前页数量估算全量统计。
      </p>
    </>
  );
}
