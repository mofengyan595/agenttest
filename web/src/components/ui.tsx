import type { ReactNode } from "react";
import {
  AlertCircle,
  ArrowUpRight,
  Inbox,
  LoaderCircle,
  RefreshCw,
} from "lucide-react";
import { Link } from "react-router";
import type { Outcome } from "../api/types";
import { outcomes } from "../status";

export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: string;
}) {
  return (
    <span className={`badge ${tone}`}>
      <span className="badge-dot" aria-hidden="true" />
      {children}
    </span>
  );
}
export function OutcomeBadge({ value }: { value?: Outcome | null }) {
  const status = value ? outcomes[value] : undefined;
  if (!status) return <Badge>Outcome 未记录</Badge>;
  return (
    <Badge tone={status?.tone}>
      {value} · {status?.label || "未知状态"}
    </Badge>
  );
}
export function PageTitle({
  eyebrow,
  title,
  description,
  children,
}: {
  eyebrow: string;
  title: string;
  description: string;
  children?: ReactNode;
}) {
  return (
    <header className="page-title">
      <div>
        <div className="eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {children}
    </header>
  );
}
export function Panel({
  title,
  subtitle,
  children,
  action,
  className = "",
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      <header className="panel-heading">
        <div>
          <h2>{title}</h2>
          {subtitle && <p>{subtitle}</p>}
        </div>
        {action}
      </header>
      {children}
    </section>
  );
}
export function Notice({
  children,
  error = false,
}: {
  children: ReactNode;
  error?: boolean;
}) {
  return (
    <div
      className={`notice ${error ? "notice-error" : ""}`}
      role={error ? "alert" : "note"}
    >
      <AlertCircle size={17} />
      <div>{children}</div>
    </div>
  );
}
export function Loading() {
  return (
    <div className="state" role="status">
      <LoaderCircle className="spin" size={24} />
      正在读取真实数据…
    </div>
  );
}
export function Empty({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="state">
      <Inbox size={28} />
      <h3>{title}</h3>
      {children}
    </div>
  );
}
export function Failure({
  message,
  retry,
}: {
  message: string;
  retry: () => void;
}) {
  return (
    <div className="state" role="alert">
      <AlertCircle size={26} />
      <h3>{message}</h3>
      <button onClick={retry}>
        <RefreshCw size={15} />
        重试
      </button>
    </div>
  );
}
export function RunLink({ id }: { id: string }) {
  return (
    <Link className="run-link" to={`/runs/${id}`}>
      <code>{id.slice(0, 12)}…</code>
      <ArrowUpRight size={14} />
      <span className="sr-only">查看运行 {id}</span>
    </Link>
  );
}
