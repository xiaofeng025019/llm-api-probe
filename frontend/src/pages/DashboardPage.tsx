import { useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { useDashboard } from "../hooks/useDashboard";
import {
  formatMs,
  formatPercent,
  formatRelative,
  modelHealthClass,
  modelHealthLabel,
  modelTypeColor,
  statusColor,
} from "../lib/format";
import { modelStatusIntervalLabel, modelStatusIntervalValue } from "../lib/settings";
import { api, type DashboardFavoriteModel, type DashboardProvider, type Provider } from "../api/types";
import { withErrorToast } from "../lib/action";
import {
  IconPlay,
  IconRefresh,
  IconDelete,
  IconEdit,
  IconPlus,
  IconServer,
  IconCheck,
  IconStarOutline,
  KindIcon,
  IconActivity,
  IconClock,
  IconArrowRight,
} from "../components/Icons";
import { Skeleton, SkeletonProviderCard, SkeletonStat } from "../components/Skeleton";
import { pushToast } from "../components/Toast";
import { ProviderDialog } from "../components/ProviderDialog";
import { useT } from "../hooks/useT";
import { t as i18nT } from "../lib/i18n";

/** Build the secondary line for the Favorite Models card. Includes a
 *  delta vs 24h ago when the backend supplies favorites_online_24h_ago
 *  (newer backends); older backends fall back to the static percent. */
function favoritesSub(totals: {
  favorites_total: number;
  favorites_online: number;
  favorites_online_24h_ago?: number;
}): string {
  if (totals.favorites_total === 0) return i18nT("dashboard.favoritesSub.none");
  const pct = Math.round(
    (totals.favorites_online / Math.max(1, totals.favorites_total)) * 100,
  );
  const base = i18nT("dashboard.favoritesSub.base", { pct });
  if (totals.favorites_online_24h_ago == null) return base;
  const delta = totals.favorites_online - totals.favorites_online_24h_ago;
  if (delta === 0) return i18nT("dashboard.favoritesSub.deltaFlat", { base });
  const sign = delta > 0 ? "↑" : "↓";
  const abs = Math.abs(delta);
  const word = i18nT(abs === 1 ? "dashboard.favoritesSub.model" : "dashboard.favoritesSub.models");
  return i18nT(
    delta > 0 ? "dashboard.favoritesSub.deltaUp" : "dashboard.favoritesSub.deltaDown",
    { base, n: abs, model: word },
  );
}

function modelStatusLabel(model: DashboardFavoriteModel): string {
  return modelHealthLabel(model.status, model.enabled);
}

function errorSummary(counts: Record<string, number>): string {
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) return i18nT("format.noErrors");
  return entries.map(([code, count]) => `${code} ${count}`).join(" · ");
}

