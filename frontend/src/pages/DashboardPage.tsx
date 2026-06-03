import { useNavigate } from "react-router-dom";
import { useDashboard } from "../hooks/useDashboard";
import { formatPercent, formatRelative, modelTypeBg, statusColor } from "../lib/format";
import { api, type Provider } from "../api/types";
import { Icon } from "../components/Icons";

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

  if (loading && !dashboard) {
    return (
      <div className="empty-state">
        <div className="spinner" style={{ width: 24, height: 24 }} />
      </div>
    );
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>总览</h1>
          <div className="subtitle">所有 LLM 服务商的实时可用性</div>
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      {dashboard && (
        <div className="stat-grid">
          <StatCard
            label="Providers"
            value={dashboard.totals.providers}
            icon={<Icon.Server />}
            accentColor="var(--primary)"
          />
          <StatCard
            label="Models"
            value={dashboard.totals.models}
            icon={<Icon.Models />}
            accentColor="var(--info)"
          />
          <StatCard
            label="OK"
            value={dashboard.totals.ok}
            icon={<Icon.Check />}
            accentColor="var(--ok)"
          />
          <StatCard
            label="Failing"
            value={dashboard.totals.failing}
            icon={<Icon.Alert />}
            accentColor="var(--fail)"
          />
          <StatCard
            label="Favorites online"
            value={`${dashboard.totals.favorites_online} / ${dashboard.totals.favorites_total}`}
            icon={<Icon.Star filled />}
            accentColor="var(--warn)"
            hint={
              dashboard.totals.favorites_total === 0
                ? "未收藏任何模型"
                : `${((dashboard.totals.favorites_online / Math.max(1, dashboard.totals.favorites_total)) * 100).toFixed(0)}% 在线`
            }
          />
        </div>
      )}

      {dashboard && dashboard.providers.length > 0 && (
        <div className="section">
          <div className="section-header">
            <div className="section-title">
              <Icon.Server />
              Providers
            </div>
            <span className="muted">{dashboard.providers.length} 个</span>
          </div>
          <div className="provider-grid">
            {dashboard.providers.map((p) => {
              const provider = providers.find((x) => x.id === p.provider_id);
              return (
                <div
                  key={p.provider_id}
                  className="provider-card"
                  style={{ ["--status-color" as string]: statusColor(p.last_status) }}
                  onClick={() => nav(`/providers/${p.provider_id}`)}
                >
                  <div className="head">
                    <div>
                      <div className="name">
                        <span
                          className={`status-dot ${p.last_status ?? "unknown"}`}
                          title={p.last_status ?? "unknown"}
                        />
                        {p.name}
                        {!p.enabled && (
                          <span className="pill" style={{ marginLeft: 6 }}>
                            disabled
                          </span>
                        )}
                      </div>
                      <div className="meta">
                        <span className="kind-badge">{p.kind}</span>
                        <span style={{ marginLeft: 8 }}>
                          {p.model_count} models · {formatRelative(p.last_checked_at)}
                        </span>
                      </div>
                    </div>
                  </div>

                  <div className="metrics">
                    <div className="metric">
                      <div className="label">24h 可用率</div>
                      <div
                        className="value"
                        style={{
                          color:
                            p.availability_24h == null
                              ? "var(--muted)"
                              : p.availability_24h >= 99
                                ? "var(--ok)"
                                : p.availability_24h >= 90
                                  ? "var(--warn)"
                                  : "var(--fail)",
                        }}
                      >
                        {formatPercent(p.availability_24h)}
                      </div>
                    </div>
                    <div className="metric">
                      <div className="label">平均延迟</div>
                      <div className="value">
                        {p.avg_latency_ms_24h != null
                          ? `${p.avg_latency_ms_24h}ms`
                          : "—"}
                      </div>
                    </div>
                    <div className="metric">
                      <div className="label">收藏在线</div>
                      <div className="value">
                        {p.favorite_models_total > 0
                          ? `${p.favorite_models_online}/${p.favorite_models_total}`
                          : "—"}
                      </div>
                    </div>
                    <div className="metric">
                      <div className="label">检测间隔</div>
                      <div className="value">
                        {provider ? `${provider.interval_seconds}s` : "—"}
                      </div>
                    </div>
                  </div>

                  <div
                    className="actions"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <button
                      className="secondary sm"
                      onClick={() => api.runNow(p.provider_id).then(refresh)}
                    >
                      <Icon.Run />
                      Run
                    </button>
                    <button
                      className="secondary sm"
                      onClick={() => api.syncModels(p.provider_id).then(refresh)}
                    >
                      <Icon.Sync />
                      Sync
                    </button>
                    {provider && (
                      <button
                        className="ghost sm"
                        onClick={() => onDelete(provider)}
                        title="删除"
                      >
                        <Icon.Delete />
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {dashboard && dashboard.providers.length === 0 && !loading && (
        <div className="card">
          <div className="empty-state">
            <div className="empty-state-icon">
              <Icon.Server />
            </div>
            <h3>还没有 provider</h3>
            <p>在 Providers 页面添加一个 LLM 服务商开始监测。</p>
            <button onClick={() => nav("/providers")} style={{ marginTop: 12 }}>
              <Icon.Plus />
              添加 Provider
            </button>
          </div>
        </div>
      )}

      {Object.keys(modelsByProvider).length > 0 && (
        <div className="section">
          <div className="section-header">
            <div className="section-title">
              <Icon.Models />
              Models
            </div>
            <span className="muted">
              {Object.values(modelsByProvider).reduce((n, ms) => n + ms.length, 0)} 个
            </span>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th style={{ width: 50 }}>★</th>
                  <th>Model</th>
                  <th>Provider</th>
                  <th>Type</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(modelsByProvider).flatMap(([pid, ms]) => {
                  const p = providers.find((x) => x.id === Number(pid));
                  return ms.map((m) => (
                    <tr key={m.id}>
                      <td>
                        <button
                          className={`favorite-star ${m.is_favorite ? "active" : ""}`}
                          onClick={() =>
                            api
                              .patchModel(m.id, { is_favorite: !m.is_favorite })
                              .then(refresh)
                          }
                          title={m.is_favorite ? "取消收藏" : "收藏"}
                        >
                          <Icon.Star filled={m.is_favorite} />
                        </button>
                      </td>
                      <td>
                        <strong>{m.model_id}</strong>
                      </td>
                      <td>
                        <a
                          href={`/providers/${pid}`}
                          onClick={(e) => {
                            e.preventDefault();
                            nav(`/providers/${pid}`);
                          }}
                        >
                          {p?.name ?? pid}
                        </a>
                      </td>
                      <td>
                        <span className="type-badge" style={{ background: modelTypeBg(m.type) }}>
                          {m.type}
                        </span>
                      </td>
                      <td>
                        {m.enabled ? (
                          <span className="pill ok">
                            <Icon.Check /> enabled
                          </span>
                        ) : (
                          <span className="pill">disabled</span>
                        )}
                      </td>
                    </tr>
                  ));
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function StatCard({
  label,
  value,
  icon,
  accentColor,
  hint,
}: {
  label: string;
  value: string | number;
  icon: React.ReactNode;
  accentColor?: string;
  hint?: string;
}) {
  return (
    <div
      className="stat"
      style={
        {
          ["--accent-color" as string]: accentColor,
          ["--accent-bg" as string]: accentColor
            ? `color-mix(in srgb, ${accentColor} 12%, transparent)`
            : undefined,
        } as React.CSSProperties
      }
    >
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {hint && <div className="hint">{hint}</div>}
      <div className="stat-icon">{icon}</div>
    </div>
  );
}
