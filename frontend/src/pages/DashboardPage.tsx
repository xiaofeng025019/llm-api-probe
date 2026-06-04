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
import { api, type DashboardFavoriteModel, type Provider } from "../api/types";
import { withErrorToast } from "../lib/action";
import {
  IconPlay,
  IconRefresh,
  IconDelete,
  IconPlus,
  IconServer,
  IconCheck,
  IconStarOutline,
  KindIcon,
  IconActivity,
  IconClock,
} from "../components/Icons";
import { Skeleton, SkeletonProviderCard, SkeletonStat } from "../components/Skeleton";
import { pushToast } from "../components/Toast";
import { ProviderDialog } from "./ProvidersPage";

/** Build the secondary line for the Favorite Models card. Includes a
 *  delta vs 24h ago when the backend supplies favorites_online_24h_ago
 *  (newer backends); older backends fall back to the static percent. */
function favoritesSub(totals: {
  favorites_total: number;
  favorites_online: number;
  favorites_online_24h_ago?: number;
}): string {
  if (totals.favorites_total === 0) return "No favorite models";
  const pct = Math.round(
    (totals.favorites_online / Math.max(1, totals.favorites_total)) * 100,
  );
  const base = `${pct}% online`;
  if (totals.favorites_online_24h_ago == null) return base;
  const delta = totals.favorites_online - totals.favorites_online_24h_ago;
  if (delta === 0) return `${base} · flat vs 24h ago`;
  const sign = delta > 0 ? "↑" : "↓";
  const word = Math.abs(delta) === 1 ? "model" : "models";
  return `${base} · ${sign}${Math.abs(delta)} ${word} vs 24h ago`;
}

function modelStatusLabel(model: DashboardFavoriteModel): string {
  return modelHealthLabel(model.status, model.enabled);
}

function errorSummary(counts: Record<string, number>): string {
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) return "No errors";
  return entries.map(([code, count]) => `${code} ${count}`).join(" · ");
}

