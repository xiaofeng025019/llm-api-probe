import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, ModelOut, ProbeResult, Provider, Setting } from "../api/types";
import { withErrorToast } from "../lib/action";
import {
  formatMs,
  formatPercent,
  parseApiDate,
  formatRelative,
  formatTime,
  modelTypeColor,
  statusColor,
} from "../lib/format";
import { modelStatusIntervalLabel } from "../lib/settings";
import { useSse } from "../hooks/useSse";
import { ResultsChart } from "../components/ResultsChart";
import {
  IconBack,
  IconCheck,
  IconAlert,
  IconClock,
  IconActivity,
  IconChart,
  IconGauge,
  IconHash,
  IconRefresh,
  IconPlay,
  IconProbe,
  IconStarOutline,
  IconModels,
  IconGlobe,
  IconNetwork,
} from "../components/Icons";

const WINDOWS: Array<{ label: string; hours: number }> = [
  { label: "1h", hours: 1 },
  { label: "24h", hours: 24 },
  { label: "7d", hours: 24 * 7 },
  { label: "30d", hours: 24 * 30 },
];

function p95(values: Array<number | null | undefined>): number | null {
  const nums = values
    .filter((v): v is number => v != null && Number.isFinite(v))
    .sort((a, b) => a - b);
  if (nums.length === 0) return null;
  const index = Math.max(0, Math.min(nums.length - 1, Math.ceil(nums.length * 0.95) - 1));
  return nums[index];
}

function consecutiveFailures(rowsDesc: ProbeResult[]): number {
  let count = 0;
  for (const row of rowsDesc) {
    if (row.success) break;
    count += 1;
  }
  return count;
}

