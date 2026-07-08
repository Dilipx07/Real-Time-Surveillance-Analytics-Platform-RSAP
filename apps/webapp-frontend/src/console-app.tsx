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
  apiDelete,
  apiGet,
  apiPatch,
  apiPost,
  apiPostForm,
  getHealth,
  login,
  type Alert,
  type AnalyticsEvent,
  type AuthSession,
  type Camera as CameraRecord,
  type Dashboard,
  type License,
  type Paginated,
  type Permission,
  type Person,
  type User
} from "./api";

type View = "dashboard" | "users" | "licenses" | "cameras" | "persons" | "analytics" | "settings";

const nav: Array<{ id: View; label: string; icon: React.ElementType; roles: string[] }> = [
  { id: "dashboard", label: "Dashboard", icon: Gauge, roles: ["super_admin", "admin"] },
  { id: "users", label: "Users", icon: Users, roles: ["super_admin", "admin"] },
  { id: "licenses", label: "Licenses", icon: KeyRound, roles: ["super_admin", "admin"] },
  { id: "cameras", label: "Cameras", icon: Camera, roles: ["super_admin", "admin", "va_user"] },
  { id: "persons", label: "Persons", icon: UserRound, roles: ["super_admin", "admin", "staff"] },
  { id: "analytics", label: "Analytics", icon: Activity, roles: ["super_admin", "admin", "va_user"] },
  { id: "settings", label: "Settings", icon: Settings, roles: ["super_admin", "admin", "staff", "va_user"] }
];

const analyticsModules = ["intrusion", "loitering", "people_count", "face_match"];
const featureKeys = ["cameras", "persons", "analytics", "alerts"];

export function ConsoleApp() {
  const [session, setSession] = useState<AuthSession | null>(null);
  const [view, setView] = useState<View>("dashboard");
  const visibleNav = useMemo(
    () => nav.filter((item) => !session || item.roles.includes(session.user.role)),
    [session]
  );

  useEffect(() => {
    const loadStoredSession = window.setTimeout(() => {
      const raw = window.localStorage.getItem("rsap.session");
      setSession(raw ? (JSON.parse(raw) as AuthSession) : null);
      setView(readHashView());
    }, 0);
    return () => window.clearTimeout(loadStoredSession);
  }, []);

  useEffect(() => {
    function syncHashView() {
      setView(readHashView());
    }
    window.addEventListener("hashchange", syncHashView);
    return () => window.removeEventListener("hashchange", syncHashView);
  }, []);

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

  const active = visibleNav.find((item) => item.id === view) ?? visibleNav[0] ?? nav[0];

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">R</div>
          <span>RSAP</span>
        </div>
        <nav className="nav" aria-label="Main navigation">
          {visibleNav.map((item) => {
            const Icon = item.icon;
            return (
              <a
                key={item.id}
                href={`#${item.id}`}
                className={item.id === active.id ? "active" : ""}
              >
                <Icon size={18} aria-hidden />
                <span>{item.label}</span>
              </a>
            );
          })}
        </nav>
      </aside>
      <main className="main">
        <header className="topbar">
          <div>
            <h1>{active.label}</h1>
            <div className="muted">Signed in as {session.user.email} ({session.user.role})</div>
          </div>
          <div className="actions">
            <HealthBadge />
            <button className="button secondary" type="button" onClick={() => persist(null)}>
              <LogOut size={16} aria-hidden /> Sign out
            </button>
          </div>
        </header>
        <div className="content">
          {active.id === "dashboard" && <DashboardView session={session} onSessionExpired={() => persist(null)} />}
          {active.id === "users" && <UsersView session={session} />}
          {active.id === "licenses" && <LicensesView session={session} />}
          {active.id === "cameras" && <CamerasView session={session} />}
          {active.id === "persons" && <PersonsView session={session} />}
          {active.id === "analytics" && <AnalyticsView session={session} />}
          {active.id === "settings" && <SettingsView session={session} />}
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

function DashboardView({ session, onSessionExpired }: { session: AuthSession; onSessionExpired: () => void }) {
  const { data, error, reload } = useResource(() => apiGet<Dashboard>("/analytics/dashboard", session), [session]);
  useEffect(() => {
    if (!isSessionExpired(error)) {
      return;
    }
    const clearExpiredSession = window.setTimeout(onSessionExpired, 0);
    return () => window.clearTimeout(clearExpiredSession);
  }, [error, onSessionExpired]);
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
      <AnalyticsView session={session} compact onChanged={reload} />
    </>
  );
}