export function DashboardPage() {
  const nav = useNavigate();
  const t = useT();
  const { dashboard, settings, loading, error, refresh } = useDashboard(true, {
    loadModels: false,
    loadProviders: false,
  });
  const [runningProviders, setRunningProviders] = useState<Record<string, boolean>>({});
  const [syncingProviders, setSyncingProviders] = useState<Record<string, boolean>>({});
  const [togglingProviders, setTogglingProviders] = useState<Record<string, boolean>>({});
  const [showAddProvider, setShowAddProvider] = useState(false);
  const [editingProvider, setEditingProvider] = useState<Provider | null>(null);
  const [refreshingAll, setRefreshingAll] = useState(false);
  const activeDashboardProviders = dashboard?.providers.filter((p) => p.enabled) ?? [];
  const dashboardProviders = dashboard?.providers ?? [];
  const statusIntervalLabel = modelStatusIntervalLabel(settings);
  const statusIntervalValue = modelStatusIntervalValue(settings);

  async function onRefreshAll() {
    if (refreshingAll) return;
    setRefreshingAll(true);
    try {
      await api.probeAll();
    } catch {
      /* toast handled by withErrorToast; the polling refresh will pick up partial results */
    } finally {
      // Briefly show the indicator even on success, so the user gets
      // feedback that the click did something. The actual probes run
      // async in the background and land via SSE / 30s polling.
      setTimeout(() => setRefreshingAll(false), 1500);
    }
  }

  async function onDelete(providerId: string, providerName: string) {
    if (!confirm(t("common.confirmDeleteProvider", { name: providerName }))) return;
    try {
      await withErrorToast(api.deleteProvider(providerId), t("common.delete"));
      pushToast("ok", t("toast.deleted"), providerName);
      await refresh();
    } catch {
      /* toast already shown */
    }
  }

  async function toggleMonitoring(p: DashboardProvider) {
    const nextEnabled = !p.enabled;
    setTogglingProviders((prev) => ({ ...prev, [p.provider_id]: true }));
    try {
      await withErrorToast(
        api.patchProvider(p.provider_id, { enabled: nextEnabled }),
        nextEnabled ? t("providers.card.monitorTitleOff") : t("providers.card.monitorTitleOn"),
      );
      pushToast("ok", nextEnabled ? t("toast.monitoringOn") : t("toast.monitoringOff"), p.name);
      await refresh();
    } catch {
      /* toast already shown */
    } finally {
      setTogglingProviders((prev) => {
        const next = { ...prev };
        delete next[p.provider_id];
        return next;
      });
    }
  }

  function toProvider(p: DashboardProvider): Provider {
    return {
      id: p.provider_id,
      name: p.name,
      kind: p.kind,
      base_url: p.base_url,
      enabled: p.enabled,
      interval_seconds: p.interval_seconds,
      timeout_seconds: p.timeout_seconds,
      proxy: p.proxy,
      headers_json: p.headers_json,
      created_at: "",
      updated_at: "",
    };
  }

  if (loading && !dashboard) {
    return (
      <div>
        <div className="page-header">
          <div>
            <h1>{t("dashboard.title")}</h1>
            <div className="subtitle">{t("dashboard.subtitle")}</div>
          </div>
        </div>
        <div className="stat-grid">
          <SkeletonStat />
          <SkeletonStat />
          <SkeletonStat />
          <SkeletonStat />
          <SkeletonStat />
        </div>
        <div className="provider-grid stagger">
          <SkeletonProviderCard />
          <SkeletonProviderCard />
          <SkeletonProviderCard />
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>{t("dashboard.title")}</h1>
          <div className="subtitle">{t("dashboard.subtitle")}</div>
        </div>
        <div className="page-header-actions">
          <button
            type="button"
            className="btn btn-ghost"
            onClick={onRefreshAll}
            disabled={refreshingAll}
            title={t("dashboard.refreshAll.title")}
            aria-label={t("dashboard.refreshAll.ariaLabel")}
          >
            <span className={refreshingAll ? "spin" : ""} aria-hidden="true">
              <IconRefresh />
            </span>
            <span>{t("dashboard.refreshAll.label")}</span>
          </button>
        </div>
      </div>

      {error && <div className="error" role="alert">{error}</div>}

      {dashboard && (
        <div className="stat-grid stagger">
          <StatCard
            label={t("dashboard.stat.providers.label")}
            value={`${activeDashboardProviders.length} / ${dashboard.totals.providers}`}
            sub={t("dashboard.stat.providers.sub")}
            icon={<IconServer />}
            accentColor="var(--primary)"
          />
          <StatCard
            label={t("dashboard.stat.availableModels.label")}
            value={
              dashboard.totals.models > 0
                ? `${dashboard.totals.available_models ?? 0} / ${dashboard.totals.models}`
                : "—"
            }
            sub={
              dashboard.totals.models > 0
                ? `${Math.round(((dashboard.totals.available_models ?? 0) / dashboard.totals.models) * 100)}% ${t("dashboard.stat.availableModels.sub")}`
                : t("dashboard.stat.availableModels.sub")
            }
            progress={
              dashboard.totals.models > 0
                ? ((dashboard.totals.available_models ?? 0) / dashboard.totals.models) * 100
                : null
            }
            icon={<IconCheck />}
            accentColor="var(--ok)"
          />
          <StatCard
            label={t("dashboard.stat.favoriteModels.label")}
            value={`${dashboard.totals.favorites_online} / ${dashboard.totals.favorites_total}`}
            icon={<IconStarOutline filled />}
            accentColor="var(--warn)"
            sub={favoritesSub(dashboard.totals)}
            progress={
              dashboard.totals.favorites_total > 0
                ? (dashboard.totals.favorites_online / dashboard.totals.favorites_total) * 100
                : null
            }
          />
        </div>
      )}

      {dashboard && dashboardProviders.length > 0 && (
        <div className="section">
          <div className="section-header">
            <div className="section-title">
              <IconServer />
              {t("dashboard.section.activeProviders")}
            </div>
            <span className="muted">
              {t("dashboard.section.activeCount", { n: activeDashboardProviders.length })}
            </span>
          </div>
          <div className="dashboard-provider-list stagger">
            {dashboardProviders.map((p) => {
              const isRunning = !!runningProviders[p.provider_id];
              const isSyncing = !!syncingProviders[p.provider_id];
              const isToggling = !!togglingProviders[p.provider_id];
              const status = p.last_status ?? "unknown";
              const visibleStatus = p.enabled ? status : "unknown";
              return (
                <article
                  key={p.provider_id}
                  className={`provider-card dashboard-provider-card${p.enabled ? "" : " dashboard-provider-paused"}`}
                  style={{ ["--status-color" as string]: statusColor(p.enabled ? p.last_status : null) }}
                  aria-label={`${p.name}, ${p.enabled ? t("status." + status) : t("dashboard.card.monitoringOff")}`}
                >
                  <div className="head">
                    <div>
                      <div className="name">
                        <span
                          className={`status-dot ${visibleStatus}`}
                          title={p.enabled ? `${t("status." + status)}` : t("dashboard.card.monitoringOff")}
                          aria-label={p.enabled ? `${t("status." + status)}` : t("dashboard.card.monitoringOff")}
                        />
                        {p.name}
                        <span className={`pill ${visibleStatus}`}>
                          {p.enabled ? t("status." + status) : t("dashboard.card.monitoringOff")}
                        </span>
                      </div>
                      <div className="meta">
                        <span className="kind-badge">
                          <KindIcon kind={p.kind} /> {p.kind}
                        </span>
                        <span style={{ marginLeft: 8 }}>
                          {p.model_count} models · {t("dashboard.card.listEvery", { n: p.interval_seconds })}
                        </span>
                      </div>
                      <div className="dashboard-endpoint-line" title={p.base_url}>
                        {p.base_url}
                        {p.proxy && <span>{t("providers.dialog.labelProxy")}: {p.proxy}</span>}
                        <span>{t("providers.dialog.labelTimeout")}: {p.timeout_seconds}s</span>
                      </div>
                      <div className="dashboard-monitoring-line">
                        <label
                          className={`monitoring-switch ${isToggling ? "busy" : ""}`}
                          onClick={(e) => e.stopPropagation()}
                          onKeyDown={(e) => e.stopPropagation()}
                          title={
                            p.enabled
                              ? t("providers.card.monitorTitleOn")
                              : t("providers.card.monitorTitleOff")
                          }
                        >
                          <input
                            type="checkbox"
                            checked={p.enabled}
                            disabled={isToggling}
                            onChange={() => void toggleMonitoring(p)}
                            aria-label={
                              p.enabled
                                ? t("providers.card.monitorAriaOn", { name: p.name })
                                : t("providers.card.monitorAriaOff", { name: p.name })
                            }
                          />
                          <span className="monitoring-switch-track" />
                          <span className="monitoring-switch-text">
                            {isToggling
                              ? t("dashboard.card.monitoringUpdating")
                              : p.enabled
                                ? t("dashboard.card.monitoringOn")
                                : t("dashboard.card.monitoringOff")}
                          </span>
                        </label>
                        {p.enabled && (
                          <>
                            <span className={`pill ${p.list_models_status ?? "unknown"}`}>
                              {t("dashboard.card.modelList")} {p.list_models_status ?? t("common.unknown")}
                            </span>
                            <span className="muted">{statusIntervalLabel}</span>
                          </>
                        )}
                        {isRunning && <span className="pill info">{t("dashboard.card.probeQueued")}</span>}
                        {isSyncing && <span className="pill info">{t("dashboard.card.syncingList")}</span>}
                      </div>
                    </div>
                  </div>

                  {p.enabled && (
                    <div className="dashboard-provider-body">
                      <div className="dashboard-provider-summary">
                        <div className="metrics">
                          <div className="metric">
                            <div className="label">{t("dashboard.card.availability24h")}</div>
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
                            <div className="label">{t("dashboard.card.p95Latency")}</div>
                            <div className="value">{formatMs(p.p95_latency_ms_24h)}</div>
                          </div>
                          <div className="metric">
                            <div className="label">{t("dashboard.card.p95Ttfb")}</div>
                            <div className="value">{formatMs(p.p95_ttfb_ms_24h)}</div>
                          </div>
                          <div className="metric">
                            <div className="label">{t("dashboard.card.samplesFailures")}</div>
                            <div className="value">{p.samples_24h} / {p.failures_24h}</div>
                          </div>
                          <div className="metric dashboard-wide-metric">
                            <div className="label">{t("dashboard.card.modelList")}</div>
                            <div className="value">
                              {p.list_models_status ? (
                                <>
                                  {t("status." + (p.list_models_status === "ok" ? "online" : p.list_models_status === "fail" ? "offline" : "unknown"))}
                                  <span className="metric-subvalue">
                                    {formatMs(p.list_models_latency_ms)} · {formatRelative(p.list_models_checked_at)}
                                  </span>
                                </>
                              ) : (
                                "—"
                              )}
                            </div>
                          </div>
                          <div className="metric dashboard-wide-metric">
                            <div className="label">{t("dashboard.stat.availableModels.label")}</div>
                            <div className="value">
                              {p.model_count > 0
                                ? `${p.available_models_online} / ${p.model_count}`
                                : "—"}
                            </div>
                          </div>
                          <div className="metric dashboard-wide-metric">
                            <div className="label">{t("dashboard.section.favoriteModels")}</div>
                            <div className="value">
                              {p.favorite_models_total > 0
                                ? `${p.favorite_models_online} / ${p.favorite_models_total}`
                                : "—"}
                            </div>
                          </div>
                          <div className="metric dashboard-wide-metric">
                            <div className="label">{t("dashboard.card.errors24h")}</div>
                            <div className="value metric-compact-value">
                              {errorSummary(p.error_counts_24h)}
                            </div>
                          </div>
                        </div>
                      </div>

                      <div className="dashboard-model-panels">
                        <div className="dashboard-model-panel">
                          <div className="dashboard-favorites-head">
                            <span>
                              <IconStarOutline filled />
                              {t("dashboard.section.favoriteModels")}
                            </span>
                            <strong>
                              {p.favorite_models_online}/{p.favorite_models_total}
                            </strong>
                          </div>
                          {(p.favorite_models ?? []).length > 0 ? (
                            <div className="favorite-model-list">
                              {(p.favorite_models ?? []).map((model) => (
                                <FavoriteModelStatus
                                  key={model.id}
                                  model={model}
                                  providerId={p.provider_id}
                                  providerName={p.name}
                                  onRefresh={refresh}
                                />
                              ))}
                            </div>
                          ) : (
                            <div className="favorite-model-empty">
                              {t("dashboard.section.noFavorites")}
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  )}

                  <div className="actions">
                    <button
                      className="ghost sm"
                      onClick={() => setEditingProvider(toProvider(p))}
                      aria-label={`${t("providers.card.edit")} ${p.name}`}
                      title={t("providers.card.edit")}
                    >
                      <IconEdit />
                    </button>
                    {p.enabled && (
                      <button
                        className="secondary sm"
                        disabled={isRunning}
                        onClick={async () => {
                          setRunningProviders((prev) => ({ ...prev, [p.provider_id]: true }));
                          try {
                            await withErrorToast(api.runNow(p.provider_id), t("dashboard.card.probeStatus"));
                            pushToast("info", t("toast.probeQueued"), p.name);
                            window.setTimeout(() => {
                              void refresh().finally(() => {
                                setRunningProviders((prev) => {
                                  const next = { ...prev };
                                  delete next[p.provider_id];
                                  return next;
                                });
                              });
                            }, 8_000);
                          } catch {
                            setRunningProviders((prev) => {
                              const next = { ...prev };
                              delete next[p.provider_id];
                              return next;
                            });
                          }
                        }}
                        title={t("dashboard.card.probeStatusTitle")}
                        aria-label={t("dashboard.card.probeStatus")}
                      >
                        {isRunning ? <span className="spinner" /> : <IconPlay />}
                        {isRunning ? t("common.saving") : t("dashboard.card.probeStatus")}
                      </button>
                    )}
                    {p.enabled && (
                      <button
                        className="secondary sm"
                        disabled={isSyncing}
                        onClick={async () => {
                          setSyncingProviders((prev) => ({ ...prev, [p.provider_id]: true }));
                          try {
                            await withErrorToast(api.syncModels(p.provider_id), t("dashboard.card.syncModels"));
                            pushToast("ok", t("toast.modelListUpdated"), p.name);
                            await refresh();
                          } finally {
                            setSyncingProviders((prev) => {
                              const next = { ...prev };
                              delete next[p.provider_id];
                              return next;
                            });
                          }
                        }}
                        title={t("dashboard.card.syncModelsTitle")}
                        aria-label={t("dashboard.card.syncModels")}
                      >
                        {isSyncing ? <span className="spinner" /> : <IconRefresh />}
                        {isSyncing ? t("dashboard.card.syncingList") : t("dashboard.card.syncModels")}
                      </button>
                    )}
                    <button
                      className="ghost sm"
                      onClick={() => onDelete(p.provider_id, p.name)}
                      aria-label={t("common.delete")}
                      title={t("common.delete")}
                    >
                      <IconDelete />
                    </button>
                    <span className="grow" />
                    <button
                      className="primary sm dashboard-view-detail"
                      onClick={() => nav(`/providers/${p.provider_id}`)}
                      aria-label={`${t("dashboard.card.viewDetail")} ${p.name}`}
                      title={t("dashboard.card.viewDetailTitle")}
                    >
                      {t("dashboard.card.viewDetail")} <IconArrowRight />
                    </button>
                  </div>
                </article>
              );
            })}
            <button
              className="dashboard-add-provider-card"
              onClick={() => setShowAddProvider(true)}
              aria-label={t("dashboard.section.addProvider")}
            >
              <span className="dashboard-add-provider-icon">
                <IconPlus />
              </span>
              <span className="dashboard-add-provider-copy">
                <strong>{t("dashboard.section.addProvider")}</strong>
                <span>{t("dashboard.section.addProviderDesc")}</span>
              </span>
            </button>
          </div>
        </div>
      )}

      {dashboard && dashboard.providers.length === 0 && !loading && (
        <div className="card fade-up">
          <div className="empty-state">
            <div className="empty-state-icon">
              <IconServer />
            </div>
            <h3>{t("dashboard.section.emptyTitle")}</h3>
            <p>{t("dashboard.section.emptyDesc")}</p>
            <button
              onClick={() => setShowAddProvider(true)}
              style={{ marginTop: 12 }}
            >
              <IconPlus />
              {t("dashboard.section.addProvider")}
            </button>
          </div>
        </div>
      )}
      {showAddProvider && (
        <ProviderDialog
          onClose={() => setShowAddProvider(false)}
          onSaved={refresh}
        />
      )}
      {editingProvider && (
        <ProviderDialog
          provider={editingProvider}
          onClose={() => setEditingProvider(null)}
          onSaved={refresh}
        />
      )}
    </div>
  );
}

function FavoriteModelStatus({
  model,
  providerId,
  providerName,
  onRefresh,
}: {
  model: DashboardFavoriteModel;
  providerId: string;
  providerName: string;
  onRefresh: () => Promise<void>;
}) {
  const t = useT();
  const label = modelStatusLabel(model);
  const statusClass = modelHealthClass(model.status, model.enabled);
  const displayName = model.display_name || model.model_id;

  return (
    <div
      className="favorite-model-item"
      style={{ ["--model-type-color" as string]: modelTypeColor(model.type) }}
    >
      <div className="favorite-model-main">
        <div className="favorite-model-title">
          <span
            className={`status-dot ${statusClass}`}
            title={`${t("dashboard.favoritesSub.model")}: ${label}`}
            aria-label={`${t("dashboard.favoritesSub.model")}: ${label}`}
          />
          <span className="favorite-model-name" title={model.model_id}>
            {displayName}
          </span>
          <span className={`pill ${statusClass}`}>{label}</span>
        </div>
        <div className="favorite-model-meta">
          <span className="favorite-model-type">{model.type}</span>
          <span>{t("dashboard.favoriteMeta.checked", { time: formatRelative(model.last_checked_at) })}</span>
          {model.status !== "online" && model.last_success_at && (
            <span>{t("dashboard.favoriteMeta.lastOk", { time: formatRelative(model.last_success_at) })}</span>
          )}
          <span>{t("dashboard.favoriteMeta.samples", { count: model.samples_24h })}</span>
          {(model.status_reason || model.error_code) && (
            <span className="favorite-model-error">{model.status_reason ?? model.error_code}</span>
          )}
        </div>
      </div>

      <div className="favorite-model-stats" aria-label={`${displayName} metrics`}>
        <div>
          <span>24h</span>
          <strong>{formatPercent(model.availability_24h)}</strong>
        </div>
        <div>
          <span>
            <IconClock />
            P95
          </span>
          <strong>{formatMs(model.p95_latency_ms_24h)}</strong>
        </div>
        <div>
          <span>
            <IconActivity />
            TTFB
          </span>
          <strong>{formatMs(model.p95_ttfb_ms_24h)}</strong>
        </div>
        <div>
          <span>Fails</span>
          <strong>{model.consecutive_failures}</strong>
        </div>
      </div>

      <button
        className="ghost sm favorite-model-run"
        onClick={async (e) => {
          e.stopPropagation();
          try {
            await api.probeNow(providerId, model.id);
            pushToast("info", t("toast.modelProbeTriggered"), `${providerName} / ${displayName}`);
            await onRefresh();
          } catch (e) {
            pushToast("fail", t("toast.triggerFailed"), e instanceof Error ? e.message : String(e));
          }
        }}
        aria-label={`${t("dashboard.card.probeNow")} ${displayName}`}
        title={t("dashboard.card.probeNow")}
      >
        <IconPlay />
      </button>
    </div>
  );
}

function StatCard({
  label,
  value,
  icon,
  accentColor,
  hint,
  sub,
  progress,
  to,
}: {
  label: string;
  value: string | number;
  icon: React.ReactNode;
  accentColor?: string;
  hint?: string;
  sub?: string;
  progress?: number | null;
  to?: string;
}) {
  const t = useT();
  const clickable = !!to;
  const friendlyValue =
    typeof value === "number" ? value.toString() : String(value);
  const ariaLabel = clickable
    ? `${label} ${friendlyValue}${hint ? `, ${hint}` : ""}${sub ? `, ${sub}` : ""}, ${t("dashboard.card.probeNow").toLowerCase()}`
    : `${label} ${friendlyValue}`;
  const inner = (
    <>
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {progress != null && (
        <div
          className="stat-progress"
          role="progressbar"
          aria-valuenow={Math.round(progress)}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={`${label} ${Math.round(progress)}%`}
        >
          <div
            className="stat-progress-fill"
            style={{ width: `${Math.max(0, Math.min(100, progress))}%` }}
          />
        </div>
      )}
      {sub && <div className="hint">{sub}</div>}
      {hint && !sub && <div className="hint">{hint}</div>}
      <div className="stat-icon" aria-hidden="true">
        {icon}
      </div>
      {clickable && (
        <svg
          className="stat-chevron"
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
          style={{ position: "absolute", right: 16, bottom: 14, color: "var(--muted)" }}
        >
          <polyline points="9 18 15 12 9 6" />
        </svg>
      )}
    </>
  );
  if (!clickable) {
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
        {inner}
      </div>
    );
  }
  return (
    <Link
      to={to!}
      className="stat stat-link"
      aria-label={ariaLabel}
      style={
        {
          ["--accent-color" as string]: accentColor,
          ["--accent-bg" as string]: accentColor
            ? `color-mix(in srgb, ${accentColor} 12%, transparent)`
            : undefined,
        } as React.CSSProperties
      }
    >
      {inner}
    </Link>
  );
}
