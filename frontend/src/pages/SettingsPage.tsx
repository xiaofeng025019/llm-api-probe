import { useEffect, useState } from "react";
import { api, Setting } from "../api/types";
import { Icon } from "../components/Icons";
import { withErrorToast, describeError } from "../lib/action";
import { pushToast } from "../components/Toast";

const KNOWN_KEYS = [
  { key: "default_interval_seconds", desc: "Default model list refresh interval (s)", defaultValue: "300" },
  { key: "favorite_model_interval_seconds", desc: "Favorite model probe interval (s)", defaultValue: "300" },
  { key: "regular_model_interval_seconds", desc: "Regular model probe interval (s)", defaultValue: "600" },
  { key: "favorite_model_failure_confirmations", desc: "Consecutive failures to confirm a favorite model as down", defaultValue: "2" },
  { key: "regular_model_failure_confirmations", desc: "Consecutive failures to confirm a regular model as down", defaultValue: "3" },
  { key: "default_timeout_seconds", desc: "Default per-probe timeout (s)", defaultValue: "30" },
  { key: "max_concurrency", desc: "Max concurrent probes (global)", defaultValue: "10" },
  { key: "retention_days", desc: "Days to keep historical probe results", defaultValue: "30" },
];

export function SettingsPage() {
  const [settings, setSettings] = useState<Setting[]>([]);
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [importErr, setImportErr] = useState<string | null>(null);

  async function load() {
    const s = await api.settings();
    setSettings(s);
    setValues({
      ...Object.fromEntries(KNOWN_KEYS.map((x) => [x.key, x.defaultValue])),
      ...Object.fromEntries(s.map((x) => [x.key, x.value])),
    });
  }

  useEffect(() => {
    load();
  }, []);

  async function save() {
    setBusy(true);
    setMsg(null);
    try {
      await withErrorToast(api.putSettings(values), "Save");
      setMsg("✓ Saved");
      await load();
    } catch (e) {
      const { detail } = describeError(e);
      setMsg(detail ?? "Save failed");
    } finally {
      setBusy(false);
    }
  }

  async function doExport() {
    try {
      const data = await withErrorToast(api.exportConfig(), "Export");
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `llm-usability-config-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      /* toast already shown */
    }
  }

  async function doImport() {
    setImportErr(null);
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "application/json";
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) return;
      try {
        const text = await file.text();
        const payload = JSON.parse(text);
        const r = await withErrorToast(
          api.importConfig(payload),
          "Import",
        );
        setMsg(`✓ Import: created ${r.providers_created}, updated ${r.providers_updated}`);
        await load();
        pushToast("ok", "Import complete", `Created ${r.providers_created}, updated ${r.providers_updated}`);
      } catch (e) {
        const { detail } = describeError(e);
        setImportErr(detail ?? "Import failed");
      }
    };
    input.click();
  }

  const otherSettings = settings.filter((s) => !KNOWN_KEYS.find((k) => k.key === s.key));

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Settings</h1>
          <div className="subtitle">Global configuration and import/export</div>
        </div>
      </div>

      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Icon.Settings />
            Global Settings
          </div>
        </div>
        <div className="card">
          <div className="form-grid">
            {KNOWN_KEYS.map(({ key, desc }) => (
              <div className="form-row" key={key}>
                <label htmlFor={`setting-${key}`}>{key}</label>
                <input
                  value={values[key] ?? ""}
                  onChange={(e) => setValues((v) => ({ ...v, [key]: e.target.value }))}
                  id={`setting-${key}`}
                  aria-describedby={`setting-${key}-hint`}
                />
                <span className="hint" id={`setting-${key}-hint`}>
                  {desc}
                </span>
              </div>
            ))}
          </div>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              marginTop: 20,
              paddingTop: 16,
              borderTop: "1px solid var(--divider)",
            }}
          >
            <button onClick={save} disabled={busy}>
              {busy ? (
                <>
                  <span className="spinner" /> Saving...
                </>
              ) : (
                "Save"
              )}
            </button>
            {msg && (
              <span className="muted" role="status" aria-live="polite">
                {msg}
              </span>
            )}
          </div>
          {otherSettings.length > 0 && (
            <details style={{ marginTop: 16 }}>
              <summary className="muted" style={{ cursor: "pointer" }}>
                Other settings ({otherSettings.length})
              </summary>
              <pre
                style={{
                  background: "var(--bg-sunken)",
                  padding: 12,
                  fontSize: 12,
                  borderRadius: "var(--r-sm)",
                  marginTop: 8,
                  overflow: "auto",
                }}
              >
                {JSON.stringify(otherSettings, null, 2)}
              </pre>
            </details>
          )}
        </div>
      </div>

      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Icon.Download />
            Import / Export
          </div>
        </div>
        <div className="card settings-transfer-card">
          <div className="settings-transfer-grid">
            <div className="settings-transfer-panel">
              <div className="settings-transfer-icon">
                <Icon.Download />
              </div>
              <div className="settings-transfer-copy">
                <strong>Export configuration</strong>
                <span>Download a JSON snapshot of current providers, favorites, and settings.</span>
              </div>
              <button className="secondary settings-transfer-action" onClick={() => doExport()}>
                <Icon.Download />
                Export configuration
              </button>
            </div>
            <div className="settings-transfer-panel">
              <div className="settings-transfer-icon">
                <Icon.Upload />
              </div>
              <div className="settings-transfer-copy">
                <strong>Import configuration</strong>
                <span>Choose a JSON file to update providers, favorite models, and global settings.</span>
              </div>
              <button className="settings-transfer-action" onClick={() => doImport()}>
                <Icon.Upload />
                Import configuration
              </button>
            </div>
          </div>
          {importErr && <div className="error">{importErr}</div>}
        </div>
      </div>
    </div>
  );
}
