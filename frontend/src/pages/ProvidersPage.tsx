import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, Provider, ProviderKind } from "../api/types";
import { useDashboard } from "../hooks/useDashboard";
import { formatMs, formatRelative, statusColor } from "../lib/format";
import { modelStatusIntervalLabel } from "../lib/settings";
import { Icon } from "../components/Icons";
import { withErrorToast } from "../lib/action";
import { pushToast } from "../components/Toast";

type StatusFilter = "all" | "ok" | "fail";
type FavoriteFilter = "all" | "favorites";

function errorSummary(counts: Record<string, number>): string {
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) return "No errors";
  return entries.map(([code, count]) => `${code} ${count}`).join(" · ");
}

export function ProvidersPage() {
  const nav = useNavigate();
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
    // Functional updater: never lose a same-tick update to a stale closure.
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (s === "all") next.delete("status");
        else next.set("status", s);
        return next;
      },
      // No replace: keep history so browser-back returns to the previous
      // filter (matches native <select> and tab UX).
    );
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
      await withErrorToast(api.patchProvider(p.id, { enabled: nextEnabled }), "Toggle monitoring");
      pushToast("ok", nextEnabled ? "Monitoring enabled" : "Monitoring paused", p.name);
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
    () => Object.fromEntries(
      (dashboard?.providers ?? []).map((p) => [p.provider_id, p.last_status]),
    ),
    // Depend on the providers array reference, not the whole `dashboard`
    // object — the dashboard is a new ref on every refresh (new {}), which
    // would invalidate this memo unnecessarily on every SSE tick.
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
          <h1>Providers</h1>
          <div className="subtitle">Manage LLM providers and probe configuration</div>
        </div>
        <div className="toolbar" style={{ margin: 0 }}>
          <button onClick={() => setShowAdd(true)}>
            <Icon.Plus />
            Add Provider
          </button>
        </div>
      </div>

      <div className="toolbar" style={{ marginBottom: 16 }}>
        <span className="muted" style={{ fontSize: 12 }}>
          Filter:
        </span>
        <div
          className="window-tabs"
          role="toolbar"
          aria-label="Filter by status"
        >
          {(
            [
              { v: "all", label: "All" },
              { v: "ok", label: "OK" },
              { v: "fail", label: "Failing" },
            ] as { v: StatusFilter; label: string }[]
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
        <div
          className="window-tabs"
          role="toolbar"
          aria-label="Filter by favorites"
        >
          {(
            [
              { v: "all", label: "All models" },
              { v: "favorites", label: "★ Favorites" },
            ] as { v: FavoriteFilter; label: string }[]
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
          Showing {filtered.length} / {totalUnfiltered}
        </span>
        {hasFilter && (
          <button className="ghost sm" onClick={() => setParams(new URLSearchParams(), { replace: true })}>
            <Icon.Close />
            Clear filter
          </button>
        )}
      </div>

      {providers.length === 0 ? (
        <div className="card">
          <div className="empty-state">
            <div className="empty-state-icon">
              <Icon.Server />
            </div>
            <h3>No providers yet</h3>
            <p>Add your first LLM provider to start monitoring.</p>
            <button onClick={() => setShowAdd(true)} style={{ marginTop: 12 }}>
              <Icon.Plus />
              Add Provider
            </button>
          </div>
        </div>
      ) : filtered.length === 0 ? (
        <div className="card">
          <div className="empty-state">
            <div className="empty-state-icon">
              <Icon.Filter />
            </div>
            <h3>No matching providers</h3>
            <p>Try adjusting or clearing your filters.</p>
            <button
              className="secondary"
              onClick={() => setParams(new URLSearchParams(), { replace: true })}
              style={{ marginTop: 12 }}
            >
              Clear filter
            </button>
          </div>
        </div>
      ) : (
        <div
          className="provider-grid stagger"
          role="list"
          aria-label="Providers"
        >
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
                aria-label={`${p.name}, status ${status ?? "unknown"}`}
              >
                <div className="head">
                  <div>
                    <div className="name">
                      <span
                        className={`status-dot ${status ?? "unknown"}`}
                        title={`Status: ${status ?? "unknown"}`}
                        aria-label={`Status: ${status ?? "unknown"}`}
                      />
                      {p.name}
                      <span className={`pill ${status ?? "unknown"}`}>
                        {status ?? "unknown"}
                      </span>
                    </div>
                    <div className="meta">
                      <span className="kind-badge">{p.kind}</span>
                      <span style={{ marginLeft: 8 }}>
                        {models.length} models · list every {p.interval_seconds}s
                      </span>
                    </div>
                    <div className="dashboard-monitoring-line">
                      <label
                        className={`monitoring-switch ${isToggling ? "busy" : ""}`}
                        onClick={(e) => e.stopPropagation()}
                        onKeyDown={(e) => e.stopPropagation()}
                        title={p.enabled ? "Pause auto-monitoring" : "Start auto-monitoring"}
                      >
                        <input
                          type="checkbox"
                          checked={p.enabled}
                          disabled={isToggling}
                          onChange={() => void toggleMonitoring(p)}
                          aria-label={`${p.enabled ? "Pause" : "Start"} auto-monitoring for ${p.name}`}
                        />
                        <span className="monitoring-switch-track" />
                        <span className="monitoring-switch-text">
                          {isToggling
                            ? "Updating..."
                            : `Monitoring ${p.enabled ? "on" : "off"}`}
                        </span>
                      </label>
                      <span className="muted">Status probe: {statusIntervalLabel}</span>
                      {isRunning && <span className="pill info">Probe queued</span>}
                      {isSyncing && <span className="pill info">Syncing model list</span>}
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
                      <div className="label">24h Availability</div>
                      <div className="value">
                        {dash.availability_24h != null
                          ? `${dash.availability_24h.toFixed(1)}%`
                          : "—"}
                      </div>
                    </div>
                    <div className="metric">
                      <div className="label">P95 Latency</div>
                      <div className="value">{formatMs(dash.p95_latency_ms_24h)}</div>
                    </div>
                    <div className="metric">
                      <div className="label">Samples / Failures</div>
                      <div className="value">
                        {dash.samples_24h} / {dash.failures_24h}
                      </div>
                    </div>
                    <div className="metric">
                      <div className="label">Model List</div>
                      <div className="value" style={{ fontSize: 14 }}>
                        {dash.list_models_status ?? "—"}
                        <span className="metric-subvalue">
                          {formatMs(dash.list_models_latency_ms)} · {formatRelative(dash.list_models_checked_at)}
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
                    title="Edit"
                    aria-label={`Edit ${p.name}`}
                  >
                    <Icon.Edit />
                  </button>
                  <button
                    className="secondary sm"
                    disabled={isRunning || !p.enabled}
                    title={p.enabled ? "Probe current model availability, latency and error status" : "Enable monitoring before probing status"}
                    onClick={async () => {
                      setRunningProviders((prev) => ({ ...prev, [p.id]: true }));
                      try {
                        await withErrorToast(api.runNow(p.id), "Probe status");
                        pushToast("info", "Probe queued", p.name);
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
                    aria-label={`Probe ${p.name} status`}
                  >
                    {isRunning ? <span className="spinner" /> : <Icon.Run />}
                    {isRunning ? "Probing..." : "Probe status"}
                  </button>
                  <button
                    className="secondary sm"
                    disabled={isSyncing}
                    title="Re-fetch the list of models from the provider"
                    onClick={async () => {
                      setSyncingProviders((prev) => ({ ...prev, [p.id]: true }));
                      try {
                        await withErrorToast(api.syncModels(p.id), "Sync models");
                        pushToast("ok", "Model list updated", p.name);
                        await refresh();
                      } finally {
                        setSyncingProviders((prev) => {
                          const next = { ...prev };
                          delete next[p.id];
                          return next;
                        });
                      }
                    }}
                    aria-label={`Sync models for ${p.name}`}
                  >
                    {isSyncing ? <span className="spinner" /> : <Icon.Sync />}
                    {isSyncing ? "Syncing..." : "Sync models"}
                  </button>
                  <button
                    className="ghost sm"
                    onClick={async () => {
                      if (!confirm(`Delete ${p.name}?`)) return;
                      try {
                        await withErrorToast(api.deleteProvider(p.id), "Delete");
                        pushToast("ok", "Deleted", p.name);
                        await refresh();
                      } catch {
                        /* toast already shown */
                      }
                    }}
                    title="Delete"
                    aria-label={`Delete ${p.name}`}
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
  const isEdit = !!provider;
  const [name, setName] = useState(provider?.name ?? "");
  const [kind, setKind] = useState<ProviderKind>(provider?.kind ?? "openai");
  const [baseUrl, setBaseUrl] = useState(provider?.base_url ?? "https://api.openai.com");
  const [apiKey, setApiKey] = useState("");
  const [proxy, setProxy] = useState(provider?.proxy ?? "");
  const [intervalSec, setIntervalSec] = useState(provider?.interval_seconds ?? 300);
  const [timeoutSec, setTimeoutSec] = useState(provider?.timeout_seconds ?? 30);
  const [headersJson, setHeadersJson] = useState(provider?.headers_json ?? "");
  const [enabled, setEnabled] = useState(provider?.enabled ?? true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Close on Escape; focus the first input on mount. Run-once: empty deps
  // so SSE-driven re-renders of the parent don't re-fire the focus, which
  // would yank the user's caret out of whatever field they're editing.
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
          base_url: baseUrl,
          proxy: proxy || null,
          interval_seconds: intervalSec,
          timeout_seconds: timeoutSec,
          headers_json: headersJson || null,
          enabled,
        };
        if (apiKey) body.api_key = apiKey;
        await withErrorToast(api.patchProvider(provider.id, body), isEdit ? "Save" : "Create");
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
          "Create",
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
          <h3 id="provider-dialog-title">{isEdit ? `Edit ${provider!.name}` : "Add Provider"}</h3>
          <button className="modal-close" onClick={onClose} aria-label="Close">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        <div className="form-grid">
          <div className="form-row full">
            <label>Name</label>
            <input
              value={name}
              disabled={isEdit}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. OpenAI Production"
            />
          </div>
          <div className="form-row">
            <label>Kind</label>
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
            <label>Base URL</label>
            <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
          </div>
          <div className="form-row full">
            <label>{isEdit ? "API Key (leave blank to keep)" : "API Key"}</label>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="sk-…"
            />
          </div>
          <div className="form-row full">
            <label>Proxy (optional)</label>
            <input
              value={proxy}
              onChange={(e) => setProxy(e.target.value)}
              placeholder="http://127.0.0.1:7890"
            />
          </div>
          <div className="form-row">
            <label>Model list interval (s)</label>
            <input
              type="number"
              value={intervalSec}
              min={10}
              max={86400}
              onChange={(e) => setIntervalSec(Number(e.target.value))}
            />
          </div>
          <div className="form-row">
            <label>Timeout (s)</label>
            <input
              type="number"
              value={timeoutSec}
              min={2}
              max={600}
              onChange={(e) => setTimeoutSec(Number(e.target.value))}
            />
          </div>
          <div className="form-row full">
            <label>Custom Headers (JSON, optional)</label>
            <textarea
              rows={2}
              value={headersJson}
              onChange={(e) => setHeadersJson(e.target.value)}
              placeholder='{"X-Org": "acme"}'
            />
          </div>
          <div className="form-row full">
            <label>Monitoring</label>
            <label className="monitoring-switch provider-dialog-switch">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
              />
              <span className="monitoring-switch-track" />
              <span className="monitoring-switch-text">
                {enabled ? "Auto-monitoring on" : "Auto-monitoring off"}
              </span>
            </label>
          </div>
        </div>

        {err && <div className="error">{err}</div>}

        <div className="modal-footer">
          <button className="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button onClick={submit} disabled={busy || (!isEdit && (!name || !apiKey))}>
            {busy ? (
              <>
                <span className="spinner" /> Saving...
              </>
            ) : (
              "Save"
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
