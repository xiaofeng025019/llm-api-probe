import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, ModelOut, ProbeResult, Provider, Setting } from "../api/types";
import { withErrorToast } from "../lib/action";
import {
  formatMs,
  formatPercent,
  parseApiDate,
  formatRelative,
  formatTime,
  modelHealthClass,
  modelHealthLabel,
  modelTypeColor,
  statusColor,
} from "../lib/format";
import { modelStatusIntervalLabel } from "../lib/settings";
import { useSse } from "../hooks/useSse";
// recharts is ~300KB gzipped; lazy-load it so the dashboard
// initial bundle doesn't pay that cost. Only fetched when the
// user opens a provider detail page.
const ResultsChart = lazy(() =>
  import("../components/ResultsChart").then((m) => ({ default: m.ResultsChart })),
);
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
  IconPlus,
} from "../components/Icons";
import { useT } from "../hooks/useT";

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
  const providerId = id!;
  const nav = useNavigate();
  const t = useT();

  const [provider, setProvider] = useState<Provider | null>(null);
  const [models, setModels] = useState<ModelOut[]>([]);
  const [results, setResults] = useState<ProbeResult[]>([]);
  const [modelStatusResults, setModelStatusResults] = useState<ProbeResult[]>([]);
  const [settings, setSettings] = useState<Setting[]>([]);
  const [window_, setWindow] = useState(WINDOWS[1]);
  const [error, setError] = useState<string | null>(null);
  const [expandedModels, setExpandedModels] = useState<Record<string, boolean>>({});
  const [newModelId, setNewModelId] = useState("");
  const [addingModel, setAddingModel] = useState(false);

  async function refresh() {
    if (!providerId) return;
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
    const byModel = new Map<string, ProbeResult>();
    for (const r of modelStatusResults) {
      if (r.model_id == null || r.target !== "chat_completion") continue;
      if (!byModel.has(r.model_id)) byModel.set(r.model_id, r);
    }
    return byModel;
  }, [modelStatusResults]);

  const modelQualityById = useMemo(() => {
    const cutoff = Date.now() - 24 * 60 * 60 * 1000;
    const grouped = new Map<string, ProbeResult[]>();
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
                  {t("providerDetail.hero.disabledBadge")}
                </span>
              )}
            </div>
            <h1>{provider.name}</h1>
            <div className="meta">
              <span>
                <IconClock /> {t("providerDetail.hero.listEvery", { n: provider.interval_seconds })}
              </span>
              <span>·</span>
              <span>
                {t("providerDetail.hero.statusProbe", { n: statusIntervalLabel })}
              </span>
              <span>·</span>
              <span>
                <IconProbe /> {t("providerDetail.hero.timeout", { n: provider.timeout_seconds })}
              </span>
              {provider.proxy && (
                <>
                  <span>·</span>
                  <span>{t("providerDetail.hero.proxy", { url: provider.proxy })}</span>
                </>
              )}
            </div>
          </div>
          <div className="hero-actions">
            <button
              className="secondary"
              onClick={async () => {
                try {
                  await withErrorToast(api.syncModels(providerId), t("providerDetail.hero.syncModels"));
                  await refresh();
                } catch {
                  /* toast already shown */
                }
              }}
              title={t("dashboard.card.syncModelsTitle")}
            >
              <IconRefresh />
              {t("providerDetail.hero.syncModels")}
            </button>
            <button
              onClick={() =>
                withErrorToast(api.runNow(providerId), t("providerDetail.hero.probeStatus")).then(refresh)
              }
              title={t("dashboard.card.probeStatusTitle")}
            >
              <IconPlay />
              {t("providerDetail.hero.probeStatus")}
            </button>
          </div>
        </div>
      </div>

      <div className="stat-grid stagger">
        <Stat
          label={t("providerDetail.stat.latestStatus")}
          value={lastResult ? (lastResult.success ? "OK" : "Failed") : "—"}
          accentColor={statusColor(
            lastResult ? (lastResult.success ? "ok" : "fail") : null,
          )}
          icon={lastResult?.success ? <IconCheck /> : <IconAlert />}
        />
        <Stat
          label={t("providerDetail.stat.latestLatency")}
          value={formatMs(lastResult?.latency_ms ?? null)}
          icon={<IconClock />}
        />
        <Stat
          label={t("providerDetail.stat.latestTtfb")}
          value={formatMs(lastResult?.ttfb_ms ?? null)}
          icon={<IconActivity />}
        />
        <Stat
          label={t("providerDetail.stat.availabilityWindow", { window: window_.label })}
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
          hint={t("providerDetail.stat.hintSuccess", { success: successes, total: results.length })}
          icon={<IconChart />}
        />
        <Stat
          label={t("providerDetail.stat.avgLatencyWindow", { window: window_.label })}
          value={formatMs(avgLatency)}
          icon={<IconClock />}
        />
        <Stat
          label={t("providerDetail.stat.p95LatencyWindow", { window: window_.label })}
          value={formatMs(p95Latency)}
          icon={<IconGauge />}
        />
        <Stat
          label={t("providerDetail.stat.p95TtfbWindow", { window: window_.label })}
          value={formatMs(p95Ttfb)}
          icon={<IconActivity />}
        />
        <Stat
          label={t("providerDetail.stat.samplesWindow", { window: window_.label })}
          value={qualityResults.length}
          hint={t("providerDetail.stat.hintSamples", { success: successes, failed: failures })}
          icon={<IconHash />}
        />
      </div>

      <div className="section fade-up">
        <div className="section-header">
          <div className="section-title">
            <IconGauge />
            {t("providerDetail.quality.modelList")}
          </div>
          <span className="muted">{window_.label} {t("dashboard.card.availability24h").replace("24h Availability", "window")}</span>
        </div>
        <div className="provider-quality-grid">
          <div className="quality-tile quality-wide">
            <div className="quality-tile-head">
              <span>
                <IconGlobe />
                {t("providerDetail.quality.endpoint")}
              </span>
              <span className={`pill ${provider.enabled ? "ok" : "warn"}`}>
                {provider.enabled ? t("providerDetail.quality.monitoringOn") : t("providerDetail.quality.monitoringOff")}
              </span>
            </div>
            <div className="endpoint-value" title={provider.base_url}>
              {provider.base_url}
            </div>
            <div className="quality-meta">
              <span>{t("providerDetail.quality.timeout", { n: provider.timeout_seconds })}</span>
              <span>{t("providerDetail.quality.listEvery", { n: statusIntervalLabel })}</span>
              <span>{t("providerDetail.quality.modelInterval", { n: provider.interval_seconds })}</span>
              {provider.proxy && <span>{t("providerDetail.quality.proxy")}</span>}
            </div>
          </div>

          <div className="quality-tile">
            <div className="quality-tile-head">
              <span>
                <IconRefresh />
                {t("providerDetail.quality.modelList")}
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
                {t("providerDetail.quality.modelCoverage")}
              </span>
            </div>
            <div className="quality-value">
              {availableModels} / {enabledModels}
            </div>
            <div className="quality-meta">
              <span>{t("providerDetail.quality.availableModels")}</span>
              <span>{t("providerDetail.quality.total", { n: models.length })}</span>
            </div>
          </div>

          <div className="quality-tile">
            <div className="quality-tile-head">
              <span>
                <IconNetwork />
                {t("providerDetail.quality.communication")}
              </span>
            </div>
            <div className="quality-value">{formatMs(p95Latency)}</div>
            <div className="quality-meta">
              <span>{t("providerDetail.quality.p95Latency")}</span>
              <span>{t("providerDetail.quality.p95Ttfb")} {formatMs(p95Ttfb)}</span>
              <span>{t("providerDetail.quality.avg", { n: formatMs(avgLatency) })}</span>
            </div>
          </div>

          <div className="quality-tile">
            <div className="quality-tile-head">
              <span>
                <IconAlert />
                {t("providerDetail.quality.errors")}
              </span>
              <span className={`pill ${failures > 0 ? "fail" : "ok"}`}>{failures}</span>
            </div>
            {selectedWindowErrors.length > 0 ? (
              <div className="error-chip-list" aria-label={t("providerDetail.quality.errors")}>
                {selectedWindowErrors.slice(0, 4).map(([code, count]) => (
                  <span className="error-chip" key={code}>
                    {code} <strong>{count}</strong>
                  </span>
                ))}
              </div>
            ) : (
              <div className="quality-value">{t("providerDetail.quality.noErrors")}</div>
            )}
            <div className="quality-meta">
              <span>{t("providerDetail.quality.modelProbes", { n: qualityResults.length })}</span>
            </div>
          </div>
        </div>
      </div>

      <div className="card section fade-up">
        <div className="section-header">
          <div className="section-title">
            <IconChart />
            {t("providerDetail.chart.title")}
          </div>
          <div
            className="window-tabs"
            role="tablist"
            aria-label={t("providerDetail.chart.windowTabs")}
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
        <div role="tabpanel" aria-label={t("providerDetail.chart.tabPanel", { window: window_.label })}>
          <Suspense fallback={<div style={{ height: 240 }} />}>
            <ResultsChart results={results} />
          </Suspense>
        </div>
      </div>

      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <IconModels />
            {t("providerDetail.models.title")}
          </div>
          <span className="muted">{t("providerDetail.models.count", { n: models.length })}</span>
          <span className="grow" />
          <div className="input-with-actions" style={{ maxWidth: 320 }}>
            <input
              value={newModelId}
              onChange={(e) => setNewModelId(e.target.value)}
              placeholder={t("providerDetail.models.addPlaceholder")}
              disabled={addingModel}
              style={{ fontSize: 13 }}
            />
            <button
              className="btn-ghost sm"
              disabled={addingModel || !newModelId.trim()}
              onClick={async () => {
                const mid = newModelId.trim();
                if (!mid) return;
                setAddingModel(true);
                try {
                  await withErrorToast(
                    api.addModel(providerId, { model_id: mid }),
                    t("providerDetail.models.addAction")
                  );
                  setNewModelId("");
                  await refresh();
                } catch {
                  /* toast already shown */
                } finally {
                  setAddingModel(false);
                }
              }}
            >
              {addingModel ? <span className="spinner" /> : <IconPlus size={14} />}
              {t("providerDetail.models.addAction")}
            </button>
          </div>
        </div>
        {models.length === 0 ? (
          <div className="empty-state" style={{ padding: 32 }}>
            <p>{t("providerDetail.models.empty")}</p>
          </div>
        ) : (
          <div className="model-card-grid fade-up">
            {models.map((m) => {
              const latest = latestResultByModel.get(m.id);
              const quality = modelQualityById.get(m.id);
              const statusClass = modelHealthClass(m.status, m.enabled);
              const statusLabel = modelHealthLabel(m.status, m.enabled);
              const avail = quality?.availability24h;
              const availColor =
                avail == null ? "var(--unknown)" : avail >= 99 ? "var(--ok)" : avail >= 90 ? "var(--warn)" : "var(--fail)";
              const expanded = expandedModels[m.id] ?? false;
              return (
                <div
                  key={m.id}
                  className={"model-card" + (expanded ? " model-card-expanded" : "")}
                  style={{ "--model-type-color": modelTypeColor(m.type) } as React.CSSProperties}
                >
                  {/* ---- Header: name + type + actions ---- */}
                  <div className="model-card-head">
                    <div className="model-card-title">
                      <button
                        className={"favorite-star" + (m.is_favorite ? " active" : "")}
                        onClick={() =>
                          api.patchModel(m.id, { is_favorite: !m.is_favorite }).then(refresh)
                        }
                        aria-label={m.is_favorite ? t("providerDetail.models.card.favoriteAriaOn") : t("providerDetail.models.card.favoriteAriaOff")}
                        aria-pressed={m.is_favorite}
                      >
                        <IconStarOutline filled={m.is_favorite} />
                      </button>
                      <span className="model-card-name" title={m.display_name || m.model_id}>
                        {m.model_id}
                      </span>
                      <span className="model-card-type" style={{ color: "var(--model-type-color)" }}>
                        {m.type}
                      </span>
                    </div>
                    <div className="model-card-actions">
                      <button
                        className="ghost sm"
                        onClick={() =>
                          withErrorToast(
                            api.patchModel(m.id, { enabled: !m.enabled }),
                            t("common.disable"),
                          ).then(refresh)
                        }
                        aria-label={m.enabled ? t("providerDetail.models.card.disableAria") : t("providerDetail.models.card.enableAria")}
                        title={m.enabled ? t("providerDetail.models.card.disableTitle") : t("providerDetail.models.card.enableTitle")}
                      >
                        {m.enabled ? <IconCheck /> : <IconAlert />}
                      </button>
                      <button
                        className="ghost sm"
                        onClick={() =>
                          withErrorToast(api.probeNow(providerId, m.id), t("dashboard.card.probeStatus")).then(refresh)
                        }
                        aria-label={t("providerDetail.models.card.probeAria", { name: m.model_id })}
                        title={t("providerDetail.models.card.probeTitle")}
                      >
                        <IconProbe />
                      </button>
                      <button
                        className="ghost sm"
                        onClick={() =>
                          setExpandedModels((prev) => ({ ...prev, [m.id]: !prev[m.id] }))
                        }
                        aria-label={expanded ? t("providerDetail.models.card.detailsAriaOn") : t("providerDetail.models.card.detailsAriaOff")}
                        title={expanded ? t("providerDetail.models.card.detailsTitleOn") : t("providerDetail.models.card.detailsTitleOff")}
                      >
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          {expanded
                            ? <polyline points="18 15 12 9 6 15" />
                            : <polyline points="6 9 12 15 18 9" />
                          }
                        </svg>
                      </button>
                    </div>
                  </div>

                  {/* ---- Status line ---- */}
                  <div className="model-card-status">
                    <span className={"status-dot " + statusClass} />
                    <span className={"model-status-text " + statusClass}>{statusLabel}</span>
                    {m.status_reason && (
                      <span className="model-status-reason">{m.status_reason}</span>
                    )}
                    {m.consecutive_failures > 0 && (
                      <span className="pill warn" style={{ fontSize: 10, padding: "1px 6px" }}>
                        {t("providerDetail.models.card.metricFail", { n: m.consecutive_failures })}
                      </span>
                    )}
                    {!m.enabled && (
                      <span className="pill warn" style={{ fontSize: 10, padding: "1px 6px" }}>
                        {t("providerDetail.models.card.metricDisabled")}
                      </span>
                    )}
                    <span className="model-status-age">
                      {m.status_checked_at
                        ? formatRelative(m.status_checked_at)
                        : m.status_confirmed_at
                          ? t("providerDetail.models.card.metricConfirmed")
                          : t("providerDetail.models.card.metricUnconfirmed")}
                    </span>
                  </div>

                  {/* ---- Collapsed: 4 key metrics ---- */}
                  <div className="model-card-metrics">
                    <div className="model-metric">
                      <span className="model-metric-label">{t("providerDetail.models.card.barLabel24h")}</span>
                      <span className="model-metric-value" style={{ color: availColor }}>
                        {formatPercent(avail)}
                      </span>
                    </div>
                    <div className="model-metric">
                      <span className="model-metric-label">{t("providerDetail.models.card.samplesLabel")}</span>
                      <span className="model-metric-value">{quality?.samples24h ?? 0}</span>
                    </div>
                    <div className="model-metric">
                      <span className="model-metric-label">{t("providerDetail.models.card.p95Label")}</span>
                      <span className="model-metric-value">{formatMs(quality?.p95Latency)}</span>
                    </div>
                    <div className="model-metric">
                      <span className="model-metric-label">{t("providerDetail.models.card.ttfbLabel")}</span>
                      <span className="model-metric-value">{formatMs(quality?.p95Ttfb)}</span>
                    </div>
                  </div>

                  {/* ---- Availability bar ---- */}
                  <div className="model-card-bar">
                    <div
                      className="model-card-bar-fill"
                      style={{
                        width: avail != null ? Math.min(100, avail) + "%" : "0%",
                        background: availColor,
                      }}
                    />
                  </div>

                  {/* ---- Expanded: detailed metrics ---- */}
                  {expanded && (
                    <div className="model-card-details">
                      <div className="model-details-grid">
                        <div className="model-detail-item">
                          <span className="model-detail-label">{t("providerDetail.models.card.labelLatestLatency")}</span>
                          <span className="model-detail-value">{formatMs(latest?.latency_ms)}</span>
                        </div>
                        <div className="model-detail-item">
                          <span className="model-detail-label">{t("providerDetail.models.card.labelLatestTtfb")}</span>
                          <span className="model-detail-value">{formatMs(latest?.ttfb_ms)}</span>
                        </div>
                        <div className="model-detail-item">
                          <span className="model-detail-label">{t("providerDetail.models.card.labelP95Latency")}</span>
                          <span className="model-detail-value">{formatMs(quality?.p95Latency)}</span>
                        </div>
                        <div className="model-detail-item">
                          <span className="model-detail-label">{t("providerDetail.models.card.labelP95Ttfb")}</span>
                          <span className="model-detail-value">{formatMs(quality?.p95Ttfb)}</span>
                        </div>
                        <div className="model-detail-item">
                          <span className="model-detail-label">{t("providerDetail.models.card.labelFailures")}</span>
                          <span className={"model-detail-value" + (m.consecutive_failures > 0 ? " text-warn" : "")}>
                            {m.consecutive_failures}
                          </span>
                        </div>
                        <div className="model-detail-item">
                          <span className="model-detail-label">{t("providerDetail.models.card.labelAvail")}</span>
                          <span className="model-detail-value" style={{ color: availColor }}>
                            {formatPercent(avail)}
                          </span>
                        </div>
                      </div>
                      <div className="model-details-footer">
                        <span>
                          {t("providerDetail.models.card.statusChecked", { t: m.status_checked_at ? formatTime(m.status_checked_at) : "—" })}
                        </span>
                        {m.last_success_at && (
                          <span>
                            {t("providerDetail.models.card.lastSuccess", { t: formatTime(m.last_success_at) })}
                          </span>
                        )}
                        <span>
                          {t("providerDetail.models.card.lastSeen", { t: formatTime(m.last_seen_at) })}
                        </span>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      <button
        className="ghost"
        onClick={() => nav("/")}
        style={{ marginTop: 16 }}
      >
        <IconBack />
        {t("common.back")} {t("nav.dashboard")}
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
