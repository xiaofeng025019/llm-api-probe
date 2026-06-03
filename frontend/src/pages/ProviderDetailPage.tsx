import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, ModelOut, ProbeResult, Provider } from "../api/types";
import { formatTime, modelTypeColor, statusColor } from "../lib/format";
import { useSse } from "../hooks/useSse";
import { ResultsChart } from "../components/ResultsChart";

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
    return <div className="muted">{error ?? "加载中…"}</div>;
  }

  const lastResult = results[0];
  return (
    <div>
      <div className="toolbar">
        <button className="secondary" onClick={() => nav("/providers")}>
          ← 返回
        </button>
        <strong style={{ fontSize: 18 }}>{provider.name}</strong>
        <span className="badge" style={{ background: "var(--muted)" }}>
          {provider.kind}
        </span>
        <span style={{ flex: 1 }} />
        <button
          className="secondary"
          onClick={async () => {
            await api.syncModels(providerId);
            await refresh();
          }}
        >
          同步模型
        </button>
        <button onClick={() => api.runNow(providerId).then(refresh)}>立即检测</button>
      </div>

      <div className="cards">
        <Stat
          label="Last check"
          value={lastResult ? formatTime(lastResult.checked_at) : "—"}
        />
        <Stat
          label="Last status"
          value={lastResult ? (lastResult.success ? "ok" : "fail") : "—"}
          color={statusColor(lastResult ? (lastResult.success ? "ok" : "fail") : null)}
        />
        <Stat
          label="Last latency"
          value={lastResult?.latency_ms != null ? `${lastResult.latency_ms}ms` : "—"}
        />
        <Stat
          label="Last TTFB"
          value={lastResult?.ttfb_ms != null ? `${lastResult.ttfb_ms}ms` : "—"}
        />
        <Stat label="Models" value={models.length} />
      </div>

      <div className="card" style={{ marginBottom: 24 }}>
        <div className="toolbar">
          <strong>趋势</strong>
          {WINDOWS.map((w) => (
            <button
              key={w.label}
              className={w.label === window_.label ? "" : "secondary"}
              onClick={() => setWindow(w)}
            >
              {w.label}
            </button>
          ))}
        </div>
        <ResultsChart results={results} />
      </div>

      <div className="card">
        <h2 style={{ marginTop: 0 }}>Models</h2>
        <table>
          <thead>
            <tr>
              <th>★</th>
              <th>Model</th>
              <th>Type</th>
              <th>Enabled</th>
              <th>Last seen</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {models.map((m) => (
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
                <td className="muted">{formatTime(m.last_seen_at)}</td>
                <td>
                  <button
                    className="secondary"
                    onClick={() => api.probeNow(providerId, m.id).then(refresh)}
                  >
                    Probe
                  </button>
                </td>
              </tr>
            ))}
            {models.length === 0 && (
              <tr>
                <td colSpan={6} className="muted">
                  没有模型。点 “同步模型” 拉取。
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: string | number; color?: string }) {
  return (
    <div className="card">
      <div className="label">{label}</div>
      <div className="value" style={{ color: color ?? "var(--text)" }}>
        {value}
      </div>
    </div>
  );
}
