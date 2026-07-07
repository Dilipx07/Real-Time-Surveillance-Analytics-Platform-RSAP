import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthContext";
import { ErrorBanner, formatDate, Panel, StateBlock, StatusPill } from "../components/ui";

export function DashboardScreen() {
  const { api } = useAuth();
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: () => api.listCameras() });
  const orchestration = useQuery({ queryKey: ["orchestration-health"], queryFn: () => api.orchestrationHealth() });
  const sync = useQuery({ queryKey: ["sync-status"], queryFn: () => api.syncStatus() });
  const cameraItems = cameras.data?.items ?? [];
  const runtimeCameras = orchestration.data?.cameras ?? [];

  return (
    <div className="screen-grid">
      <Panel title="Runtime Overview">
        {orchestration.isError ? <ErrorBanner error={orchestration.error} /> : null}
        <div className="metric-grid">
          <StateBlock title="Orchestration" detail={orchestration.data?.status || "Checking"} />
          <StateBlock title="Active cameras" detail={String(orchestration.data?.active_cameras ?? 0)} />
          <StateBlock title="Failed cameras" detail={String(orchestration.data?.failed_cameras ?? 0)} />
          <StateBlock title="Last reconciliation" detail={formatDate(orchestration.data?.service?.last_reconciliation_at)} />
        </div>
      </Panel>
      <Panel title="Camera Estate">
        {cameras.isError ? <ErrorBanner error={cameras.error} /> : null}
        <div className="metric-grid">
          <StateBlock title="Configured" detail={String(cameras.data?.total ?? 0)} />
          <StateBlock
            title="Active"
            detail={String(cameraItems.filter((camera) => camera.is_active).length)}
          />
          <StateBlock
            title="Pending sync"
            detail={String(cameraItems.filter((camera) => camera.sync_state !== "synced").length)}
          />
        </div>
      </Panel>
      <Panel title="Sync">
        {sync.isError ? <ErrorBanner error={sync.error} /> : null}
        <div className="metric-grid">
          <StateBlock title="Connectivity" detail={sync.data?.connected ? "Connected" : "Offline"} />
          <StateBlock title="Queue" detail={String(sync.data?.queue_count ?? 0)} />
          <StateBlock title="Dead letters" detail={String(sync.data?.dead_letter_count ?? 0)} />
        </div>
      </Panel>
      <Panel title="Camera Runtime States">
        {!runtimeCameras.length ? (
          <StateBlock title="No runtime statuses" detail="Start a configured camera to publish orchestration status." />
        ) : (
          <div className="status-list">
            {runtimeCameras.map((camera) => (
              <div className="status-row" key={camera.camera_id}>
                <span>{camera.camera_id}</span>
                <StatusPill value={camera.lifecycle_state} />
                <StatusPill value={camera.health} />
              </div>
            ))}
          </div>
        )}
      </Panel>
    </div>
  );
}