function errorCounts(rows: ProbeResult[]): Array<[string, number]> {
  const counts = new Map<string, number>();
  for (const row of rows) {
    if (row.success) continue;
    const key = row.error_code ?? (row.http_status ? `HTTP ${row.http_status}` : "unknown");
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
}

export function ProviderDetailPage() {
  const { id } = useParams<{ id: string }>();
  const providerId = Number(id);
  const nav = useNavigate();

  const [provider, setProvider] = useState<Provider | null>(null);
  const [models, setModels] = useState<ModelOut[]>([]);
  const [results, setResults] = useState<ProbeResult[]>([]);
  const [modelStatusResults, setModelStatusResults] = useState<ProbeResult[]>([]);
  const [settings, setSettings] = useState<Setting[]>([]);
  const [window_, setWindow] = useState(WINDOWS[1]);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    if (!Number.isFinite(providerId)) return;
    try {
      const [p, ms, rs, statusRs, ss] = await Promise.all([
        api.getProvider(providerId),
        api.models(providerId),
        api.results({ provider_id: providerId, hours: window_.hours, limit: 1000 }),
        api.results({ provider_id: providerId, hours: 24 * 30, limit: 1000 }),
        api.settings(),
      ]);
      setProvider(p);
      setModels(ms);
      setResults(rs);
      setModelStatusResults(statusRs);
      setSettings(ss);
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

  const latestResultByModel = useMemo(() => {
    const byModel = new Map<number, ProbeResult>();
    for (const r of modelStatusResults) {
      if (r.model_id == null || r.target !== "chat_completion") continue;
      if (!byModel.has(r.model_id)) byModel.set(r.model_id, r);
    }
    return byModel;
  }, [modelStatusResults]);

  const modelQualityById = useMemo(() => {
    const cutoff = Date.now() - 24 * 60 * 60 * 1000;
    const grouped = new Map<number, ProbeResult[]>();
    for (const r of modelStatusResults) {
      if (r.model_id == null || r.target !== "chat_completion") continue;
      const rows = grouped.get(r.model_id) ?? [];
      rows.push(r);
      grouped.set(r.model_id, rows);
    }
    return new Map(
      [...grouped.entries()].map(([modelId, rows]) => {
        const sorted = [...rows].sort(
          (a, b) => parseApiDate(b.checked_at).getTime() - parseApiDate(a.checked_at).getTime(),
        );
        const recent = sorted.filter((r) => parseApiDate(r.checked_at).getTime() >= cutoff);
        const successes = recent.filter((r) => r.success).length;
        return [
          modelId,
          {
            samples24h: recent.length,
            availability24h: recent.length > 0 ? (successes / recent.length) * 100 : null,
            p95Latency: p95(recent.map((r) => r.latency_ms)),
            p95Ttfb: p95(recent.map((r) => r.ttfb_ms)),
            consecutiveFailures: consecutiveFailures(sorted),
          },
        ];
      }),
    );
  }, [modelStatusResults]);

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

  const qualityResults = results.filter((r) => r.target === "chat_completion");
  const sortedModelListResults = modelStatusResults
    .filter((r) => r.target === "list_models")
    .sort((a, b) => parseApiDate(b.checked_at).getTime() - parseApiDate(a.checked_at).getTime());
  const latestModelListResult = sortedModelListResults[0];
  const listModelsStatus = latestModelListResult
    ? latestModelListResult.success
      ? "ok"
      : "fail"
    : null;
  const lastResult = qualityResults[0] ?? results[0];
  const successes = qualityResults.filter((r) => r.success).length;
  const availability =
    qualityResults.length > 0 ? (successes / qualityResults.length) * 100 : null;
  const avgLatency =
    qualityResults.length > 0
      ? qualityResults
          .filter((r) => r.success && r.latency_ms != null)
          .reduce((a, r) => a + (r.latency_ms ?? 0), 0) /
        Math.max(1, qualityResults.filter((r) => r.success).length)
      : null;
  const failures = qualityResults.length - successes;
  const p95Latency = p95(qualityResults.map((r) => r.latency_ms));
  const p95Ttfb = p95(qualityResults.map((r) => r.ttfb_ms));
  const enabledModels = models.filter((m) => m.enabled).length;
  const availableModels = models.filter((m) => m.enabled && latestResultByModel.get(m.id)?.success).length;
  const selectedWindowErrors = errorCounts(qualityResults);
  const statusIntervalLabel = modelStatusIntervalLabel(settings);

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
                <IconClock /> 模型清单每 {provider.interval_seconds}s 更新
              </span>
              <span>·</span>
              <span>
                状态检测：{statusIntervalLabel}
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
                try {
                  await withErrorToast(api.syncModels(providerId), "更新模型清单");
                  await refresh();
                } catch {
                  /* toast already shown */
                }
              }}
              title="从 provider 重新拉取可提供的模型列表"
            >
              <IconRefresh />
              更新模型清单
            </button>
            <button
              onClick={() => withErrorToast(api.runNow(providerId), "检测状态").then(refresh)}
              title="检测当前模型可用性、延迟和错误状态"
            >
              <IconPlay />
              检测状态
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
        <Stat
          label={`${window_.label} P95 延迟`}
          value={formatMs(p95Latency)}
          icon={<IconGauge />}
        />
        <Stat
          label={`${window_.label} P95 TTFB`}
          value={formatMs(p95Ttfb)}
          icon={<IconActivity />}
        />
        <Stat
          label={`${window_.label} 样本`}
          value={`${qualityResults.length} / ${failures}`}
          hint="总数 / 失败"
          icon={<IconHash />}
        />
      </div>

      <div className="section fade-up">
        <div className="section-header">
          <div className="section-title">
            <IconGauge />
            Provider Quality
          </div>
          <span className="muted">{window_.label} window</span>
        </div>
        <div className="provider-quality-grid">
          <div className="quality-tile quality-wide">
            <div className="quality-tile-head">
              <span>
                <IconGlobe />
                Endpoint
              </span>
              <span className={`pill ${provider.enabled ? "ok" : "warn"}`}>
                {provider.enabled ? "monitoring" : "paused"}
              </span>
            </div>
            <div className="endpoint-value" title={provider.base_url}>
              {provider.base_url}
            </div>
            <div className="quality-meta">
              <span>Timeout {provider.timeout_seconds}s</span>
              <span>Probe {statusIntervalLabel}</span>
              <span>Model list {provider.interval_seconds}s</span>
              {provider.proxy && <span>Proxy configured</span>}
            </div>
          </div>

          <div className="quality-tile">
            <div className="quality-tile-head">
              <span>
                <IconRefresh />
                Model List
              </span>
              <span className={`status-dot ${listModelsStatus ?? "unknown"}`} />
            </div>
            <div className="quality-value">
              {latestModelListResult ? (latestModelListResult.success ? "OK" : "Failed") : "—"}
            </div>
            <div className="quality-meta">
              <span>{formatMs(latestModelListResult?.latency_ms)}</span>
              <span title={formatTime(latestModelListResult?.checked_at)}>
                {formatRelative(latestModelListResult?.checked_at)}
              </span>
              {latestModelListResult?.error_code && <span>{latestModelListResult.error_code}</span>}
            </div>
          </div>

          <div className="quality-tile">
            <div className="quality-tile-head">
              <span>
                <IconModels />
                Model Coverage
              </span>
            </div>
            <div className="quality-value">
              {availableModels} / {enabledModels}
            </div>
            <div className="quality-meta">
              <span>Available Models</span>
              <span>Total {models.length}</span>
            </div>
          </div>

          <div className="quality-tile">
            <div className="quality-tile-head">
              <span>
                <IconNetwork />
                Communication
              </span>
            </div>
            <div className="quality-value">{formatMs(p95Latency)}</div>
            <div className="quality-meta">
              <span>P95 latency</span>
              <span>P95 TTFB {formatMs(p95Ttfb)}</span>
              <span>Avg {formatMs(avgLatency)}</span>
            </div>
          </div>

          <div className="quality-tile">
            <div className="quality-tile-head">
              <span>
                <IconAlert />
                Errors
              </span>
              <span className={`pill ${failures > 0 ? "fail" : "ok"}`}>{failures}</span>
            </div>
            {selectedWindowErrors.length > 0 ? (
              <div className="error-chip-list" aria-label={`${window_.label} error distribution`}>
                {selectedWindowErrors.slice(0, 4).map(([code, count]) => (
                  <span className="error-chip" key={code}>
                    {code} <strong>{count}</strong>
                  </span>
                ))}
              </div>
            ) : (
              <div className="quality-value">No errors</div>
            )}
            <div className="quality-meta">
              <span>{qualityResults.length} model probes</span>
            </div>
          </div>
        </div>
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
                <th scope="col">Status</th>
                <th scope="col">Type</th>
                <th scope="col">Enabled</th>
                <th scope="col">Last probe</th>
                <th scope="col">24h</th>
                <th scope="col">Samples</th>
                <th scope="col">Latency</th>
                <th scope="col">TTFB</th>
                <th scope="col">P95 Lat</th>
                <th scope="col">P95 TTFB</th>
                <th scope="col">Failures</th>
                <th scope="col">Last seen</th>
                <th style={{ width: 100 }} scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {models.map((m) => {
                const latest = latestResultByModel.get(m.id);
                const quality = modelQualityById.get(m.id);
                const status = !m.enabled ? "disabled" : latest?.success ? "ok" : latest ? "fail" : "unknown";
                return (
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
                      <div className="model-name-cell">
                        <strong>{m.model_id}</strong>
                        {m.display_name && <span>{m.display_name}</span>}
                      </div>
                    </td>
                    <td>
                      <span className={`model-status-pill ${status}`}>
                        <span className={`status-dot ${status === "disabled" ? "warn" : status}`} />
                        {status}
                      </span>
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
                            withErrorToast(
                              api.patchModel(m.id, { enabled: !m.enabled }),
                              "切换 enabled",
                            ).then(refresh)
                          }
                          aria-label={`Enable ${m.model_id}`}
                        />
                        <span style={{ fontSize: 12 }}>
                          {m.enabled ? "on" : "off"}
                        </span>
                      </label>
                    </td>
                    <td className="muted">
                      {latest ? (
                        <span title={formatTime(latest.checked_at)}>
                          {formatRelative(latest.checked_at)}
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="mono">{formatPercent(quality?.availability24h)}</td>
                    <td className="mono">{quality?.samples24h ?? 0}</td>
                    <td className="mono">{formatMs(latest?.latency_ms ?? null)}</td>
                    <td className="mono">{formatMs(latest?.ttfb_ms ?? null)}</td>
                    <td className="mono">{formatMs(quality?.p95Latency)}</td>
                    <td className="mono">{formatMs(quality?.p95Ttfb)}</td>
                    <td>
                      <span className={`pill ${quality?.consecutiveFailures ? "warn" : "ok"}`}>
                        {quality?.consecutiveFailures ?? 0}
                      </span>
                    </td>
                    <td className="muted">
                      <span title={formatTime(m.last_seen_at)}>
                        {formatRelative(m.last_seen_at)}
                      </span>
                    </td>
                    <td>
                      <button
                        className="secondary sm"
                        onClick={() =>
                          withErrorToast(api.probeNow(providerId, m.id), "检测模型").then(refresh)
                        }
                        aria-label={`检测模型 ${m.model_id}`}
                        title="只检测这个模型的可用性和延迟"
                      >
                        <IconProbe />
                        检测模型
                      </button>
                    </td>
                  </tr>
                );
              })}
              {models.length === 0 && (
                <tr>
                  <td colSpan={15}>
                    <div className="empty-state">
                      <p>没有模型。点 “更新模型清单” 拉取。</p>
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
