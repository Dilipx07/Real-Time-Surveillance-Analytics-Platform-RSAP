import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthContext";
import { ErrorBanner, formatDate, Panel, payloadSummary, StateBlock, StatusPill } from "../components/ui";

export function AnalyticsScreen() {
  const { api } = useAuth();
  const events = useQuery({ queryKey: ["analytics-events"], queryFn: () => api.listAnalyticsEvents(), refetchInterval: 10000 });
  const persons = useQuery({ queryKey: ["persons"], queryFn: () => api.listPersons() });

  return (
    <div className="two-column">
      <Panel title="Recent Analytics Events">
        {events.isError ? <ErrorBanner error={events.error} /> : null}
        {events.data?.items.length === 0 ? <StateBlock title="No analytics events" detail="Events will appear after camera analytics writes local records." /> : null}
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Type</th>
                <th>Camera</th>
                <th>Payload</th>
                <th>Sync</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {events.data?.items.map((event) => (
                <tr key={event.id}>
                  <td>{event.event_type}</td>
                  <td>{event.camera_id}</td>
                  <td>{payloadSummary(event.payload)}</td>
                  <td>
                    <StatusPill value={event.synced ? "synced" : "queued"} />
                  </td>
                  <td>{formatDate(event.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
      <Panel title="Person Cache">
        {persons.isError ? <ErrorBanner error={persons.error} /> : null}
        {persons.data?.items.length === 0 ? <StateBlock title="No cached people" detail="Central person cache has not been synchronized locally." /> : null}
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Phone</th>
                <th>Synced</th>
              </tr>
            </thead>
            <tbody>
              {persons.data?.items.map((person) => (
                <tr key={person.id}>
                  <td>{person.name}</td>
                  <td>{person.phone}</td>
                  <td>{formatDate(person.synced_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
