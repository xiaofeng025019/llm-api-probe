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
import {
  IconBack,
  IconCheck,
  IconAlert,
  IconClock,
  IconActivity,
  IconChart,
  IconRefresh,
  IconPlay,
  IconProbe,
  IconStarOutline,
  IconModels,
} from "../components/Icons";

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

  // keyboard: arrow keys on window tabs
  function onWindowKey(e: React.KeyboardEvent, idx: number) {
    if (e.key === "ArrowRight" || e.key === "ArrowDown") {
      e.preventDefault();
      const next = WINDOWS[(idx + 1) % WINDOWS.length];
      setWindow(next);
      document.getElementById(`window-tab-${next.label}`)?.focus();
    } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
      e.preventDefault();
      const prev = WINDOWS[(idx - 1 + WINDOWS.length) % WINDOWS.length];
      setWindow(prev);
      document.getElementById(`window-tab-${prev.label}`)?.focus();
    }
  }

  if (!provider) {
    return (
      <div className="empty-state">
        <div className="spinner" style={{ width: 24, height: 24 }} />
        {error && (
          <div className="error" role="alert" style={{ marginTop: 12 }}>
            {error}
          </div>
        )}
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
      <div className="hero fade-up">
        <div className="hero-content">
          <div>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 12,
                marginBottom: 6,
              }}
            >
              <span
                className="kind-badge"
                style={{ background: "rgba(255,255,255,0.2)", color: "white" }}
              >
                {provider.kind}
              </span>
              {!provider.enabled && (
                <span
                  className="pill"
                  style={{ background: "rgba(255,255,255,0.2)", color: "white" }}
                >
                  disabled
                </span>
              )}
            </div>
            <h1>{provider.name}</h1>
            <div className="meta">
              <span>
                <IconClock /> 每 {provider.interval_seconds}s 检测
              </span>
              <span>·</span>
              <span>
                <IconProbe /> 超时 {provider.timeout_seconds}s
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
              <IconRefresh />
              同步模型
            </button>
            <button onClick={() => api.runNow(providerId).then(refresh)}>
              <IconPlay />
              立即检测
            </button>
          </div>
        </div>
      </div>

      <div className="stat-grid stagger">
        <Stat
          label="最近状态"
          value={lastResult ? (lastResult.success ? "OK" : "Failed") : "—"}
          accentColor={statusColor(
            lastResult ? (lastResult.success ? "ok" : "fail") : null,
          )}
          icon={lastResult?.success ? <IconCheck /> : <IconAlert />}
        />
        <Stat
          label="最近延迟"
          value={formatMs(lastResult?.latency_ms ?? null)}
          icon={<IconClock />}
        />
        <Stat
          label="最近 TTFB"
          value={formatMs(lastResult?.ttfb_ms ?? null)}
          icon={<IconActivity />}
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
          icon={<IconChart />}
        />
        <Stat
          label={`${window_.label} 平均延迟`}
          value={formatMs(avgLatency)}
          icon={<IconClock />}
        />
      </div>

      <div className="card section fade-up">
        <div className="section-header">
          <div className="section-title">
            <IconChart />
            趋势
          </div>
          <div
            className="window-tabs"
            role="tablist"
            aria-label="时间窗"
          >
            {WINDOWS.map((w, idx) => (
              <button
                key={w.label}
                id={`window-tab-${w.label}`}
                role="tab"
                aria-selected={w.label === window_.label}
                tabIndex={w.label === window_.label ? 0 : -1}
                className={w.label === window_.label ? "active" : ""}
                onClick={() => setWindow(w)}
                onKeyDown={(e) => onWindowKey(e, idx)}
              >
                {w.label}
              </button>
            ))}
          </div>
        </div>
        <div role="tabpanel" aria-label={`${window_.label} 趋势`}>
          <ResultsChart results={results} />
        </div>
      </div>

      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <IconModels />
            Models
          </div>
          <span className="muted">{models.length} 个</span>
        </div>
        <div className="table-wrap fade-up">
          <table>
            <caption className="sr-only">Models for {provider.name}</caption>
            <thead>
              <tr>
                <th style={{ width: 50 }} scope="col">
                  <span className="sr-only">Favorite</span>
                </th>
                <th scope="col">Model</th>
                <th scope="col">Type</th>
                <th scope="col">Enabled</th>
                <th scope="col">Last seen</th>
                <th style={{ width: 100 }} scope="col">
                  <span className="sr-only">Actions</span>
                </th>
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
                      aria-label={m.is_favorite ? "取消收藏" : "收藏"}
                      aria-pressed={m.is_favorite}
                    >
                      <IconStarOutline filled={m.is_favorite} />
                    </button>
                  </td>
                  <td>
                    <strong>{m.model_id}</strong>
                  </td>
                  <td>
                    <span
                      className="type-icon"
                      style={{ background: modelTypeColor(m.type) }}
                    >
                      {m.type}
                    </span>
                  </td>
                  <td>
                    <label
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 6,
                        cursor: "pointer",
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={m.enabled}
                        onChange={() =>
                          api
                            .patchModel(m.id, { enabled: !m.enabled })
                            .then(refresh)
                        }
                        aria-label={`Enable ${m.model_id}`}
                      />
                      <span style={{ fontSize: 12 }}>
                        {m.enabled ? "on" : "off"}
                      </span>
                    </label>
                  </td>
                  <td className="muted">
                    <span title={new Date(m.last_seen_at).toLocaleString()}>
                      {formatRelative(m.last_seen_at)}
                    </span>
                  </td>
                  <td>
                    <button
                      className="secondary sm"
                      onClick={() => api.probeNow(providerId, m.id).then(refresh)}
                      aria-label={`单独探测 ${m.model_id}`}
                    >
                      <IconProbe />
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

      <button
        className="ghost"
        onClick={() => nav("/providers")}
        style={{ marginTop: 16 }}
      >
        <IconBack />
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
      <div className="stat-icon" aria-hidden="true">
        {icon}
      </div>
    </div>
  );
}
