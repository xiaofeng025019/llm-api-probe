import { useNavigate, Link } from "react-router-dom";
import { useDashboard } from "../hooks/useDashboard";
import { formatPercent, formatRelative, statusColor } from "../lib/format";
import { api, type Provider } from "../api/types";
import { withErrorToast } from "../lib/action";
import {
  IconPlay,
  IconRefresh,
  IconDelete,
  IconServer,
  IconCheck,
  IconStarOutline,
  KindIcon,
} from "../components/Icons";
import { CountUp } from "../components/CountUp";
import { Skeleton, SkeletonProviderCard, SkeletonStat } from "../components/Skeleton";
import { pushToast } from "../components/Toast";

export function DashboardPage() {
  const nav = useNavigate();
  const { dashboard, providers, loading, error, refresh } = useDashboard();

  async function onDelete(p: Provider) {
    if (!confirm(`删除 provider ${p.name}?`)) return;
    try {
      await withErrorToast(api.deleteProvider(p.id), "删除");
      pushToast("ok", "已删除", p.name);
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
            <h1>总览</h1>
            <div className="subtitle">所有 LLM 服务商的实时可用性</div>
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
          <h1>总览</h1>
          <div className="subtitle">所有 LLM 服务商的实时可用性</div>
        </div>
      </div>

      {error && <div className="error" role="alert">{error}</div>}

      {dashboard && (
        <div className="stat-grid stagger">
          <StatCard
            label="Providers"
            value={dashboard.totals.providers}
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
                ? `${Math.round(((dashboard.totals.available_models ?? 0) / dashboard.totals.models) * 100)}% 24h 在线`
                : "无 model"
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
            to="/models"
            sub={
              dashboard.totals.favorites_total === 0
                ? "未收藏任何 model"
                : `${Math.round((dashboard.totals.favorites_online / Math.max(1, dashboard.totals.favorites_total)) * 100)}% 在线`
            }
            progress={
              dashboard.totals.favorites_total > 0
                ? (dashboard.totals.favorites_online / dashboard.totals.favorites_total) * 100
                : null
            }
          />
        </div>
      )}

      {dashboard && dashboard.providers.length > 0 && (
        <div className="section">
          <div className="section-header">
            <div className="section-title">
              <IconServer />
              Providers
            </div>
            <span className="muted">{dashboard.providers.length} 个</span>
          </div>
          <div className="provider-grid stagger">
            {dashboard.providers.map((p) => {
              const provider = providers.find((x) => x.id === p.provider_id);
              return (
                <article
                  key={p.provider_id}
                  className="provider-card"
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
                        {!p.enabled && (
                          <span className="pill" style={{ marginLeft: 6 }}>
                            disabled
                          </span>
                        )}
                      </div>
                      <div className="meta">
                        <span className="kind-badge">
                          <KindIcon kind={p.kind} /> {p.kind}
                        </span>
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
                        {p.avg_latency_ms_24h != null ? (
                          <>
                            <CountUp value={p.avg_latency_ms_24h} />
                            <span style={{ fontSize: 13, marginLeft: 2 }}>ms</span>
                          </>
                        ) : (
                          "—"
                        )}
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
                        {provider ? (
                          <>
                            <CountUp value={provider.interval_seconds} />
                            <span style={{ fontSize: 13, marginLeft: 2 }}>s</span>
                          </>
                        ) : (
                          "—"
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
                      onClick={async () => {
                        try {
                          await api.runNow(p.provider_id);
                          pushToast("info", "已触发探测", p.name);
                        } catch (e) {
                          pushToast("fail", "触发失败", e instanceof Error ? e.message : String(e));
                        }
                      }}
                      aria-label="立即探测"
                    >
                      <IconPlay />
                      Run
                    </button>
                    <button
                      className="secondary sm"
                      onClick={async () => {
                        try {
                          await api.syncModels(p.provider_id);
                          pushToast("ok", "已同步", p.name);
                          await refresh();
                        } catch (e) {
                          pushToast("fail", "同步失败", e instanceof Error ? e.message : String(e));
                        }
                      }}
                      aria-label="同步模型"
                    >
                      <IconRefresh />
                      Sync
                    </button>
                    {provider && (
                      <button
                        className="ghost sm"
                        onClick={() => onDelete(provider)}
                        aria-label="删除"
                        title="删除"
                      >
                        <IconDelete />
                      </button>
                    )}
                  </div>
                </article>
              );
            })}
          </div>
        </div>
      )}

      {dashboard && dashboard.providers.length === 0 && !loading && (
        <div className="card fade-up">
          <div className="empty-state">
            <div className="empty-state-icon">
              <IconServer />
            </div>
            <h3>还没有 provider</h3>
            <p>在 Providers 页面添加一个 LLM 服务商开始监测。</p>
            <button
              onClick={() => nav("/providers")}
              style={{ marginTop: 12 }}
            >
              添加 Provider
            </button>
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
  //   "Providers 3, 查看详情" / "Favorites online 8 of 12, 查看"
  const friendlyValue =
    typeof value === "number" ? value.toString() : String(value);
  const ariaLabel = clickable
    ? `${label} ${friendlyValue}${hint ? `, ${hint}` : ""}${sub ? `, ${sub}` : ""}, 查看详情`
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