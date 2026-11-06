import { useState } from "react";
import type { ProjectInventory as Inventory } from "../api/tasks";

const names: Record<string, string> = {
  READY: "可以测试",
  NEEDS_INPUT: "需要补充",
  UNAUTHORIZED: "未获授权",
  UNSUPPORTED: "暂不支持",
};
const responseBasis = {
  OPERATOR_RULE: "已填写响应字段及业务规则依据",
  DOCUMENT_FIELDS: "有响应字段检查，业务规则依据待补充",
  STATUS_ONLY: "仅配置状态码，响应内容待补充",
  UNCONFIGURED: "待配置响应预期",
};

export function ProjectInventory({ value }: { value: Inventory }) {
  const [filter, setFilter] = useState("all");
  const [page, setPage] = useState(0);
  const entries = value.operations.filter(
    (o) => filter === "all" || o.state === filter,
  );
  return (
    <section className="project-inventory" aria-label="完整接口清单">
      <p>
        <strong>文档共 {value.total} 个接口</strong> · 可测试{" "}
        {value.counts.READY} · 待补充 {value.counts.NEEDS_INPUT} · 未授权{" "}
        {value.counts.UNAUTHORIZED} · 暂不支持 {value.counts.UNSUPPORTED}
      </p>
      {value.pending_checks?.map((check, i) => (
        <p key={i}>
          身份 {check.role} · {check.confirmed_rule}：{check.reason}{" "}
          {check.next_step}
        </p>
      ))}
      <details>
        <summary>查看完整接口清单与下一步</summary>
        <label>
          按就绪情况查看
          <select
            value={filter}
            onChange={(e) => {
              setFilter(e.target.value);
              setPage(0);
            }}
          >
            <option value="all">全部接口</option>
            {Object.entries(names).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </label>
        <ul className="inventory-list">
          {entries.slice(page * 10, page * 10 + 10).map((o, i) => (
            <li key={`${o.method}-${o.path}-${i}`}>
              <strong>
                {o.method} {o.path}
              </strong>
              <span>
                {names[o.state] || o.state}：{o.reason}
              </span>
              {o.response_basis && <span>{responseBasis[o.response_basis]}</span>}
              {o.state !== "READY" && <span>下一步：{o.next_step}</span>}
            </li>
          ))}
        </ul>
        {entries.length > 10 && (
          <div className="button-row">
            <button
              type="button"
              disabled={page === 0}
              onClick={() => setPage(page - 1)}
            >
              上一组
            </button>
            <span>
              {page + 1}/{Math.ceil(entries.length / 10)} 组 · 共{" "}
              {entries.length} 个
            </span>
            <button
              type="button"
              disabled={(page + 1) * 10 >= entries.length}
              onClick={() => setPage(page + 1)}
            >
              下一组
            </button>
          </div>
        )}
      </details>
    </section>
  );
}
