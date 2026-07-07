import { Pause, Play, RotateCcw } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthContext";
import type { OrchestrationCameraStatus } from "../api/types";
import { ErrorBanner, formatDate, Panel, StateBlock, StatusPill } from "../components/ui";

export function OrchestrationScreen() {
  const { api } = useAuth();
  const queryClient = useQueryClient();
  const health = useQuery({ queryKey: ["orchestration-health"], queryFn: () => api.orchestrationHealth(), refetchInterval: 5000 });
  const cameras = useQuery({ queryKey: ["orchestration-cameras"], queryFn: () => api.orchestrationCameras(), refetchInterval: 5000 });
  const operation = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "start" | "stop" | "restart" }) => api.cameraOperation(id, action),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["orchestration-health"] });
      void queryClient.invalidateQueries({ queryKey: ["orchestration-cameras"] });
    }
  });

  return (
    <div className="stack">
      <Panel title="Orchestration Health">
        {health.isError ? <ErrorBanner error={health.error} /> : null}
        <div className="metric-grid">
          <StateBlock title="Health" detail={health.data?.status ?? "Checking"} />
          <StateBlock title="Active" detail={String(health.data?.active_cameras ?? 0)} />
          <StateBlock title="Failed" detail={String(health.data?.failed_cameras ?? 0)} />
          <StateBlock title="Service running" detail={health.data?.service?.running ? "Yes" : "No"} />
        </div>
      </Panel>
      <Panel title="Camera Lifecycle">
        {cameras.isError ? <ErrorBanner error={cameras.error} /> : null}
        {operation.isError ? <ErrorBanner error={operation.error} /> : null}
        {operation.data ? (
          <div className="info-banner" role="status">
            {operation.data.operation} outcome: {operation.data.outcome}
          </div>
        ) : null}
        {cameras.data?.length === 0 ? <StateBlock title="No orchestration statuses" detail="No camera worker has published status yet." /> : null}
        <div className="orchestration-grid">
          {cameras.data?.map((camera) => (
            <CameraRuntimeCard
              key={camera.camera_id}
              camera={camera}
              pendingId={operation.variables?.id}
              pending={operation.isPending}
              onAction={(action) => operation.mutate({ id: camera.camera_id, action })}
            />
          ))}
        </div>
      </Panel>
    </div>
  );
}

function CameraRuntimeCard({
  camera,
  pending,
  pendingId,
  onAction
}: {
  camera: OrchestrationCameraStatus;
  pending: boolean;
  pendingId?: string;
  onAction: (action: "start" | "stop" | "restart") => void;
}) {
  const disabled = pending && pendingId === camera.camera_id;
  return (
    <article className="runtime-card">
      <div className="runtime-title">
        <strong>{camera.camera_id}</strong>
        <div className="button-row">
          <button type="button" onClick={() => onAction("start")} disabled={disabled || camera.lifecycle_state === "starting"}>
            <Play aria-hidden="true" />
            Start
          </button>
          <button type="button" onClick={() => onAction("stop")} disabled={disabled || camera.lifecycle_state === "stopping"}>
            <Pause aria-hidden="true" />
            Stop
          </button>
          <button type="button" onClick={() => onAction("restart")} disabled={disabled}>
            <RotateCcw aria-hidden="true" />
            Restart
          </button>
        </div>
      </div>
      <div className="status-row compact">
        <StatusPill value={camera.lifecycle_state} />
        <StatusPill value={camera.health} />
        <StatusPill value={camera.is_running ? "running" : "not running"} />
      </div>
      <div className="metric-grid small">
        <StateBlock title="FPS" detail={camera.processing_fps.toFixed(1)} />
        <StateBlock title="Frames" detail={String(camera.frames_captured)} />
        <StateBlock title="Events" detail={String(camera.events_emitted)} />
        <StateBlock title="Dropped" detail={String(camera.dropped_event_count)} />
        <StateBlock title="Backlog" detail={String(camera.callback_backlog)} />
        <StateBlock title="Reconnects" detail={String(camera.reconnect_count)} />
      </div>
      <div className="muted-lines">
        <span>Last frame: {formatDate(camera.last_frame_at)}</span>
        <span>Last event: {formatDate(camera.last_event_at)}</span>
        <span>Updated: {formatDate(camera.updated_at)}</span>
        {camera.error_summary ? <span>Error: {camera.error_summary}</span> : null}
      </div>
    </article>
  );
}