export function DashboardPage() {
  const nav = useNavigate();
  const { dashboard, providers, settings, loading, error, refresh } = useDashboard();
  const [runningProviders, setRunningProviders] = useState<Record<string, boolean>>({});
  const [showAddProvider, setShowAddProvider] = useState(false);
  const activeDashboardProviders = dashboard?.providers.filter((p) => p.enabled) ?? [];
  const statusIntervalLabel = modelStatusIntervalLabel(settings);
  const statusIntervalValue = modelStatusIntervalValue(settings);

  async function onDelete(p: Provider) {
    if (!confirm(`Delete provider ${p.name}?`)) return;
    try {
      await withErrorToast(api.deleteProvider(p.id), "Delete");
      pushToast("ok", "Deleted", p.name);
      await refresh();
    } catch {
      /* toast already shown */
    }
  }

  if (loading && !dashboard) {
    return (
      <div>
        <div className="page-header">
          <div>
            <h1>Dashboard</h1>
            <div className="subtitle">Real-time availability of all LLM providers</div>
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
          <h1>Dashboard</h1>
          <div className="subtitle">Real-time availability of all LLM providers</div>
        </div>
      </div>

      {error && <div className="error" role="alert">{error}</div>}

      {dashboard && (
        <div className="stat-grid stagger">
          <StatCard
            label="Providers"
            value={`${activeDashboardProviders.length} / ${dashboard.totals.providers}`}
            sub="monitoring"
            icon={<IconServer />}
            accentColor="var(--primary)"
            to="/providers"
          />
          <StatCard
            label="Available Models"
            value={
              dashboard.totals.models > 0
                ? `${dashboard.totals.available_models ?? 0} / ${dashboard.totals.models}`
                : "—"
            }
            sub={
              dashboard.totals.models > 0
                ? `${Math.round(((dashboard.totals.available_models ?? 0) / dashboard.totals.models) * 100)}% online 24h`
                : "no models"
            }
            progress={
              dashboard.totals.models > 0
                ? ((dashboard.totals.available_models ?? 0) / dashboard.totals.models) * 100
                : null
            }
            icon={<IconCheck />}
            accentColor="var(--ok)"
            to="/providers"
          />
          <StatCard
            label="Favorite Models"
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

      {dashboard && activeDashboardProviders.length > 0 && (
        <div className="section">
          <div className="section-header">
            <div className="section-title">
              <IconServer />
              Providers
            </div>
            <span className="muted">{activeDashboardProviders.length} active</span>
          </div>
          <div className="dashboard-provider-list stagger">
            {activeDashboardProviders.map((p) => {
              const provider = providers.find((x) => x.id === p.provider_id);
              const isRunning = !!runningProviders[p.provider_id];
              return (
                <article
                  key={p.provider_id}
                  className="provider-card dashboard-provider-card"
                  style={{ ["--status-color" as string]: statusColor(p.last_status) }}
                  onClick={() => nav(`/providers/${p.provider_id}`)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      nav(`/providers/${p.provider_id}`);
                    }
                  }}
                  role="button"
                  tabIndex={0}
                  aria-label={`${p.name}, status ${p.last_status ?? "unknown"}`}
                >
                  <div className="head">
                    <div>
                      <div className="name">
                        <span
                          className={`status-dot ${p.last_status ?? "unknown"}`}
                          title={`Status: ${p.last_status ?? "unknown"}`}
                          aria-label={`Status: ${p.last_status ?? "unknown"}`}
                        />
                        {p.name}
                        <span className={`pill ${p.last_status ?? "unknown"}`}>
                          {p.last_status ?? "unknown"}
                        </span>
                      </div>
                      <div className="meta">
                        <span className="kind-badge">
                          <KindIcon kind={p.kind} /> {p.kind}
                        </span>
                        <span style={{ marginLeft: 8 }}>
                          {p.model_count} models · {formatRelative(p.last_checked_at)}
                        </span>
                      </div>
                      <div className="dashboard-monitoring-line">
                        <span className="pill ok">Monitoring on</span>
                        <span className={`pill ${p.list_models_status ?? "unknown"}`}>
                          List {p.list_models_status ?? "unknown"}
                        </span>
                        <span className="muted">{statusIntervalLabel}</span>
                        {isRunning && <span className="pill info">Probe queued</span>}
                      </div>
                    </div>
                  </div>

                  <div className="dashboard-provider-body">
                    <div className="dashboard-provider-summary">
                      <div className="metrics">
                        <div className="metric">
                          <div className="label">24h Availability</div>
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
                          <div className="label">P95 Latency</div>
                          <div className="value">{formatMs(p.p95_latency_ms_24h)}</div>
                        </div>
                        <div className="metric">
                          <div className="label">P95 TTFB</div>
                          <div className="value">{formatMs(p.p95_ttfb_ms_24h)}</div>
                        </div>
                        <div className="metric">
                          <div className="label">Samples / Failures</div>
                          <div className="value">{p.samples_24h} / {p.failures_24h}</div>
                        </div>
                        <div className="metric dashboard-wide-metric">
                          <div className="label">Model List</div>
                          <div className="value">
                            {p.list_models_status ? (
                              <>
                                {p.list_models_status}
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
                          <div className="label">Available Models</div>
                          <div className="value">
                            {p.model_count > 0
                              ? `${p.available_models_online} / ${p.model_count}`
                              : "—"}
                          </div>
                        </div>
                        <div className="metric dashboard-wide-metric">
                          <div className="label">Favorite Models</div>
                          <div className="value">
                            {p.favorite_models_total > 0
                              ? `${p.favorite_models_online} / ${p.favorite_models_total}`
                              : "—"}
                          </div>
                        </div>
                        <div className="metric dashboard-wide-metric">
                          <div className="label">Errors 24h</div>
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
                            Favorite Models
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
                            No favorite models yet. Mark them on the Models or Provider Detail page.
                          </div>
                        )}
                      </div>
                    </div>
                  </div>

                  <div
                    className="actions"
                    onClick={(e) => e.stopPropagation()}
                    onKeyDown={(e) => e.stopPropagation()}
                  >
                    <button
                      className="secondary sm"
                      disabled={isRunning}
                      onClick={async () => {
                        setRunningProviders((prev) => ({ ...prev, [p.provider_id]: true }));
                        try {
                          await api.runNow(p.provider_id);
                          pushToast("info", "Probe queued", p.name);
                          window.setTimeout(() => {
                            void refresh().finally(() => {
                              setRunningProviders((prev) => {
                                const next = { ...prev };
                                delete next[p.provider_id];
                                return next;
                              });
                            });
                          }, 8_000);
                        } catch (e) {
                          setRunningProviders((prev) => {
                            const next = { ...prev };
                            delete next[p.provider_id];
                            return next;
                          });
                          pushToast("fail", "Trigger failed", e instanceof Error ? e.message : String(e));
                        }
                      }}
                      title="Probe current model availability, latency and error status"
                      aria-label="Probe status"
                    >
                      {isRunning ? <span className="spinner" /> : <IconPlay />}
                      {isRunning ? "Probing..." : "Probe status"}
                    </button>
                    <button
                      className="secondary sm"
                      onClick={async () => {
                        try {
                          await api.syncModels(p.provider_id);
                          pushToast("ok", "Model list updated", p.name);
                          await refresh();
                        } catch (e) {
                          pushToast("fail", "Update failed", e instanceof Error ? e.message : String(e));
                        }
                      }}
                      title="Re-fetch the list of models from the provider"
                      aria-label="Sync models"
                    >
                      <IconRefresh />
                      Sync models
                    </button>
                    {provider && (
                      <button
                        className="ghost sm"
                        onClick={() => onDelete(provider)}
                        aria-label="Delete"
                        title="Delete"
                      >
                        <IconDelete />
                      </button>
                    )}
                  </div>
                </article>
              );
            })}
            <button
              className="dashboard-add-provider-card"
              onClick={() => setShowAddProvider(true)}
              aria-label="Add Provider"
            >
              <span className="dashboard-add-provider-icon">
                <IconPlus />
              </span>
              <span className="dashboard-add-provider-copy">
                <strong>Add Provider</strong>
                <span>Connect a new LLM provider and configure monitoring frequency</span>
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
            <h3>No providers yet</h3>
            <p>Add an LLM provider on the Providers page to start monitoring.</p>
            <button
              onClick={() => setShowAddProvider(true)}
              style={{ marginTop: 12 }}
            >
              <IconPlus />
              Add Provider
            </button>
          </div>
        </div>
      )}
      {dashboard && dashboard.providers.length > 0 && activeDashboardProviders.length === 0 && !loading && (
        <div className="card fade-up">
          <div className="empty-state">
            <div className="empty-state-icon">
              <IconServer />
            </div>
            <h3>No monitoring providers</h3>
            <p>Toggle monitoring on the Providers page to see them here.</p>
            <button
              onClick={() => nav("/providers")}
              style={{ marginTop: 12 }}
            >
              <IconServer />
              Manage Providers
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
            title={`Status: ${label}`}
            aria-label={`Status: ${label}`}
          />
          <span className="favorite-model-name" title={model.model_id}>
            {displayName}
          </span>
          <span className={`pill ${statusClass}`}>{label}</span>
        </div>
        <div className="favorite-model-meta">
          <span className="favorite-model-type">{model.type}</span>
          <span>{formatRelative(model.last_checked_at)}</span>
          <span>confirmed {formatRelative(model.status_confirmed_at)}</span>
          {model.last_success_at && <span>last ok {formatRelative(model.last_success_at)}</span>}
          <span>{model.samples_24h} samples</span>
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
            pushToast("info", "Model probe triggered", `${providerName} / ${displayName}`);
            await onRefresh();
          } catch (e) {
            pushToast("fail", "Trigger failed", e instanceof Error ? e.message : String(e));
          }
        }}
        aria-label={`Probe ${displayName} now`}
        title="Probe now"
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
  const clickable = !!to;
  // The card's a11y name includes the headline number (the most important
  // data on the dashboard) plus the navigation target. Format:
  //   "Providers 3, view details" / "Favorites online 8 of 12, view"
  const friendlyValue =
    typeof value === "number" ? value.toString() : String(value);
  const ariaLabel = clickable
    ? `${label} ${friendlyValue}${hint ? `, ${hint}` : ""}${sub ? `, ${sub}` : ""}, view details`
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
