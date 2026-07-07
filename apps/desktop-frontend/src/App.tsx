import { Camera, Gauge, Home, LogOut, RefreshCcw, ServerCog, Settings, ShieldAlert, type LucideIcon } from "lucide-react";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "./auth/AuthContext";
import { LoginScreen } from "./screens/LoginScreen";
import { DashboardScreen } from "./screens/DashboardScreen";
import { CamerasScreen } from "./screens/CamerasScreen";
import { OrchestrationScreen } from "./screens/OrchestrationScreen";
import { AnalyticsScreen } from "./screens/AnalyticsScreen";
import { SyncScreen } from "./screens/SyncScreen";
import { SettingsScreen } from "./screens/SettingsScreen";
import { ErrorBanner, StatusPill } from "./components/ui";

type Route = "dashboard" | "cameras" | "orchestration" | "analytics" | "sync" | "settings";

const navigation: Array<{ id: Route; label: string; icon: LucideIcon }> = [
  { id: "dashboard", label: "Dashboard", icon: Home },
  { id: "cameras", label: "Cameras", icon: Camera },
  { id: "orchestration", label: "Orchestration", icon: ServerCog },
  { id: "analytics", label: "Analytics", icon: Gauge },
  { id: "sync", label: "Sync", icon: RefreshCcw },
  { id: "settings", label: "Runtime", icon: Settings }
];

export function App() {
  const { isAuthenticated, session, logout, api } = useAuth();
  const [route, setRoute] = useState<Route>("dashboard");
  const health = useQuery({
    queryKey: ["backend-health", isAuthenticated],
    queryFn: () => api.health(),
    refetchInterval: 10000,
    enabled: isAuthenticated
  });

  if (!isAuthenticated) {
    return <LoginScreen />;
  }

  const screen =
    route === "dashboard" ? (
      <DashboardScreen />
    ) : route === "cameras" ? (
      <CamerasScreen />
    ) : route === "orchestration" ? (
      <OrchestrationScreen />
    ) : route === "analytics" ? (
      <AnalyticsScreen />
    ) : route === "sync" ? (
      <SyncScreen />
    ) : (
      <SettingsScreen />
    );

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-brand">
          <ShieldAlert aria-hidden="true" />
          <div>
            <strong>RSAP</strong>
            <span>Desktop Ops</span>
          </div>
        </div>
        <nav aria-label="Primary">
          {navigation.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                className={route === item.id ? "nav-item active" : "nav-item"}
                onClick={() => setRoute(item.id)}
                type="button"
              >
                <Icon aria-hidden="true" />
                {item.label}
              </button>
            );
          })}
        </nav>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <div>
            <span className="eyeline">Operator session</span>
            <strong>{session?.user.email || "Authenticated user"}</strong>
          </div>
          <div className="topbar-status">
            {health.isError ? <StatusPill value="backend offline" /> : <StatusPill value={health.data?.sync || "checking"} />}
            <button type="button" className="ghost-button" onClick={() => void logout()}>
              <LogOut aria-hidden="true" />
              Logout
            </button>
          </div>
        </header>
        {health.isError ? <ErrorBanner error={health.error} /> : null}
        <main className="content">{screen}</main>
      </div>
    </div>
  );
}
