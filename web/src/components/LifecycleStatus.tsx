import type { CreationReceipt, ResourceLifecycle } from "../api/tasks";

export function CreationStatus({receipts}: {receipts?: CreationReceipt[]}) {
  if (!receipts?.length) return null;
  return <div className="finding-card" aria-label="新建资源归属">
    <p>本轮新建资源收据：{receipts.filter((r) => r.status === "OWNED").length} 项归属已核实。</p>
    <p>已核实清理 {receipts.filter((r) => r.cleanup_result === "PASS").length} 项；
      其余资源保留待处理，不会自动重放写入。</p>
    <details><summary>查看新建资源与依据</summary>
      {receipts.map((r) => <div key={r.create_step}>
        <p>{r.operation_id} · {r.resource_id === null ? "资源标识未核实" : `资源 ${r.resource_id}`} ·
          {r.status === "OWNED" ? "本轮归属已核实" : r.status.includes("UNKNOWN")
            ? "写入结果未知，需核实" : "新建结果未核实"}</p>
        <p className="caption">归属运行 {r.owner_run_id}；依据：{r.basis}；
          {r.cleanup_result === "PASS" ? "本轮清理已核实。" : "清理尚未确认。"}</p>
      </div>)}
    </details>
  </div>;
}

export function LifecycleStatus({resources}: {resources?: ResourceLifecycle[]}) {
  if (!resources?.length) return null;
  const created = resources.filter((r) => r.resource_id !== null);
  const cleaned = resources.filter((r) => r.status === "CLEANED");
  const unknown = resources.filter((r) => r.status.includes("UNKNOWN") ||
    r.status === "RESOURCE_ID_UNVERIFIED");
  const pending = resources.filter((r) => !["NOT_CREATED", "CLEANED"].includes(r.status));
  return <div className={pending.length ? "finding-card" : "read-probe"} aria-label="本轮数据清理">
    <p>本轮数据：已准备 {created.length} 项，已清理 {cleaned.length} 项。</p>
    {!!pending.length && <p>清理未完成：请按本轮资源证据核实，再安排新的测试。</p>}
    {!!unknown.length && <p>{unknown.length} 项写入结果尚未核实，不会自动重发或猜测资源。</p>}
    <details><summary>查看本轮资源</summary>
      {resources.map((r) => <p key={r.create_step}>
        {r.resource_id === null ? "资源编号尚未核实" : `资源 ${r.resource_id}`} ·
        {r.status === "CLEANED" ? "已清理" : r.status === "NOT_CREATED" ? "尚未创建" : "待处理"}
        <span className="caption"> · 归属本次运行 {r.owner_run_id}</span>
      </p>)}
    </details>
  </div>;
}