function UsersView({ session }: { session: AuthSession }) {
  const [role, setRole] = useState<"staff" | "va_user">("staff");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [phone, setPhone] = useState("");
  const [whatsapp, setWhatsapp] = useState("");
  const [selected, setSelected] = useState<User | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const resource = useResource(() => apiGet<Paginated<User>>("/users/", session), [session]);

  async function run(action: () => Promise<unknown>, success: string) {
    setError(null);
    try {
      await action();
      setNotice(success);
      resource.reload();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Request failed");
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    await run(async () => {
      await apiPost<User>("/users/", session, {
        email,
        password,
        role,
        phone: phone || undefined,
        whatsapp_number: whatsapp || undefined
      });
      setEmail("");
      setPassword("");
      setPhone("");
      setWhatsapp("");
    }, "User created.");
  }

  return (
    <div className="split wide">
      <section className="stack">
        <form className="card form" onSubmit={submit}>
          <div className="section-head"><h2>Create user</h2></div>
          <label className="field">Email<input value={email} onChange={(event) => setEmail(event.target.value)} /></label>
          <label className="field">Password<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} /></label>
          <label className="field">Phone<input value={phone} onChange={(event) => setPhone(event.target.value)} /></label>
          <label className="field">WhatsApp<input value={whatsapp} onChange={(event) => setWhatsapp(event.target.value)} /></label>
          <label className="field">
            Role
            <select value={role} onChange={(event) => setRole(event.target.value as "staff" | "va_user")}>
              <option value="staff">Staff</option>
              <option value="va_user">VA user</option>
            </select>
          </label>
          <button className="button" type="submit">Create</button>
        </form>
        <UserPermissions session={session} user={selected} />
      </section>
      <DataTable
        title="Users"
        error={resource.error ?? error}
        notice={notice}
        empty="No users are configured."
        rows={resource.data?.items ?? []}
        columns={["email", "role", "phone", "is_active", "created_at"]}
        actions={(user) => (
          <RowActions>
            <button type="button" onClick={() => setSelected(user)}>Permissions</button>
            <button
              type="button"
              onClick={() => run(() => apiPatch<User>(`/users/${user.id}/toggle-active`, session, {}), "User status updated.")}
            >
              {user.is_active ? "Disable" : "Enable"}
            </button>
            {user.id !== session.user.id && (
              <button
                type="button"
                className="danger-link"
                onClick={() => run(() => apiDelete<{ deleted: boolean }>(`/users/${user.id}`, session), "User deleted.")}
              >
                Delete
              </button>
            )}
          </RowActions>
        )}
      />
    </div>
  );
}

