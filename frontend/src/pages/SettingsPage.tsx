import { useEffect, useState } from "react";
import { api, Setting } from "../api/types";
import { Icon } from "../components/Icons";
import { withErrorToast, describeError } from "../lib/action";
import { pushToast } from "../components/Toast";
import { useT } from "../hooks/useT";

// Each KNOWN_SETTINGS row carries the input type the UI should
// render so numeric settings get a `type="number"` with min/max
// matching the backend validator (see backend/app/services/settings.py
// _INT_SETTING_BOUNDS / _BOOL_SETTING_KEYS). Without this, the user
// could type "abc" and submit; the backend rejected it but the UI
// previously gave no early feedback. Boolean rows render as a checkbox.
const KNOWN_SETTINGS = [
  { key: "default_interval_seconds", descKey: "settingsPage.setting.defaultInterval", defaultValue: "300", kind: "int", min: 10, max: 86400 },
  { key: "favorite_model_interval_seconds", descKey: "settingsPage.setting.favoriteInterval", defaultValue: "300", kind: "int", min: 10, max: 86400 },
  { key: "regular_model_interval_seconds", descKey: "settingsPage.setting.regularInterval", defaultValue: "600", kind: "int", min: 10, max: 86400 },
  { key: "provider_rate_limit_per_minute", descKey: "settingsPage.setting.providerRateLimit", defaultValue: "20", kind: "int", min: 1, max: 1000 },
  { key: "favorite_model_failure_confirmations", descKey: "settingsPage.setting.favoriteConfirm", defaultValue: "2", kind: "int", min: 1, max: 100 },
  { key: "regular_model_failure_confirmations", descKey: "settingsPage.setting.regularConfirm", defaultValue: "3", kind: "int", min: 1, max: 100 },
  { key: "default_timeout_seconds", descKey: "settingsPage.setting.defaultTimeout", defaultValue: "60", kind: "int", min: 2, max: 600 },
  { key: "max_concurrency", descKey: "settingsPage.setting.maxConcurrency", defaultValue: "10", kind: "int", min: 1, max: 1000 },
  { key: "retention_days", descKey: "settingsPage.setting.retentionDays", defaultValue: "30", kind: "int", min: 1, max: 3650 },
  { key: "adaptive_backoff_enabled", descKey: "settingsPage.setting.adaptiveBackoff", defaultValue: "true", kind: "bool" },
  { key: "idle_throttle_enabled", descKey: "settingsPage.setting.idleThrottle", defaultValue: "true", kind: "bool" },
] as const;

/** Truthy set in the backend's `_TRUTHY_STRINGS`. We render bool rows
 * as a checkbox; saving converts to "true"/"false" so the back-end
 * validator accepts either form. */
function isTruthy(v: string): boolean {
  return ["true", "1", "yes", "on"].includes(v.trim().toLowerCase());
}

export function SettingsPage() {
  const t = useT();
  const [settings, setSettings] = useState<Setting[]>([]);
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [importErr, setImportErr] = useState<string | null>(null);

  async function load() {
    const s = await api.settings();
    setSettings(s);
    setValues({
      ...Object.fromEntries(KNOWN_SETTINGS.map((x) => [x.key, x.defaultValue])),
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
      await withErrorToast(api.putSettings(values), t("settingsPage.save"));
      setMsg(t("toast.savedOk"));
      await load();
    } catch (e) {
      const { detail } = describeError(e);
      setMsg(detail ?? t("settingsPage.saveFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function doExport() {
    try {
      const data = await withErrorToast(api.exportConfig(), t("settingsPage.exportAction"));
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `llm-api-probe-config-${new Date().toISOString().slice(0, 10)}.json`;
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
          t("settingsPage.importAction"),
        );
        setMsg(t("toast.importOk", { created: r.providers_created, updated: r.providers_updated }));
        await load();
        pushToast(
          "ok",
          t("settingsPage.importComplete"),
          t("toast.importOk", { created: r.providers_created, updated: r.providers_updated }),
        );
      } catch (e) {
        const { detail } = describeError(e);
        setImportErr(detail ?? t("settingsPage.importFailed"));
      }
    };
    input.click();
  }

  const otherSettings = settings.filter((s) => !KNOWN_SETTINGS.find((k) => k.key === s.key));

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>{t("settingsPage.title")}</h1>
          <div className="subtitle">{t("settingsPage.subtitle")}</div>
        </div>
      </div>

      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Icon.Settings />
            {t("settingsPage.globalSettings")}
          </div>
        </div>
        <div className="card">
          <div className="form-grid">
            {KNOWN_SETTINGS.map((setting) => (
              <div className="form-row" key={setting.key}>
                <label htmlFor={`setting-${setting.key}`}>{setting.key}</label>
                {setting.kind === "bool" ? (
                  <input
                    id={`setting-${setting.key}`}
                    type="checkbox"
                    checked={isTruthy(values[setting.key] ?? setting.defaultValue)}
                    onChange={(e) =>
                      setValues((v) => ({
                        ...v,
                        [setting.key]: e.target.checked ? "true" : "false",
                      }))
                    }
                    aria-describedby={`setting-${setting.key}-hint`}
                  />
                ) : (
                  <input
                    id={`setting-${setting.key}`}
                    type="number"
                    inputMode="numeric"
                    min={setting.min}
                    max={setting.max}
                    step={1}
                    value={values[setting.key] ?? ""}
                    onChange={(e) =>
                      setValues((v) => ({ ...v, [setting.key]: e.target.value }))
                    }
                    aria-describedby={`setting-${setting.key}-hint`}
                  />
                )}
                <span className="hint" id={`setting-${setting.key}-hint`}>
                  {t(setting.descKey)}
                  {setting.kind === "int" && (
                    <span className="muted" style={{ marginLeft: 6 }}>
                      ({setting.min}–{setting.max})
                    </span>
                  )}
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
                  <span className="spinner" /> {t("settingsPage.saving")}
                </>
              ) : (
                t("settingsPage.save")
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
                {t("settingsPage.otherSettings", { n: otherSettings.length })}
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
            {t("settingsPage.transferTitle")}
          </div>
        </div>
        <div className="card settings-transfer-card">
          <div className="settings-transfer-grid">
            <div className="settings-transfer-panel">
              <div className="settings-transfer-icon">
                <Icon.Download />
              </div>
              <div className="settings-transfer-copy">
                <strong>{t("settingsPage.exportTitle")}</strong>
                <span>{t("settingsPage.exportDesc")}</span>
              </div>
              <button className="secondary settings-transfer-action" onClick={() => doExport()}>
                <Icon.Download />
                {t("settingsPage.exportAction")}
              </button>
            </div>
            <div className="settings-transfer-panel">
              <div className="settings-transfer-icon">
                <Icon.Upload />
              </div>
              <div className="settings-transfer-copy">
                <strong>{t("settingsPage.importTitle")}</strong>
                <span>{t("settingsPage.importDesc")}</span>
              </div>
              <button className="settings-transfer-action" onClick={() => doImport()}>
                <Icon.Upload />
                {t("settingsPage.importAction")}
              </button>
            </div>
          </div>
          {importErr && <div className="error">{importErr}</div>}
        </div>
      </div>
    </div>
  );
}
