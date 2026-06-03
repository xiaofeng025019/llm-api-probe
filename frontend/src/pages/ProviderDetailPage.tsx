import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, ModelOut, ProbeResult, Provider } from "../api/types";
import {
  formatMs,
  formatRelative,
  modelTypeColor,
  statusColor,
} from "../lib/format";
import { useSse } from "../hooks/useSse";
import { ResultsChart } from "../components/ResultsChart";
import { Icon } from "../components/Icons";

const WINDOWS: Array<{ label: string; hours: number }> = [
  { label: "1h", hours: 1 },
  { label: "24h", hours: 24 },
  { label: "7d", hours: 24 * 7 },
  { label: "30d", hours: 24 * 30 },
];

export function ProviderDetailPage() {
  const { id } = useParams<{ id: string }>();
  const providerId = Number(id);
  const nav = useNavigate();

  const [provider, setProvider] = useState<Provider | null>(null);
  const [models, setModels] = useState<ModelOut[]>([]);
  const [results, setResults] = useState<ProbeResult[]>([]);
  const [window_, setWindow] = useState(WINDOWS[1]);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    if (!Number.isFinite(providerId)) return;
    try {
      const [p, ms, rs] = await Promise.all([
        api.getProvider(providerId),
        api.models(providerId),
        api.results({ provider_id: providerId, hours: window_.hours, limit: 1000 }),
      ]);
      setProvider(p);
      setModels(ms);
      setResults(rs);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [providerId, window_.hours]);

  useSse((event) => {
    if (event === "probe.completed" || event === "model.updated") {
      void refresh();
    }
  });

  if (!provider) {
    return (
      <div className="empty-state">
        <div className="spinner" style={{ width: 24, height: 24 }} />
        {error && <div className="error" style={{ marginTop: 12 }}>{error}</div>}
      </div>
    );
  }

  const lastResult = results[0];
  const successes = results.filter((r) => r.success).length;
  const availability =
    results.length > 0 ? (successes / results.length) * 100 : null;
  const avgLatency =
    results.length > 0
      ? results
          .filter((r) => r.success && r.latency_ms != null)
          .reduce((a, r) => a + (r.latency_ms ?? 0), 0) /
        Math.max(1, results.filter((r) => r.success).length)
      : null;

  return (
    <div>
      <div className="hero">
        <div className="hero-content">
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 6 }}>
              <span className="kind-badge" style={{ background: "rgba(255,255,255,0.2)", color: "white" }}>
                {provider.kind}
              </span>
              {!provider.enabled && (
                <span className="pill" style={{ background: "rgba(255,255,255,0.2)", color: "white" }}>
                  disabled
                </span>
              )}
            </div>
            <h1>{provider.name}</h1>
            <div className="meta">
              <span>
                <Icon.Clock /> 每 {provider.interval_seconds}s 检测
              </span>
              <span>·</span>
              <span>
                <Icon.Probe /> 超时 {provider.timeout_seconds}s
              </span>
              {provider.proxy && (
                <>
                  <span>·</span>
                  <span>proxy: {provider.proxy}</span>
                </>
              )}
            </div>
          </div>
          <div className="hero-actions">
            <button
              className="secondary"
              onClick={async () => {
                await api.syncModels(providerId);
                await refresh();
              }}
            >
              <Icon.Sync />
              同步模型
            </button>
            <button onClick={() => api.runNow(providerId).then(refresh)}>
              <Icon.Run />
              立即检测
            </button>
          </div>
        </div>
      </div>

      <div className="stat-grid">
        <Stat
          label="最近状态"
          value={lastResult ? (lastResult.success ? "OK" : "Failed") : "—"}
          accentColor={statusColor(lastResult ? (lastResult.success ? "ok" : "fail") : null)}
          icon={lastResult?.success ? <Icon.Check /> : <Icon.Alert />}
        />
        <Stat
          label="最近延迟"
          value={formatMs(lastResult?.latency_ms ?? null)}
          icon={<Icon.Clock />}
        />
        <Stat
          label="最近 TTFB"
          value={formatMs(lastResult?.ttfb_ms ?? null)}
          icon={<Icon.Activity />}
        />
        <Stat
          label={`${window_.label} 可用率`}
          value={availability != null ? `${availability.toFixed(1)}%` : "—"}
          accentColor={
            availability == null
              ? undefined
              : availability >= 99
                ? "var(--ok)"
                : availability >= 90
                  ? "var(--warn)"
                  : "var(--fail)"
          }
          hint={`${successes}/${results.length} 次成功`}
          icon={<Icon.Chart />}
        />
        <Stat
          label={`${window_.label} 平均延迟`}
          value={formatMs(avgLatency)}
          icon={<Icon.Clock />}
        />
      </div>

      <div className="card section">
        <div className="section-header">
          <div className="section-title">
            <Icon.Chart />
            趋势
          </div>
          <div className="window-tabs">
            {WINDOWS.map((w) => (
              <button
                key={w.label}
                className={w.label === window_.label ? "active" : ""}
                onClick={() => setWindow(w)}
              >
                {w.label}
              </button>
            ))}
          </div>
        </div>
        <ResultsChart results={results} />
      </div>

      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Icon.Models />
            Models
          </div>
          <span className="muted">{models.length} 个</span>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th style={{ width: 50 }}>★</th>
                <th>Model</th>
                <th>Type</th>
                <th>Enabled</th>
                <th>Last seen</th>
                <th style={{ width: 100 }}></th>
              </tr>
            </thead>
            <tbody>
              {models.map((m) => (
                <tr key={m.id}>
                  <td>
                    <button
                      className={`favorite-star ${m.is_favorite ? "active" : ""}`}
                      onClick={() =>
                        api
                          .patchModel(m.id, { is_favorite: !m.is_favorite })
                          .then(refresh)
                      }
                    >
                      <Icon.Star filled={m.is_favorite} />
                    </button>
                  </td>
                  <td>
                    <strong>{m.model_id}</strong>
                  </td>
                  <td>
                    <span
                      className="type-badge"
                      style={{ background: modelTypeColor(m.type) }}
                    >
                      {m.type}
                    </span>
                  </td>
                  <td>
                    <input
                      type="checkbox"
                      checked={m.enabled}
                      onChange={() =>
                        api.patchModel(m.id, { enabled: !m.enabled }).then(refresh)
                      }
                    />
                  </td>
                  <td className="muted">{formatRelative(m.last_seen_at)}</td>
                  <td>
                    <button
                      className="secondary sm"
                      onClick={() => api.probeNow(providerId, m.id).then(refresh)}
                    >
                      <Icon.Probe />
                      Probe
                    </button>
                  </td>
                </tr>
              ))}
              {models.length === 0 && (
                <tr>
                  <td colSpan={6}>
                    <div className="empty-state">
                      <p>没有模型。点 “同步模型” 拉取。</p>
                    </div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <button className="ghost" onClick={() => nav("/providers")} style={{ marginTop: 16 }}>
        <Icon.Back />
        返回 Providers
      </button>
    </div>
  );
}

function Stat({
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