function UserPermissions({ session, user }: { session: AuthSession; user: User | null }) {
  const [resource, setResource] = useState("persons");
  const [actions, setActions] = useState("read");
  const [constraints, setConstraints] = useState("{}");
  const [version, setVersion] = useState(0);
  const permissions = useResource(
    () => user ? apiGet<Permission[]>(`/users/${user.id}/permissions`, session) : Promise.resolve([]),
    [session, user?.id, version]
  );
  const [error, setError] = useState<string | null>(null);

  if (!user) {
    return <div className="card muted">Select a user to review and grant scoped permissions.</div>;
  }

  async function grant(event: FormEvent) {
    event.preventDefault();
    const target = user;
    if (!target) {
      return;
    }
    setError(null);
    try {
      await apiPost<Permission>(`/users/${target.id}/permissions`, session, {
        resource,
        actions: actions.split(",").map((item) => item.trim()).filter(Boolean),
        constraints: parseJsonObject(constraints)
      });
      setVersion((value) => value + 1);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Permission update failed");
    }
  }

  return (
    <section className="card form">
      <div>
        <h2>Permissions</h2>
        <p className="muted">{user.email}</p>
      </div>
      {error && <div className="error">{error}</div>}
      <form className="form compact-form" onSubmit={grant}>
        <label className="field">Resource<input value={resource} onChange={(event) => setResource(event.target.value)} /></label>
        <label className="field">Actions<input value={actions} onChange={(event) => setActions(event.target.value)} /></label>
        <label className="field">Constraints JSON<textarea value={constraints} onChange={(event) => setConstraints(event.target.value)} /></label>
        <button className="button" type="submit">Grant permission</button>
      </form>
      <div className="mini-list">
        {(permissions.data ?? []).length === 0 && <span className="muted">No scoped permissions configured.</span>}
        {(permissions.data ?? []).map((permission) => (
          <div key={permission.id} className="mini-row">
            <span>{permission.resource}: {permission.actions.join(", ")}</span>
            <button
              type="button"
              onClick={async () => {
                await apiDelete<{ deleted: boolean }>(`/users/${user.id}/permissions/${permission.id}`, session);
                setVersion((value) => value + 1);
              }}
            >
              Revoke
            </button>
          </div>
        ))}
      </div>
    </section>
  );
}

