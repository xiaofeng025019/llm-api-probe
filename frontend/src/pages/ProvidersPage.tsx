import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, Provider, ProviderKind } from "../api/types";
import { useDashboard } from "../hooks/useDashboard";
import { formatMs, formatRelative, statusColor } from "../lib/format";
import { modelStatusIntervalLabel } from "../lib/settings";
import { Icon, IconEye, IconEyeOff } from "../components/Icons";
import { withErrorToast } from "../lib/action";
import { pushToast } from "../components/Toast";
import { useT } from "../hooks/useT";
import { t as i18nT } from "../lib/i18n";

type StatusFilter = "all" | "ok" | "fail";
type FavoriteFilter = "all" | "favorites";

function errorSummary(counts: Record<string, number>): string {
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) return i18nT("format.noErrors");
  return entries.map(([code, count]) => `${code} ${count}`).join(" · ");
}

export function ProvidersPage() {
  const nav = useNavigate();
  const t = useT();
  const { providers, dashboard, modelsByProvider, settings, refresh } = useDashboard();
  const [showAdd, setShowAdd] = useState(false);
  const [editing, setEditing] = useState<Provider | null>(null);
  const [runningProviders, setRunningProviders] = useState<Record<string, boolean>>({});
  const [syncingProviders, setSyncingProviders] = useState<Record<string, boolean>>({});
  const [togglingProviders, setTogglingProviders] = useState<Record<string, boolean>>({});
  const [params, setParams] = useSearchParams();

  // URL-driven filter state: ?status=ok|fail&favorites=1
  // Use a whitelist — anything else (typo'd URL, old bookmark) is treated
  // as "all" so the UI is always recoverable and the active tab is honest.
  const rawStatus = params.get("status");
  const statusFilter: StatusFilter =
    rawStatus === "ok" || rawStatus === "fail" ? rawStatus : "all";
  // favorites is treated as a boolean flag: any truthy string = on.
  // (Accepts '1', 'true', 'on' for friendlier hand-edited URLs.)
  const rawFav = params.get("favorites");
  const favFilter: FavoriteFilter =
    rawFav !== null && rawFav !== "" && rawFav !== "0" && rawFav !== "false"
      ? "favorites"
      : "all";

  function setStatus(s: StatusFilter) {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (s === "all") next.delete("status");
      else next.set("status", s);
      return next;
    });
  }
  function setFav(f: FavoriteFilter) {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (f === "all") next.delete("favorites");
      else next.set("favorites", "1");
      return next;
    });
  }

  async function toggleMonitoring(p: Provider) {
    const nextEnabled = !p.enabled;
    setTogglingProviders((prev) => ({ ...prev, [p.id]: true }));
    try {
      await withErrorToast(
        api.patchProvider(p.id, { enabled: nextEnabled }),
        nextEnabled ? t("providers.card.monitorTitleOff") : t("providers.card.monitorTitleOn"),
      );
      pushToast(
        "ok",
        nextEnabled ? t("toast.monitoringOn") : t("toast.monitoringOff"),
        p.name,
      );
      await refresh();
    } catch {
      /* toast already shown */
    } finally {
      setTogglingProviders((prev) => {
        const next = { ...prev };
        delete next[p.id];
        return next;
      });
    }
  }

  const lastStatusById = useMemo(
    () =>
      Object.fromEntries(
        (dashboard?.providers ?? []).map((p) => [p.provider_id, p.last_status]),
      ),
    [dashboard?.providers],
  );

  const filtered = useMemo(() => {
    return providers.filter((p) => {
      if (statusFilter !== "all") {
        const st = lastStatusById[p.id];
        if (statusFilter === "ok" && st !== "ok") return false;
        if (statusFilter === "fail" && st !== "fail" && st !== "degraded") return false;
      }
      if (favFilter === "favorites") {
        const ms = modelsByProvider[p.id] ?? [];
        if (!ms.some((m) => m.is_favorite)) return false;
      }
      return true;
    });
  }, [providers, statusFilter, favFilter, lastStatusById, modelsByProvider]);

  const totalUnfiltered = providers.length;
  const hasFilter = statusFilter !== "all" || favFilter !== "all";
  const statusIntervalLabel = modelStatusIntervalLabel(settings);

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>{t("providers.title")}</h1>
          <div className="subtitle">{t("providers.subtitle")}</div>
        </div>
        <div className="toolbar" style={{ margin: 0 }}>
          <button onClick={() => setShowAdd(true)}>
            <Icon.Plus />
            {t("providers.addProvider")}
          </button>
        </div>
      </div>

      <div className="toolbar" style={{ marginBottom: 16 }}>
        <span className="muted" style={{ fontSize: 12 }}>
          {t("providers.filter")}
        </span>
        <div className="window-tabs" role="toolbar" aria-label="Filter by status">
          {(
            [
              { v: "all" as const, label: t("providers.statusAll") },
              { v: "ok" as const, label: t("providers.statusOk") },
              { v: "fail" as const, label: t("providers.statusFail") },
            ]
          ).map((opt) => (
            <button
              key={opt.v}
              type="button"
              aria-pressed={statusFilter === opt.v}
              className={statusFilter === opt.v ? "active" : ""}
              onClick={() => setStatus(opt.v)}
            >
              {opt.label}
            </button>
          ))}
        </div>
        <div className="window-tabs" role="toolbar" aria-label="Filter by favorites">
          {(
            [
              { v: "all" as const, label: t("providers.favAll") },
              { v: "favorites" as const, label: t("providers.favOnly") },
            ]
          ).map((opt) => (
            <button
              key={opt.v}
              type="button"
              aria-pressed={favFilter === opt.v}
              className={favFilter === opt.v ? "active" : ""}
              onClick={() => setFav(opt.v)}
            >
              {opt.label}
            </button>
          ))}
        </div>
        <span className="grow" />
        <span className="muted" style={{ fontSize: 12 }}>
          {t("providers.showing", { shown: filtered.length, total: totalUnfiltered })}
        </span>
        {hasFilter && (
          <button
            className="ghost sm"
            onClick={() => setParams(new URLSearchParams(), { replace: true })}
          >
            <Icon.Close />
            {t("providers.clearFilter")}
          </button>
        )}
      </div>

      {providers.length === 0 ? (
        <div className="card">
          <div className="empty-state">
            <div className="empty-state-icon">
              <Icon.Server />
            </div>
            <h3>{t("providers.emptyTitle")}</h3>
            <p>{t("providers.emptyDesc")}</p>
            <button onClick={() => setShowAdd(true)} style={{ marginTop: 12 }}>
              <Icon.Plus />
              {t("providers.addProvider")}
            </button>
          </div>
        </div>
      ) : filtered.length === 0 ? (
        <div className="card">
          <div className="empty-state">
            <div className="empty-state-icon">
              <Icon.Filter />
            </div>
            <h3>{t("providers.emptyFilteredTitle")}</h3>
            <p>{t("providers.emptyFilteredDesc")}</p>
            <button
              className="secondary"
              onClick={() => setParams(new URLSearchParams(), { replace: true })}
              style={{ marginTop: 12 }}
            >
              {t("providers.clearFilter")}
            </button>
          </div>
        </div>
      ) : (
        <div className="provider-grid stagger" role="list" aria-label={t("providers.title")}>
          {filtered.map((p) => {
            const dash = dashboard?.providers.find((d) => d.provider_id === p.id);
            const models = modelsByProvider[p.id] ?? [];
            const status = lastStatusById[p.id];
            const isRunning = !!runningProviders[p.id];
            const isSyncing = !!syncingProviders[p.id];
            const isToggling = !!togglingProviders[p.id];
            return (
              <article
                key={p.id}
                className="provider-card"
                style={{ ["--status-color" as string]: statusColor(status) }}
                onClick={() => nav(`/providers/${p.id}`)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    nav(`/providers/${p.id}`);
                  }
                }}
                role="button"
                tabIndex={0}
                aria-label={`${p.name}, ${t("dashboard.card.modelList")} ${status ?? t("common.unknown")}`}
              >
                <div className="head">
                  <div>
                    <div className="name">
                      <span
                        className={`status-dot ${status ?? "unknown"}`}
                        title={t("dashboard.card.modelList") + ": " + (status ?? t("common.unknown"))}
                        aria-label={t("dashboard.card.modelList") + ": " + (status ?? t("common.unknown"))}
                      />
                      {p.name}
                      <span className={`pill ${status ?? "unknown"}`}>
                        {status ?? t("common.unknown")}
                      </span>
                    </div>
                    <div className="meta">
                      <span className="kind-badge">{p.kind}</span>
                      <span style={{ marginLeft: 8 }}>
                        {models.length} {t("dashboard.card.modelList")} ·{" "}
                        {t("providers.card.listEvery", { n: p.interval_seconds })}
                      </span>
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
                      <span className="muted">
                        {t("providers.card.statusProbe", { n: statusIntervalLabel })}
                      </span>
                      {isRunning && <span className="pill info">{t("dashboard.card.probeQueued")}</span>}
                      {isSyncing && <span className="pill info">{t("dashboard.card.syncingList")}</span>}
                    </div>
                  </div>
                </div>
                <div
                  className="muted mono"
                  style={{ fontSize: 11, marginTop: 8, wordBreak: "break-all" }}
                >
                  {p.base_url}
                </div>

                {dash && (
                  <div className="metrics">
                    <div className="metric">
                      <div className="label">{t("dashboard.card.availability24h")}</div>
                      <div className="value">
                        {dash.availability_24h != null
                          ? `${dash.availability_24h.toFixed(1)}%`
                          : "—"}
                      </div>
                    </div>
                    <div className="metric">
                      <div className="label">{t("dashboard.card.p95Latency")}</div>
                      <div className="value">{formatMs(dash.p95_latency_ms_24h)}</div>
                    </div>
                    <div className="metric">
                      <div className="label">{t("dashboard.card.samplesFailures")}</div>
                      <div className="value">
                        {dash.samples_24h} / {dash.failures_24h}
                      </div>
                    </div>
                    <div className="metric">
                      <div className="label">{t("dashboard.card.modelList")}</div>
                      <div className="value" style={{ fontSize: 14 }}>
                        {dash.list_models_status ?? "—"}
                        <span className="metric-subvalue">
                          {formatMs(dash.list_models_latency_ms)} ·{" "}
                          {formatRelative(dash.list_models_checked_at)}
                        </span>
                      </div>
                    </div>
                    <div className="metric provider-error-metric">
                      <div className="label">Errors 24h</div>
                      <div className="value metric-compact-value">
                        {errorSummary(dash.error_counts_24h)}
                      </div>
                    </div>
                  </div>
                )}

                <div
                  className="actions"
                  onClick={(e) => e.stopPropagation()}
                  onKeyDown={(e) => {
                    if (e.key === " " || e.key === "Enter") e.stopPropagation();
                  }}
                >
                  <button
                    className="ghost sm"
                    onClick={() => setEditing(p)}
                    title={t("providers.card.edit")}
                    aria-label={`${t("providers.card.edit")} ${p.name}`}
                  >
                    <Icon.Edit />
                  </button>
                  <button
                    className="secondary sm"
                    disabled={isRunning || !p.enabled}
                    title={
                      p.enabled
                        ? t("providers.card.probeTitle")
                        : t("providers.card.probeTitleDisabled")
                    }
                    onClick={async () => {
                      setRunningProviders((prev) => ({ ...prev, [p.id]: true }));
                      try {
                        await withErrorToast(api.runNow(p.id), t("providers.card.probeStatus"));
                        pushToast("info", t("toast.probeQueued"), p.name);
                        window.setTimeout(() => {
                          void refresh().finally(() => {
                            setRunningProviders((prev) => {
                              const next = { ...prev };
                              delete next[p.id];
                              return next;
                            });
                          });
                        }, 8_000);
                      } catch {
                        setRunningProviders((prev) => {
                          const next = { ...prev };
                          delete next[p.id];
                          return next;
                        });
                      }
                    }}
                    aria-label={t("providers.card.probeAria", { name: p.name })}
                  >
                    {isRunning ? <span className="spinner" /> : <Icon.Run />}
                    {isRunning ? t("providers.card.probeBusy") : t("providers.card.probeStatus")}
                  </button>
                  <button
                    className="secondary sm"
                    disabled={isSyncing}
                    title={t("providers.card.syncTitle")}
                    onClick={async () => {
                      setSyncingProviders((prev) => ({ ...prev, [p.id]: true }));
                      try {
                        await withErrorToast(api.syncModels(p.id), t("providers.card.syncAction"));
                        pushToast("ok", t("toast.modelListUpdated"), p.name);
                        await refresh();
                      } finally {
                        setSyncingProviders((prev) => {
                          const next = { ...prev };
                          delete next[p.id];
                          return next;
                        });
                      }
                    }}
                    aria-label={t("providers.card.syncAria", { name: p.name })}
                  >
                    {isSyncing ? <span className="spinner" /> : <Icon.Sync />}
                    {isSyncing ? t("providers.card.syncBusy") : t("providers.card.syncAction")}
                  </button>
                  <button
                    className="ghost sm"
                    onClick={async () => {
                      if (!confirm(t("providers.card.deleteConfirm", { name: p.name }))) return;
                      try {
                        await withErrorToast(api.deleteProvider(p.id), t("common.delete"));
                        pushToast("ok", t("toast.deleted"), p.name);
                        await refresh();
                      } catch {
                        /* toast already shown */
                      }
                    }}
                    title={t("common.delete")}
                    aria-label={t("providers.card.deleteAria", { name: p.name })}
                  >
                    <Icon.Delete />
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      )}

      {showAdd && <ProviderDialog onClose={() => setShowAdd(false)} onSaved={refresh} />}
      {editing && (
        <ProviderDialog
          provider={editing}
          onClose={() => setEditing(null)}
          onSaved={refresh}
        />
      )}
    </div>
  );
}

