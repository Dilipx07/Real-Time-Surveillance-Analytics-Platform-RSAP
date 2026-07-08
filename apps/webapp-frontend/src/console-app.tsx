"use client";

import {
  Activity,
  Camera,
  Gauge,
  KeyRound,
  LogOut,
  Settings,
  ShieldCheck,
  UserRound,
  Users
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";
import type React from "react";
import {
  apiGet,
  apiPost,
  getHealth,
  login,
  type AnalyticsEvent,
  type AuthSession,
  type Camera as CameraRecord,
  type Dashboard,
  type License,
  type Paginated,
  type Person,
  type User
} from "./api";

type View = "dashboard" | "users" | "licenses" | "cameras" | "persons" | "analytics" | "settings";

const nav: Array<{ id: View; label: string; icon: React.ElementType }> = [
  { id: "dashboard", label: "Dashboard", icon: Gauge },
  { id: "users", label: "Users", icon: Users },
  { id: "licenses", label: "Licenses", icon: KeyRound },
  { id: "cameras", label: "Cameras", icon: Camera },
  { id: "persons", label: "Persons", icon: UserRound },
  { id: "analytics", label: "Analytics", icon: Activity },
  { id: "settings", label: "Settings", icon: Settings }
];

export function ConsoleApp() {
  const [session, setSession] = useState<AuthSession | null>(() => {
    if (typeof window === "undefined") {
      return null;
    }
    const raw = window.localStorage.getItem("rsap.session");
    return raw ? (JSON.parse(raw) as AuthSession) : null;
  });
  const [view, setView] = useState<View>("dashboard");

  function persist(next: AuthSession | null) {
    setSession(next);
    if (next) {
      window.localStorage.setItem("rsap.session", JSON.stringify(next));
    } else {
      window.localStorage.removeItem("rsap.session");
    }
  }

  if (!session) {
    return <LoginScreen onLogin={persist} />;
  }

  const active = nav.find((item) => item.id === view) ?? nav[0];

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">R</div>
          <span>RSAP</span>
        </div>
        <nav className="nav" aria-label="Main navigation">
          {nav.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                type="button"
                className={item.id === view ? "active" : ""}
                onClick={() => setView(item.id)}
              >
                <Icon size={18} aria-hidden />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>
      </aside>
      <main className="main">
        <header className="topbar">
          <div>
            <h1>{active.label}</h1>
            <div className="muted">Signed in as {session.user.email}</div>
          </div>
          <div className="actions">
            <HealthBadge />
            <button className="button secondary" type="button" onClick={() => persist(null)}>
              <LogOut size={16} aria-hidden /> Sign out
            </button>
          </div>
        </header>
        <div className="content">
          {view === "dashboard" && <DashboardView session={session} />}
          {view === "users" && <UsersView session={session} />}
          {view === "licenses" && <LicensesView session={session} />}
          {view === "cameras" && <CamerasView session={session} />}
          {view === "persons" && <PersonsView session={session} />}
          {view === "analytics" && <AnalyticsView session={session} />}
          {view === "settings" && <SettingsView />}
        </div>
      </main>
    </div>
  );
}

function LoginScreen({ onLogin }: { onLogin: (session: AuthSession) => void }) {
  const [email, setEmail] = useState("admin@rsap.local");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onLogin(await login(email, password));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-page">
      <section className="login-panel">
        <div className="brand">
          <div className="brand-mark">R</div>
          <span>RSAP</span>
        </div>
        <div>
          <h1>Central surveillance console</h1>
          <p>Authenticate against the central API using JWT plus the Redis-backed session token.</p>
        </div>
        <form className="form" onSubmit={submit}>
          <label className="field">
            Email
            <input value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="username" />
          </label>
          <label className="field">
            Password
            <input
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              type="password"
              autoComplete="current-password"
            />
          </label>
          {error && <div className="error">{error}</div>}
          <button className="button" type="submit" disabled={busy}>
            {busy ? "Signing in..." : "Sign in"}
          </button>
        </form>
      </section>
      <section className="login-context">
        <div className="card">
          <ShieldCheck size={28} color="var(--accent)" aria-hidden />
          <h2>Runtime validation surface</h2>
          <p className="muted">
            This console calls live RSAP endpoints only. Empty screens report not configured instead of showing
            manufactured production data.
          </p>
        </div>
      </section>
    </div>
  );
}

function HealthBadge() {
  const [state, setState] = useState("checking");
  useEffect(() => {
    let alive = true;
    getHealth()
      .then((health) => alive && setState(health.status ?? "ok"))
      .catch(() => alive && setState("offline"));
    return () => {
      alive = false;
    };
  }, []);
  return <span className={`badge ${state === "ok" ? "ok" : "warn"}`}>API {state}</span>;
}