function LicensesView({ session }: { session: AuthSession }) {
  const users = useResource(() => apiGet<Paginated<User>>("/users/?page_size=100", session), [session]);
  const resource = useResource(() => apiGet<Paginated<License>>("/licenses/", session), [session]);
  const [userId, setUserId] = useState("");
  const [validUntil, setValidUntil] = useState(tomorrowIsoDate());
  const [maxCameras, setMaxCameras] = useState(4);
  const [features, setFeatures] = useState(() => Object.fromEntries(featureKeys.map((key) => [key, true])));
  const [modules, setModules] = useState<string[]>(["intrusion", "people_count"]);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const userMap = new Map((users.data?.items ?? []).map((user) => [user.id, user.email]));

  const effectiveUserId = userId || users.data?.items?.[0]?.id || "";

  async function run(action: () => Promise<unknown>, success: string) {
    setError(null);
    try {
      await action();
      setNotice(success);
      resource.reload();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Request failed");
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    await run(() => apiPost<License>("/licenses/", session, {
      user_id: effectiveUserId,
      valid_from: new Date().toISOString(),
      valid_until: new Date(`${validUntil}T23:59:59.000Z`).toISOString(),
      max_cameras: maxCameras,
      features,
      analytics_modules: modules
    }), "License created.");
  }

  return (
    <div className="split wide">
      <form className="card form" onSubmit={submit}>
        <div className="section-head"><h2>Create license</h2></div>
        <label className="field">
          User
          <select value={effectiveUserId} onChange={(event) => setUserId(event.target.value)}>
            {(users.data?.items ?? []).map((user) => <option key={user.id} value={user.id}>{user.email}</option>)}
          </select>
        </label>
        <label className="field">Valid until<input type="date" value={validUntil} onChange={(event) => setValidUntil(event.target.value)} /></label>
        <label className="field">Max cameras<input type="number" min={1} max={64} value={maxCameras} onChange={(event) => setMaxCameras(Number(event.target.value))} /></label>
        <Checklist title="Features" values={featureKeys} selected={Object.keys(features).filter((key) => features[key])} onChange={(next) => setFeatures(Object.fromEntries(featureKeys.map((key) => [key, next.includes(key)])))} />
        <Checklist title="Analytics modules" values={analyticsModules} selected={modules} onChange={setModules} />
        <button className="button" type="submit" disabled={!effectiveUserId}>Create</button>
      </form>
      <DataTable
        title="Licenses"
        error={resource.error ?? users.error ?? error}
        notice={notice}
        empty="No licenses are configured."
        rows={(resource.data?.items ?? []).map((license) => ({ ...license, user_email: userMap.get(license.user_id) ?? license.user_id }))}
        columns={["user_email", "max_cameras", "analytics_modules", "is_active", "valid_until"]}
        actions={(license) => (
          <RowActions>
            <button type="button" onClick={() => run(() => apiPatch<License>(`/licenses/${license.id}`, session, { valid_until: addDaysIso(license.valid_until, 30) }), "License extended.")}>Extend 30d</button>
            <button type="button" className="danger-link" onClick={() => run(() => apiDelete<{ expired: boolean }>(`/licenses/${license.id}/expire`, session), "License expired.")}>Expire</button>
          </RowActions>
        )}
      />
    </div>
  );
}

function CamerasView({ session }: { session: AuthSession }) {
  const resource = useResource(() => apiGet<Paginated<CameraRecord>>("/cameras/", session), [session]);
  const [name, setName] = useState("");
  const [streamUrl, setStreamUrl] = useState("");
  const [streamType, setStreamType] = useState<"rtsp" | "webcam" | "nvr">("rtsp");
  const [location, setLocation] = useState("");
  const [analytics, setAnalytics] = useState<string[]>(["intrusion"]);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run(action: () => Promise<unknown>, success: string) {
    setError(null);
    try {
      await action();
      setNotice(success);
      resource.reload();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Request failed");
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    await run(async () => {
      await apiPost<CameraRecord>("/cameras/", session, {
        name,
        stream_url: streamUrl,
        stream_type: streamType,
        location_label: location || undefined,
        analytics_config: moduleConfig(analytics),
        zones: []
      });
      setName("");
      setStreamUrl("");
      setLocation("");
    }, "Camera created.");
  }

  return (
    <div className="split wide">
      <form className="card form" onSubmit={submit}>
        <div className="section-head"><h2>Add camera</h2></div>
        <label className="field">Name<input value={name} onChange={(event) => setName(event.target.value)} /></label>
        <label className="field">Stream URL<input value={streamUrl} onChange={(event) => setStreamUrl(event.target.value)} /></label>
        <label className="field">
          Stream type
          <select value={streamType} onChange={(event) => setStreamType(event.target.value as "rtsp" | "webcam" | "nvr")}>
            <option value="rtsp">RTSP</option>
            <option value="webcam">Webcam</option>
            <option value="nvr">NVR</option>
          </select>
        </label>
        <label className="field">Location<input value={location} onChange={(event) => setLocation(event.target.value)} /></label>
        <Checklist title="Analytics config" values={analyticsModules} selected={analytics} onChange={setAnalytics} />
        <button className="button" type="submit">Add camera</button>
      </form>
      <DataTable
        title="Cameras"
        error={resource.error ?? error}
        notice={notice}
        empty="No cameras are configured."
        rows={resource.data?.items ?? []}
        columns={["name", "stream_type", "location_label", "analytics_config", "is_active"]}
        actions={(camera) => (
          <RowActions>
            <button type="button" onClick={() => run(() => apiPatch<CameraRecord>(`/cameras/${camera.id}`, session, { is_active: !camera.is_active }), "Camera status updated.")}>{camera.is_active ? "Disable" : "Enable"}</button>
            <button type="button" onClick={() => run(() => apiPatch<CameraRecord>(`/cameras/${camera.id}/analytics-config`, session, { analytics_config: moduleConfig(analytics), zones: camera.zones ?? [] }), "Analytics config saved.")}>Apply config</button>
            <button type="button" className="danger-link" onClick={() => run(() => apiDelete<{ deleted: boolean }>(`/cameras/${camera.id}`, session), "Camera deleted.")}>Delete</button>
          </RowActions>
        )}
      />
    </div>
  );
}

function PersonsView({ session }: { session: AuthSession }) {
  const resource = useResource(() => apiGet<Paginated<Person>>("/persons/", session), [session]);
  const [fullName, setFullName] = useState("");
  const [phone, setPhone] = useState("");
  const [aadhaarLast4, setAadhaarLast4] = useState("");
  const [faceImage, setFaceImage] = useState<File | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run(action: () => Promise<unknown>, success: string) {
    setError(null);
    try {
      await action();
      setNotice(success);
      resource.reload();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Request failed");
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    await run(async () => {
      if (!faceImage) {
        throw new Error("Face image is required.");
      }
      const body = new FormData();
      body.set("full_name", fullName);
      body.set("phone", phone);
      body.set("aadhaar_last4", aadhaarLast4);
      body.set("face_image", faceImage);
      await apiPostForm<Person>("/persons/", session, body);
      setFullName("");
      setPhone("");
      setAadhaarLast4("");
      setFaceImage(null);
    }, "Person registered.");
  }

  return (
    <div className="split wide">
      <form className="card form" onSubmit={submit}>
        <div className="section-head"><h2>Register person</h2></div>
        <label className="field">Full name<input value={fullName} onChange={(event) => setFullName(event.target.value)} /></label>
        <label className="field">Phone<input value={phone} onChange={(event) => setPhone(event.target.value)} /></label>
        <label className="field">Aadhaar last 4<input maxLength={4} value={aadhaarLast4} onChange={(event) => setAadhaarLast4(event.target.value)} /></label>
        <label className="field">Face image<input type="file" accept="image/png,image/jpeg" onChange={(event) => setFaceImage(event.target.files?.[0] ?? null)} /></label>
        <button className="button" type="submit">Register</button>
      </form>
      <DataTable
        title="Registered persons"
        error={resource.error ?? error}
        notice={notice}
        empty="No persons are registered."
        rows={resource.data?.items ?? []}
        columns={["full_name", "phone", "aadhaar_last4", "entry_status", "created_at"]}
        actions={(person) => (
          <RowActions>
            <button type="button" className="danger-link" onClick={() => run(() => apiDelete<{ deleted: boolean }>(`/persons/${person.id}`, session), "Person deleted.")}>Delete</button>
          </RowActions>
        )}
      />
    </div>
  );
}

function AnalyticsView({ session, compact = false, onChanged }: { session: AuthSession; compact?: boolean; onChanged?: () => void }) {
  const [resolvedFilter, setResolvedFilter] = useState<"open" | "all">("open");
  const events = useResource(() => apiGet<Paginated<AnalyticsEvent>>("/analytics/events", session), [session]);
  const alerts = useResource(
    () => apiGet<Paginated<Alert>>(`/analytics/alerts${resolvedFilter === "open" ? "?resolved=false" : ""}`, session),
    [session, resolvedFilter]
  );
  const [error, setError] = useState<string | null>(null);

  async function resolve(alert: Alert) {
    setError(null);
    try {
      await apiPatch<Alert>(`/analytics/alerts/${alert.id}/resolve`, session, {});
      alerts.reload();
      onChanged?.();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Alert resolve failed");
    }
  }

  return (
    <div className={compact ? "stack" : "stack roomy"}>
      <DataTable
        title={compact ? "Recent analytics" : "Analytics events"}
        error={events.error}
        empty="No analytics events are configured."
        rows={events.data?.items ?? []}
        columns={compact ? ["camera_name", "event_type", "created_at"] : ["camera_name", "event_type", "payload", "created_at"]}
      />
      {!compact && (
        <div className="filter-row">
          <label className="field inline-field">
            Alert filter
            <select value={resolvedFilter} onChange={(event) => setResolvedFilter(event.target.value as "open" | "all")}>
              <option value="open">Open alerts</option>
              <option value="all">All alerts</option>
            </select>
          </label>
        </div>
      )}
      <DataTable
        title={compact ? "Open alerts" : "Intrusion alerts"}
        error={alerts.error ?? error}
        empty="No alerts are configured."
        rows={alerts.data?.items ?? []}
        columns={["camera_name", "zone_id", "confidence", "resolved", "created_at"]}
        actions={(alert) => !alert.resolved && (
          <RowActions>
            <button type="button" onClick={() => resolve(alert)}>Resolve</button>
          </RowActions>
        )}
      />
    </div>
  );
}

function SettingsView({ session }: { session: AuthSession }) {
  const api = useMemo(() => process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1", []);
  return (
    <div className="card">
      <div className="section-head">
        <div>
          <h2>Runtime settings</h2>
          <p>Central API URL: {api}</p>
        </div>
      </div>
      <div className="settings-grid">
        <div><strong>User</strong><span>{session.user.email}</span></div>
        <div><strong>Role</strong><span>{session.user.role}</span></div>
        <div><strong>Session token</strong><span>{session.session_token ? "Present" : "Missing"}</span></div>
        <div><strong>Data policy</strong><span>Live API data only; empty states mean not configured.</span></div>
      </div>
    </div>
  );
}

function DataTable<T extends Record<string, unknown>>({
  title,
  rows,
  columns,
  empty,
  error,
  notice,
  actions
}: {
  title: string;
  rows: T[];
  columns: string[];
  empty: string;
  error: string | null;
  notice?: string | null;
  actions?: (row: T) => React.ReactNode;
}) {
  const hasActions = Boolean(actions);
  return (
    <section className="table-wrap">
      <div className="section-head table-head">
        <div>
          <h2>{title}</h2>
          <p>{rows.length} records returned</p>
        </div>
      </div>
      {notice && <div className="notice">{notice}</div>}
      {error && <div className="error">{error}</div>}
      {!error && rows.length === 0 ? (
        <div className="empty">{empty}</div>
      ) : (
        <table>
          <thead>
            <tr>
              {columns.map((column) => <th key={column}>{column.replaceAll("_", " ")}</th>)}
              {hasActions && <th>Actions</th>}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={String(row.id ?? index)}>
                {columns.map((column) => <td key={column}>{formatCell(row[column])}</td>)}
                {hasActions && <td>{actions?.(row)}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function Checklist({
  title,
  values,
  selected,
  onChange
}: {
  title: string;
  values: string[];
  selected: string[];
  onChange: (next: string[]) => void;
}) {
  return (
    <fieldset className="checklist">
      <legend>{title}</legend>
      {values.map((value) => (
        <label key={value}>
          <input
            type="checkbox"
            checked={selected.includes(value)}
            onChange={(event) => {
              onChange(event.target.checked ? [...selected, value] : selected.filter((item) => item !== value));
            }}
          />
          <span>{value.replaceAll("_", " ")}</span>
        </label>
      ))}
    </fieldset>
  );
}

function RowActions({ children }: { children: React.ReactNode }) {
  return <div className="row-actions">{children}</div>;
}

function formatCell(value: unknown): React.ReactNode {
  if (value === null || value === undefined || value === "") {
    return "Not configured";
  }
  if (typeof value === "boolean") {
    return value ? "Yes" : "No";
  }
  if (Array.isArray(value)) {
    return value.length ? value.join(", ") : "Not configured";
  }
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.length === 0) {
      return "Not configured";
    }
    return entries.map(([key, item]) => `${key}: ${stringifyCell(item)}`).join("; ");
  }
  return String(value);
}

function useResource<T>(loader: () => Promise<T>, deps: React.DependencyList) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  useEffect(() => {
    let alive = true;
    loader()
      .then((value) => {
        if (alive) {
          setData(value);
        }
      })
      .catch((exc) => {
        if (alive) {
          setError(exc instanceof Error ? exc.message : "Request failed");
        }
      });
    return () => {
      alive = false;
    };
  // The caller owns the dependency list so resources can be reloaded after mutations.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, version]);
  return { data, error, reload: () => setVersion((value) => value + 1) };
}

function stringifyCell(value: unknown): string {
  const formatted = formatCell(value);
  return typeof formatted === "string" || typeof formatted === "number" ? String(formatted) : "";
}

function moduleConfig(modules: string[]) {
  return Object.fromEntries(analyticsModules.map((module) => [module, modules.includes(module)]));
}

function parseJsonObject(raw: string) {
  const parsed = JSON.parse(raw);
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
    throw new Error("Constraints must be a JSON object.");
  }
  return parsed as Record<string, unknown>;
}

function tomorrowIsoDate() {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() + 30);
  return date.toISOString().slice(0, 10);
}

function addDaysIso(current: string, days: number) {
  const date = new Date(current);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString();
}

function isSessionExpired(error: string | null) {
  return Boolean(error && /inactive|unavailable|session|token|unauthorized/i.test(error));
}

function readHashView(): View {
  const value = window.location.hash.replace("#", "");
  return nav.some((item) => item.id === value) ? (value as View) : "dashboard";
}
