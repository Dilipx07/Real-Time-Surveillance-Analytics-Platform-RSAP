import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthContext";
import { ErrorBanner, formatDate, Panel, StateBlock, StatusPill } from "../components/ui";

export function SettingsScreen() {
  const { api, session } = useAuth();
  const health = useQuery({ queryKey: ["backend-health", "settings"], queryFn: () => api.health(), refetchInterval: 10000 });

  return (
    <div className="screen-grid">
      <Panel title="Desktop Backend">
        {health.isError ? <ErrorBanner error={health.error} /> : null}
        <div className="metric-grid">
          <StateBlock title="API URL" detail={import.meta.env.VITE_RSAP_DESKTOP_API_URL || "http://127.0.0.1:8001"} />
          <StateBlock title="Status" detail={health.data?.status ?? "Unavailable"} />
          <StateBlock title="Database" detail={health.data?.database ?? "Unknown"} />
          <StateBlock title="Sync" detail={health.data?.sync ?? "Unknown"} />
          <StateBlock title="Timestamp" detail={formatDate(health.data?.timestamp)} />
        </div>
      </Panel>
      <Panel title="Session">
        <div className="metric-grid">
          <StateBlock title="User" detail={session?.user.email || session?.user.id || "Authenticated"} />
          <StateBlock title="Role" detail={session?.user.role || "Not reported"} />
          <StateBlock title="Access expires" detail={formatDate(session?.access_expires_at)} />
          <StateBlock title="License" detail={session?.license?.is_active === false ? "Inactive" : "Active or not reported"} />
          <StatusPill value={session?.license?.is_active === false ? "license inactive" : "session active"} />
        </div>
      </Panel>
    </div>
  );
}
