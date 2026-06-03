import { useState } from "react";
import { api, Provider, ProviderKind } from "../api/types";
import { useDashboard } from "../hooks/useDashboard";
import { formatTime } from "../lib/format";

export function ProvidersPage() {
  const { providers, modelsByProvider, refresh } = useDashboard();
  const [showAdd, setShowAdd] = useState(false);
  const [editing, setEditing] = useState<Provider | null>(null);

  return (
    <div>
      <div className="toolbar">
        <button onClick={() => setShowAdd(true)}>+ 添加 Provider</button>
      </div>

      <div className="card">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Kind</th>
              <th>Base URL</th>
              <th>Enabled</th>
              <th>Interval</th>
              <th>Timeout</th>
              <th>Proxy</th>
              <th>Models</th>
              <th>Updated</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {providers.map((p) => (
              <tr key={p.id}>
                <td>{p.name}</td>
                <td>
                  <span className="badge" style={{ background: "var(--muted)" }}>
                    {p.kind}
                  </span>
                </td>
                <td>
                  <code style={{ fontSize: 12 }}>{p.base_url}</code>
                </td>
                <td>{p.enabled ? "yes" : "no"}</td>
                <td>{p.interval_seconds}s</td>
                <td>{p.timeout_seconds}s</td>
                <td>{p.proxy ?? "—"}</td>
                <td>{(modelsByProvider[p.id] ?? []).length}</td>
                <td className="muted">{formatTime(p.updated_at)}</td>
                <td>
                  <div className="row-actions">
                    <button className="secondary" onClick={() => setEditing(p)}>
                      Edit
                    </button>
                    <button
                      className="danger"
                      onClick={async () => {
                        if (!confirm(`删除 ${p.name}?`)) return;
                        await api.deleteProvider(p.id);
                        await refresh();
                      }}
                    >
                      Del
                    </button>
                  </div>
                </td>
              </tr>
            ))}
            {providers.length === 0 && (
              <tr>
                <td colSpan={10} className="muted">
                  还没有 provider。
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

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
  const [baseUrl, setBaseUrl] = useState(
    provider?.base_url ?? "https://api.openai.com",
  );
  const [apiKey, setApiKey] = useState("");
  const [proxy, setProxy] = useState(provider?.proxy ?? "");
  const [intervalSec, setIntervalSec] = useState(provider?.interval_seconds ?? 300);
  const [timeoutSec, setTimeoutSec] = useState(provider?.timeout_seconds ?? 30);
  const [headersJson, setHeadersJson] = useState(provider?.headers_json ?? "");
  const [enabled, setEnabled] = useState(provider?.enabled ?? true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

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
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>{isEdit ? `编辑 ${provider!.name}` : "添加 Provider"}</h3>
        <div className="form-row">
          <label>Name</label>
          <input
            value={name}
            disabled={isEdit}
            onChange={(e) => setName(e.target.value)}
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
        <div className="form-row">
          <label>{isEdit ? "API Key (留空不修改)" : "API Key"}</label>
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
          />
        </div>
        <div className="form-row">
          <label>Proxy (optional)</label>
          <input
            value={proxy}
            onChange={(e) => setProxy(e.target.value)}
            placeholder="http://127.0.0.1:7890"
          />
        </div>
        <div style={{ display: "flex", gap: 12 }}>
          <div className="form-row" style={{ flex: 1 }}>
            <label>Interval (s)</label>
            <input
              type="number"
              value={intervalSec}
              min={10}
              max={86400}
              onChange={(e) => setIntervalSec(Number(e.target.value))}
            />
          </div>
          <div className="form-row" style={{ flex: 1 }}>
            <label>Timeout (s)</label>
            <input
              type="number"
              value={timeoutSec}
              min={2}
              max={600}
              onChange={(e) => setTimeoutSec(Number(e.target.value))}
            />
          </div>
        </div>
        <div className="form-row">
          <label>Custom Headers (JSON, optional)</label>
          <textarea
            rows={2}
            value={headersJson}
            onChange={(e) => setHeadersJson(e.target.value)}
            placeholder='{"X-Org": "acme"}'
          />
        </div>
        <div className="form-row">
          <label>
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
            />{" "}
            Enabled
          </label>
        </div>
        {err && <div className="error">{err}</div>}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button className="secondary" onClick={onClose} disabled={busy}>
            取消
          </button>
          <button
            onClick={submit}
            disabled={busy || (!isEdit && (!name || !apiKey))}
          >
            {busy ? "保存中…" : "保存"}
          </button>
        </div>
      </div>
    </div>
  );
}
