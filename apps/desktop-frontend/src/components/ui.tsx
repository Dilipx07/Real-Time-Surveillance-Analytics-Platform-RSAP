import type { ReactNode } from "react";
import { ApiError } from "../api/client";

export function formatError(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "Request failed.";
}

export function formatDate(value: string | null | undefined): string {
  if (!value) {
    return "Not reported";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}

export function payloadSummary(payload: Record<string, unknown>): string {
  const entries = Object.entries(payload).slice(0, 4);
  if (entries.length === 0) {
    return "No payload fields";
  }
  return entries
    .map(([key, value]) => `${key}: ${typeof value === "object" ? JSON.stringify(value) : String(value)}`)
    .join(" | ");
}

export function Panel({
  title,
  action,
  children
}: {
  title: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="panel">
      <div className="panel-header">
        <h2>{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

export function StateBlock({ title, detail }: { title: string; detail?: string }) {
  return (
    <div className="state-block">
      <strong>{title}</strong>
      {detail ? <span>{detail}</span> : null}
    </div>
  );
}

export function ErrorBanner({ error }: { error: unknown }) {
  return (
    <div role="alert" className="error-banner">
      {formatError(error)}
    </div>
  );
}

export function StatusPill({ value }: { value: string | boolean | null | undefined }) {
  const label = value === true ? "online" : value === false ? "offline" : value || "unknown";
  return <span className={`status-pill status-${String(label).toLowerCase().replace(/[^a-z0-9]+/g, "-")}`}>{label}</span>;
}
