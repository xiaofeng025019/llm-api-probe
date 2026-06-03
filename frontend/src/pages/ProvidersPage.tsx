import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, Provider, ProviderKind } from "../api/types";
import { useDashboard } from "../hooks/useDashboard";
import { formatRelative, statusColor } from "../lib/format";
import { Icon } from "../components/Icons";

export function ProvidersPage() {
  const nav = useNavigate();
  const { providers, dashboard, modelsByProvider, refresh } = useDashboard();
  const [showAdd, setShowAdd] = useState(false);
  const [editing, setEditing] = useState<Provider | null>(null);

  const lastStatusById = Object.fromEntries(
    (dashboard?.providers ?? []).map((p) => [p.provider_id, p.last_status]),
  );

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Providers</h1>
          <div className="subtitle">管理 LLM 服务商和检测配置</div>
        </div>
        <div className="toolbar" style={{ margin: 0 }}>
          <button onClick={() => setShowAdd(true)}>
            <Icon.Plus />
            添加 Provider
          </button>
        </div>
      </div>

      {providers.length === 0 ? (
        <div className="card">
          <div className="empty-state">
            <div className="empty-state-icon">
              <Icon.Server />
            </div>
            <h3>还没有 provider</h3>
            <p>添加你的第一个 LLM 服务商开始监测可用性。</p>
            <button onClick={() => setShowAdd(true)} style={{ marginTop: 12 }}>
              <Icon.Plus />
              添加 Provider
            </button>
          </div>
        </div>
      ) : (
        <div
          className="provider-grid stagger"
          role="list"
          aria-label="Providers"
        >
          {providers.map((p) => {
            const dash = dashboard?.providers.find((d) => d.provider_id === p.id);
            const models = modelsByProvider[p.id] ?? [];
            const status = lastStatusById[p.id];
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
                      <span className={`status-dot ${status ?? "unknown"}`} />
                      {p.name}
                    </div>
                    <div className="meta">
                      <span className="kind-badge">{p.kind}</span>
                      <span style={{ marginLeft: 8 }}>
                        {models.length} models · {p.interval_seconds}s 间隔
                      </span>
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
                      <div className="label">24h 可用率</div>
                      <div className="value">
                        {dash.availability_24h != null
                          ? `${dash.availability_24h.toFixed(1)}%`
                          : "—"}
                      </div>
                    </div>
                    <div className="metric">
                      <div className="label">最近</div>
                      <div className="value" style={{ fontSize: 14 }}>
                        {formatRelative(dash.last_checked_at)}
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
                    title="编辑"
                    aria-label={`编辑 ${p.name}`}
                  >
                    <Icon.Edit />
                  </button>
                  <button
                    className="secondary sm"
                    onClick={() => api.runNow(p.id).then(refresh)}
                    aria-label={`立即探测 ${p.name}`}
                  >
                    <Icon.Run />
                    Run
                  </button>
                  <button
                    className="secondary sm"
                    onClick={() => api.syncModels(p.id).then(refresh)}
                    aria-label={`同步模型 ${p.name}`}
                  >
                    <Icon.Sync />
                    Sync
                  </button>
                  <button
                    className="ghost sm"
                    onClick={async () => {
                      if (!confirm(`删除 ${p.name}?`)) return;
                      await api.deleteProvider(p.id);
                      await refresh();
                    }}
                    title="删除"
                    aria-label={`删除 ${p.name}`}
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

function ProviderDialog({
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

  // Close on Escape; focus the first input on mount.
  const modalRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    // focus first focusable element
    const first = modalRef.current?.querySelector<HTMLElement>(
      "input, select, textarea, button",
    );
    first?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

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
        await api.patchProvider(provider.id, body);
      } else {
        await api.createProvider({
          name,
          kind,
          base_url: baseUrl,
          api_key: apiKey,
          proxy: proxy || null,
          interval_seconds: intervalSec,
          timeout_seconds: timeoutSec,
          headers_json: headersJson || null,
          enabled,
        });
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
          <h3 id="provider-dialog-title">{isEdit ? `编辑 ${provider!.name}` : "添加 Provider"}</h3>
          <button className="modal-close" onClick={onClose} aria-label="关闭">
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
            <label>{isEdit ? "API Key（留空不修改）" : "API Key"}</label>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="sk-…"
            />
          </div>
          <div className="form-row full">
            <label>Proxy（可选）</label>
            <input
              value={proxy}
              onChange={(e) => setProxy(e.target.value)}
              placeholder="http://127.0.0.1:7890"
            />
          </div>
          <div className="form-row">
            <label>Interval (秒)</label>
            <input
              type="number"
              value={intervalSec}
              min={10}
              max={86400}
              onChange={(e) => setIntervalSec(Number(e.target.value))}
            />
          </div>
          <div className="form-row">
            <label>Timeout (秒)</label>
            <input
              type="number"
              value={timeoutSec}
              min={2}
              max={600}
              onChange={(e) => setTimeoutSec(Number(e.target.value))}
            />
          </div>
          <div className="form-row full">
            <label>Custom Headers（JSON，可选）</label>
            <textarea
              rows={2}
              value={headersJson}
              onChange={(e) => setHeadersJson(e.target.value)}
              placeholder='{"X-Org": "acme"}'
            />
          </div>
          <div className="form-row full checkbox">
            <label>
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
              />
              启用检测
            </label>
          </div>
        </div>

        {err && <div className="error">{err}</div>}

        <div className="modal-footer">
          <button className="secondary" onClick={onClose} disabled={busy}>
            取消
          </button>
          <button onClick={submit} disabled={busy || (!isEdit && (!name || !apiKey))}>
            {busy ? (
              <>
                <span className="spinner" /> 保存中…
              </>
            ) : (
              "保存"
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