export function ProviderDialog({
  provider,
  onClose,
  onSaved,
}: {
  provider?: Provider;
  onClose: () => void;
  onSaved: () => Promise<void> | void;
}) {
  const t = useT();
  const isEdit = !!provider;
  const [name, setName] = useState(provider?.name ?? "");
  const [kind, setKind] = useState<ProviderKind>(provider?.kind ?? "openai");
  const [baseUrl, setBaseUrl] = useState(provider?.base_url ?? "https://api.openai.com");
  const [apiKey, setApiKey] = useState(provider?.api_key ?? "");
  const [showKey, setShowKey] = useState(false);
  const [proxy, setProxy] = useState(provider?.proxy ?? "");
  const [intervalSec, setIntervalSec] = useState(provider?.interval_seconds ?? 300);
  const [timeoutSec, setTimeoutSec] = useState(provider?.timeout_seconds ?? 30);
  const [headersJson, setHeadersJson] = useState(provider?.headers_json ?? "");
  const [enabled, setEnabled] = useState(provider?.enabled ?? true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const modalRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    const first = modalRef.current?.querySelector<HTMLElement>(
      "input, select, textarea, button",
    );
    first?.focus();
    return () => document.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function submit() {
    setBusy(true);
    setErr(null);
    try {
      if (isEdit && provider) {
        const body: Record<string, unknown> = {
          name,
          base_url: baseUrl,
          proxy: proxy || null,
          interval_seconds: intervalSec,
          timeout_seconds: timeoutSec,
          headers_json: headersJson || null,
          enabled,
        };
        if (apiKey) body.api_key = apiKey;
        await withErrorToast(api.patchProvider(provider.id, body), t("common.save"));
      } else {
        await withErrorToast(
          api.createProvider({
            name,
            kind,
            base_url: baseUrl,
            api_key: apiKey,
            proxy: proxy || null,
            interval_seconds: intervalSec,
            timeout_seconds: timeoutSec,
            headers_json: headersJson || null,
            enabled,
          }),
          t("common.add"),
        );
      }
      await onSaved();
      onClose();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-bg" onClick={onClose}>
      <div
        className="modal"
        ref={modalRef}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="provider-dialog-title"
      >
        <div className="modal-header">
          <h3 id="provider-dialog-title">
            {isEdit
              ? t("providers.dialog.editTitle", { name: provider!.name })
              : t("providers.dialog.addTitle")}
          </h3>
          <button className="modal-close" onClick={onClose} aria-label={t("common.close")}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        <div className="form-grid">
          <div className="form-row full">
            <label>{t("providers.dialog.labelName")}</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t("providers.dialog.namePlaceholder")}
            />
          </div>
          <div className="form-row">
            <label>{t("providers.dialog.labelKind")}</label>
            <select
              value={kind}
              disabled={isEdit}
              onChange={(e) => setKind(e.target.value as ProviderKind)}
            >
              <option value="openai">openai</option>
              <option value="openai_compat">openai_compat</option>
              <option value="anthropic">anthropic</option>
              <option value="gemini">gemini</option>
            </select>
          </div>
          <div className="form-row">
            <label>{t("providers.dialog.labelBaseUrl")}</label>
            <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
          </div>
          <div className="form-row full">
            <label>
              {isEdit
                ? t("providers.dialog.apiKeyEditHint")
                : t("providers.dialog.labelApiKey")}
            </label>
            <div className="input-with-icon">
              <input
                type={showKey ? "text" : "password"}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="sk-…"
              />
              <button
                type="button"
                className="input-icon-btn"
                title={showKey ? t("common.hide") : t("common.show")}
                onClick={() => setShowKey((s) => !s)}
                aria-label={showKey ? t("common.hide") : t("common.show")}
              >
                {showKey ? <IconEyeOff size={14} /> : <IconEye size={14} />}
              </button>
            </div>
          </div>
          <div className="form-row full">
            <label>{t("providers.dialog.labelProxy")}</label>
            <input
              value={proxy}
              onChange={(e) => setProxy(e.target.value)}
              placeholder={t("providers.dialog.proxyPlaceholder")}
            />
          </div>
          <div className="form-row">
            <label>{t("providers.dialog.labelModelInterval")}</label>
            <input
              type="number"
              value={intervalSec}
              min={10}
              max={86400}
              onChange={(e) => setIntervalSec(Number(e.target.value))}
            />
          </div>
          <div className="form-row">
            <label>{t("providers.dialog.labelTimeout")}</label>
            <input
              type="number"
              value={timeoutSec}
              min={2}
              max={600}
              onChange={(e) => setTimeoutSec(Number(e.target.value))}
            />
          </div>
          <div className="form-row full">
            <label>{t("providers.dialog.labelCustomHeaders")}</label>
            <textarea
              rows={2}
              value={headersJson}
              onChange={(e) => setHeadersJson(e.target.value)}
              placeholder={t("providers.dialog.customHeadersPlaceholder")}
            />
          </div>
          <div className="form-row full">
            <label>{t("providers.dialog.labelMonitoring")}</label>
            <label className="monitoring-switch provider-dialog-switch">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
              />
              <span className="monitoring-switch-track" />
              <span className="monitoring-switch-text">
                {enabled
                  ? t("providers.dialog.monitorOn")
                  : t("providers.dialog.monitorOff")}
              </span>
            </label>
          </div>
        </div>

        {err && <div className="error">{err}</div>}

        <div className="modal-footer">
          <button className="secondary" onClick={onClose} disabled={busy}>
            {t("common.cancel")}
          </button>
          <button onClick={submit} disabled={busy || (!isEdit && (!name || !apiKey))}>
            {busy ? (
              <>
                <span className="spinner" /> {t("common.saving")}
              </>
            ) : (
              t("common.save")
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
