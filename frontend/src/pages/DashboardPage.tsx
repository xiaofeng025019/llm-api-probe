import { useNavigate } from "react-router-dom";
import { useDashboard } from "../hooks/useDashboard";
import { formatTime, statusColor } from "../lib/format";
import { api, type Provider } from "../api/types";

export function DashboardPage() {
  const nav = useNavigate();
  const { dashboard, providers, modelsByProvider, loading, error, refresh } = useDashboard();

  async function onDelete(p: Provider) {
    if (!confirm(`删除 provider ${p.name}?`)) return;
    try {
      await api.deleteProvider(p.id);
      await refresh();
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div>
      {error && <div className="error">{error}</div>}
      {loading && <div className="muted">加载中…</div>}

      {dashboard && (
        <div className="cards">
          <Stat label="Providers" value={dashboard.totals.providers} />
          <Stat label="Models" value={dashboard.totals.models} />
          <Stat label="OK" value={dashboard.totals.ok} color="var(--ok)" />
          <Stat label="Fail" value={dashboard.totals.failing} color="var(--fail)" />
          <Stat
            label="Favorites online"
            value={`${dashboard.totals.favorites_online}/${dashboard.totals.favorites_total}`}
            color="var(--primary)"
          />
        </div>
      )}

      {dashboard && dashboard.providers.length > 0 && (
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Providers</h2>
          <table>
            <thead>
              <tr>
                <th></th>
                <th>Name</th>
                <th>Kind</th>
                <th>Models</th>
                <th>Last check</th>
                <th>24h avail</th>
                <th>Avg lat (24h)</th>
                <th>Fav online</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {dashboard.providers.map((p) => (
                <tr key={p.provider_id}>
                  <td>
                    <span
                      className="dot"
                      style={{ background: statusColor(p.last_status) }}
                      title={p.last_status ?? "unknown"}
                    />
                  </td>
                  <td>
                    <a
                      href={`/providers/${p.provider_id}`}
                      onClick={(e) => {
                        e.preventDefault();
                        nav(`/providers/${p.provider_id}`);
                      }}
                    >
                      {p.name}
                    </a>
                    {!p.enabled && <span className="muted"> (disabled)</span>}
                  </td>
                  <td>
                    <span className="badge" style={{ background: "var(--muted)" }}>
                      {p.kind}
                    </span>
                  </td>
                  <td>{p.model_count}</td>
                  <td>{formatTime(p.last_checked_at)}</td>
                  <td>{p.availability_24h != null ? `${p.availability_24h}%` : "—"}</td>
                  <td>{p.avg_latency_ms_24h != null ? `${p.avg_latency_ms_24h}ms` : "—"}</td>
                  <td>
                    {p.favorite_models_total > 0
                      ? `${p.favorite_models_online}/${p.favorite_models_total}`
                      : "—"}
                  </td>
                  <td>
                    <div className="row-actions">
                      <button
                        className="secondary"
                        onClick={() => api.runNow(p.provider_id).then(refresh)}
                      >
                        Run
                      </button>
                      <button
                        className="secondary"
                        onClick={() => api.syncModels(p.provider_id).then(refresh)}
                      >
                        Sync
                      </button>
                      <button
                        className="danger"
                        onClick={() =>
                          onDelete(providers.find((x) => x.id === p.provider_id)!)
                        }
                      >
                        Del
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {dashboard && dashboard.providers.length === 0 && !loading && (
        <div className="card">
          <p>还没有 provider。在 <a href="/providers">Providers</a> 页添加一个。</p>
        </div>
      )}

      {Object.keys(modelsByProvider).length > 0 && (
        <div style={{ marginTop: 24 }}>
          <h2>Models</h2>
          {Object.entries(modelsByProvider).map(([pid, ms]) => {
            const p = providers.find((x) => x.id === Number(pid));
            if (!ms.length) return null;
            return (
              <div className="card" key={pid} style={{ marginBottom: 12 }}>
                <strong>{p?.name ?? pid}</strong>
                <table>
                  <thead>
                    <tr>
                      <th>★</th>
                      <th>Model</th>
                      <th>Type</th>
                      <th>Enabled</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ms.map((m) => (
                      <tr key={m.id}>
                        <td>
                          <input
                            type="checkbox"
                            checked={m.is_favorite}
                            onChange={() =>
                              api
                                .patchModel(m.id, { is_favorite: !m.is_favorite })
                                .then(refresh)
                            }
                          />
                        </td>
                        <td>{m.model_id}</td>
                        <td>
                          <span
                            className="badge"
                            style={{ background: modelTypeBg(m.type) }}
                          >
                            {m.type}
                          </span>
                        </td>
                        <td>{m.enabled ? "yes" : "no"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: number | string; color?: string }) {
  return (
    <div className="card">
      <div className="label">{label}</div>
      <div className="value" style={{ color: color ?? "var(--text)" }}>
        {value}
      </div>
    </div>
  );
}

function modelTypeBg(type: string): string {
  const map: Record<string, string> = {
    chat: "#2563eb",
    vision: "#7c3aed",
    audio: "#db2777",
    image: "#ea580c",
    embedding: "#0891b2",
    code: "#65a30d",
  };
  return map[type] ?? "#6b7280";
}
