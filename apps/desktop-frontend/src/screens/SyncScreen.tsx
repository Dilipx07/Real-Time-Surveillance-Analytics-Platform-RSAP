import { RefreshCw, Trash2 } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthContext";
import { ErrorBanner, formatDate, Panel, StateBlock, StatusPill } from "../components/ui";

export function SyncScreen() {
  const { api } = useAuth();
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: ["sync-status"], queryFn: () => api.syncStatus(), refetchInterval: 10000 });
  const deadLetters = useQuery({ queryKey: ["dead-letters"], queryFn: () => api.deadLetters(), refetchInterval: 10000 });
  const retry = useMutation({
    mutationFn: (id: string) => api.retryDeadLetter(id),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["dead-letters"] })
  });
  const discard = useMutation({
    mutationFn: (id: string) => api.discardDeadLetter(id),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["dead-letters"] })
  });

  return (
    <div className="stack">
      <Panel title="Sync Status">
        {status.isError ? <ErrorBanner error={status.error} /> : null}
        <div className="metric-grid">
          <StateBlock title="Connectivity" detail={status.data?.connected ? "Connected" : "Offline"} />
          <StateBlock title="Queue count" detail={String(status.data?.queue_count ?? 0)} />
          <StateBlock title="Dead letters" detail={String(status.data?.dead_letter_count ?? 0)} />
          <StateBlock title="Last checked" detail={formatDate(status.data?.last_checked_at)} />
        </div>
        {status.data?.last_error ? <div className="error-banner">{status.data.last_error}</div> : null}
      </Panel>
      <Panel title="Dead Letters">
        {deadLetters.isError ? <ErrorBanner error={deadLetters.error} /> : null}
        {retry.isError ? <ErrorBanner error={retry.error} /> : null}
        {discard.isError ? <ErrorBanner error={discard.error} /> : null}
        {deadLetters.data?.items.length === 0 ? <StateBlock title="No dead letters" detail="The local sync queue has no permanent failures." /> : null}
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Logical key</th>
                <th>Endpoint</th>
                <th>Error</th>
                <th>Attempts</th>
                <th>Failed</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {deadLetters.data?.items.map((item) => (
                <tr key={item.id}>
                  <td>{item.logical_key}</td>
                  <td>{item.endpoint}</td>
                  <td>
                    <StatusPill value={item.last_error_code || item.failure_class} />
                    <span className="table-note">{item.last_error_message}</span>
                  </td>
                  <td>
                    {item.attempt_count}/{item.max_attempts}
                  </td>
                  <td>{formatDate(item.failed_at)}</td>
                  <td>
                    <div className="button-row">
                      <button type="button" onClick={() => retry.mutate(item.id)} disabled={retry.isPending}>
                        <RefreshCw aria-hidden="true" />
                        Retry
                      </button>
                      <button type="button" className="danger-button" onClick={() => discard.mutate(item.id)} disabled={discard.isPending}>
                        <Trash2 aria-hidden="true" />
                        Discard
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
