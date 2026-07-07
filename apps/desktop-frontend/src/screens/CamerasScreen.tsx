import { FormEvent, useState } from "react";
import { Plus, Save, Trash2 } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthContext";
import type { CameraCreateRequest, CameraDto } from "../api/types";
import { ErrorBanner, formatDate, formatError, Panel, StateBlock, StatusPill } from "../components/ui";

const emptyForm: CameraCreateRequest = {
  name: "",
  stream_url: "",
  stream_type: "rtsp",
  location_label: "",
  analytics_config: {},
  zones: []
};

export function CamerasScreen() {
  const { api } = useAuth();
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<CameraDto | null>(null);
  const [form, setForm] = useState<CameraCreateRequest>(emptyForm);
  const [formError, setFormError] = useState<string | null>(null);
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: () => api.listCameras() });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["cameras"] });
  const createCamera = useMutation({
    mutationFn: (payload: CameraCreateRequest) => api.createCamera(payload),
    onSuccess: (camera) => {
      setForm(emptyForm);
      setSelected(camera);
      void refresh();
    },
    onError: (error) => setFormError(formatError(error))
  });
  const updateCamera = useMutation({
    mutationFn: (camera: CameraDto) =>
      api.updateCamera(camera.id, {
        name: camera.name,
        stream_type: camera.stream_type,
        location_label: camera.location_label,
        is_active: camera.is_active
      }),
    onSuccess: (camera) => {
      setSelected(camera);
      void refresh();
    },
    onError: (error) => setFormError(formatError(error))
  });
  const deleteCamera = useMutation({
    mutationFn: (id: string) => api.deleteCamera(id),
    onSuccess: () => {
      setSelected(null);
      void refresh();
    },
    onError: (error) => setFormError(formatError(error))
  });

  function submitCreate(event: FormEvent) {
    event.preventDefault();
    setFormError(null);
    createCamera.mutate({
      ...form,
      location_label: form.location_label || null,
      analytics_config: {},
      zones: []
    });
  }

  return (
    <div className="two-column">
      <Panel title="Camera List">
        {cameras.isError ? <ErrorBanner error={cameras.error} /> : null}
        {cameras.isLoading ? <StateBlock title="Loading cameras" /> : null}
        {cameras.data?.items.length === 0 ? <StateBlock title="No cameras configured" detail="Add a local camera source to begin." /> : null}
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Type</th>
                <th>State</th>
                <th>Sync</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {cameras.data?.items.map((camera) => (
                <tr
                  key={camera.id}
                  className={selected?.id === camera.id ? "selected-row" : ""}
                  onClick={() => setSelected(camera)}
                >
                  <td>{camera.name}</td>
                  <td>{camera.stream_type}</td>
                  <td>
                    <StatusPill value={camera.is_active ? "active" : "disabled"} />
                  </td>
                  <td>{camera.sync_state}</td>
                  <td>{formatDate(camera.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
      <div className="stack">
        <Panel
          title="Add Camera"
          action={
            <button type="submit" form="camera-create" disabled={createCamera.isPending}>
              <Plus aria-hidden="true" />
              Add
            </button>
          }
        >
          {formError ? (
            <div role="alert" className="error-banner">
              {formError}
            </div>
          ) : null}
          <form id="camera-create" className="form-grid" onSubmit={submitCreate}>
            <label>
              Name
              <input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} required />
            </label>
            <label>
              Stream type
              <select
                value={form.stream_type}
                onChange={(event) => setForm({ ...form, stream_type: event.target.value as CameraCreateRequest["stream_type"] })}
              >
                <option value="rtsp">RTSP</option>
                <option value="webcam">Webcam</option>
                <option value="nvr">NVR</option>
              </select>
            </label>
            <label>
              Source URL or device
              <input
                value={form.stream_url}
                onChange={(event) => setForm({ ...form, stream_url: event.target.value })}
                required
              />
            </label>
            <label>
              Location
              <input
                value={form.location_label ?? ""}
                onChange={(event) => setForm({ ...form, location_label: event.target.value })}
              />
            </label>
          </form>
        </Panel>
        <Panel
          title="Camera Detail"
          action={
            selected ? (
              <div className="button-row">
                <button type="button" onClick={() => updateCamera.mutate(selected)} disabled={updateCamera.isPending}>
                  <Save aria-hidden="true" />
                  Save
                </button>
                <button
                  type="button"
                  className="danger-button"
                  onClick={() => deleteCamera.mutate(selected.id)}
                  disabled={deleteCamera.isPending}
                >
                  <Trash2 aria-hidden="true" />
                  Delete
                </button>
              </div>
            ) : null
          }
        >
          {!selected ? (
            <StateBlock title="Select a camera" detail="Camera credentials stay hidden unless the backend explicitly returns a value." />
          ) : (
            <div className="form-grid">
              <label>
                Name
                <input value={selected.name} onChange={(event) => setSelected({ ...selected, name: event.target.value })} />
              </label>
              <label>
                Location
                <input
                  value={selected.location_label ?? ""}
                  onChange={(event) => setSelected({ ...selected, location_label: event.target.value || null })}
                />
              </label>
              <label>
                Active
                <select
                  value={String(selected.is_active)}
                  onChange={(event) => setSelected({ ...selected, is_active: event.target.value === "true" })}
                >
                  <option value="true">Active</option>
                  <option value="false">Disabled</option>
                </select>
              </label>
              <StateBlock title="Local ID" detail={selected.id} />
              <StateBlock title="Server ID" detail={selected.server_id ?? "Not synced"} />
              <StateBlock title="Zones" detail={`${selected.zones.length} configured`} />
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}