function DashboardView({ session }: { session: AuthSession }) {
  const { data, error } = useResource(() => apiGet<Dashboard>("/analytics/dashboard", session), [session]);
  const metrics = [
    ["Registered persons", data?.total_persons],
    ["Today entries", data?.today_entries],
    ["Active cameras", data?.active_cameras],
    ["Open alerts", data?.open_alerts]
  ];
  return (
    <>
      {error && <div className="error">{error}</div>}
      <div className="grid">
        {metrics.map(([label, value]) => (
          <div className="card metric" key={label}>
            <span>{label}</span>
            <strong>{typeof value === "number" ? value : "Not configured"}</strong>
          </div>
        ))}
      </div>
      <AnalyticsView session={session} compact />
    </>
  );
}

function UsersView({ session }: { session: AuthSession }) {
  const [role, setRole] = useState<"staff" | "va_user">("staff");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const resource = useResource(() => apiGet<Paginated<User>>("/users/", session), [session, message]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setMessage(null);
    await apiPost<User>("/users/", session, { email, password, role });
    setEmail("");
    setPassword("");
    setMessage("User created.");
  }

  return (
    <div className="split">
      <form className="card form" onSubmit={submit}>
        <div className="section-head"><h2>Create user</h2></div>
        <label className="field">Email<input value={email} onChange={(event) => setEmail(event.target.value)} /></label>
        <label className="field">Password<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} /></label>
        <label className="field">Role<select value={role} onChange={(event) => setRole(event.target.value as "staff" | "va_user")}><option value="staff">Staff</option><option value="va_user">VA user</option></select></label>
        {message && <span className="badge ok">{message}</span>}
        <button className="button" type="submit">Create</button>
      </form>
      <DataTable title="Users" error={resource.error} empty="No users are configured." rows={resource.data?.items ?? []} columns={["email", "role", "is_active", "created_at"]} />
    </div>
  );
}

function LicensesView({ session }: { session: AuthSession }) {
  const resource = useResource(() => apiGet<Paginated<License>>("/licenses/", session), [session]);
  return <DataTable title="Licenses" error={resource.error} empty="No licenses are configured." rows={resource.data?.items ?? []} columns={["user_id", "max_cameras", "is_active", "valid_until"]} />;
}

function CamerasView({ session }: { session: AuthSession }) {
  const resource = useResource(() => apiGet<Paginated<CameraRecord>>("/cameras/", session), [session]);
  return <DataTable title="Cameras" error={resource.error} empty="No cameras are configured." rows={resource.data?.items ?? []} columns={["name", "stream_type", "location_label", "is_active"]} />;
}

function PersonsView({ session }: { session: AuthSession }) {
  const resource = useResource(() => apiGet<Paginated<Person>>("/persons/", session), [session]);
  return <DataTable title="Registered persons" error={resource.error} empty="No persons are registered." rows={resource.data?.items ?? []} columns={["full_name", "phone", "entry_status", "created_at"]} />;
}

function AnalyticsView({ session, compact = false }: { session: AuthSession; compact?: boolean }) {
  const resource = useResource(() => apiGet<Paginated<AnalyticsEvent>>("/analytics/events", session), [session]);
  return <DataTable title={compact ? "Recent analytics" : "Analytics events"} error={resource.error} empty="No analytics events are configured." rows={resource.data?.items ?? []} columns={["camera_name", "event_type", "created_at"]} />;
}

function SettingsView() {
  const api = useMemo(() => process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1", []);
  return (
    <div className="card">
      <div className="section-head">
        <div>
          <h2>Runtime settings</h2>
          <p>Central API URL: {api}</p>
        </div>
      </div>
    </div>
  );
}

function DataTable<T extends Record<string, unknown>>({
  title,
  rows,
  columns,
  empty,
  error
}: {
  title: string;
  rows: T[];
  columns: string[];
  empty: string;
  error: string | null;
}) {
  return (
    <section className="table-wrap">
      <div className="card section-head">
        <div>
          <h2>{title}</h2>
          <p>{rows.length} records returned</p>
        </div>
      </div>
      {error && <div className="error">{error}</div>}
      {!error && rows.length === 0 ? (
        <div className="empty">{empty}</div>
      ) : (
        <table>
          <thead>
            <tr>{columns.map((column) => <th key={column}>{column.replaceAll("_", " ")}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={String(row.id ?? index)}>
                {columns.map((column) => <td key={column}>{formatCell(row[column])}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function formatCell(value: unknown) {
  if (value === null || value === undefined || value === "") {
    return "Not configured";
  }
  if (typeof value === "boolean") {
    return value ? "Yes" : "No";
  }
  return String(value);
}

function useResource<T>(loader: () => Promise<T>, deps: React.DependencyList) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    loader()
      .then((value) => alive && setData(value))
      .catch((exc) => alive && setError(exc instanceof Error ? exc.message : "Request failed"));
    return () => {
      alive = false;
    };
  // The caller owns the dependency list so resources can be reloaded after mutations.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, error };
}
