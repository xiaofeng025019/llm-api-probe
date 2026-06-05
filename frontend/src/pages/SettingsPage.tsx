import { useEffect, useState } from "react";
import { api, Setting } from "../api/types";
import { Icon } from "../components/Icons";
import { withErrorToast, describeError } from "../lib/action";
import { pushToast } from "../components/Toast";
import { useT } from "../hooks/useT";

const KNOWN_SETTINGS = [
  { key: "default_interval_seconds", descKey: "settingsPage.setting.defaultInterval", defaultValue: "300" },
  { key: "favorite_model_interval_seconds", descKey: "settingsPage.setting.favoriteInterval", defaultValue: "300" },
  { key: "regular_model_interval_seconds", descKey: "settingsPage.setting.regularInterval", defaultValue: "600" },
  { key: "favorite_model_failure_confirmations", descKey: "settingsPage.setting.favoriteConfirm", defaultValue: "2" },
  { key: "regular_model_failure_confirmations", descKey: "settingsPage.setting.regularConfirm", defaultValue: "3" },
  { key: "default_timeout_seconds", descKey: "settingsPage.setting.defaultTimeout", defaultValue: "30" },
  { key: "max_concurrency", descKey: "settingsPage.setting.maxConcurrency", defaultValue: "10" },
  { key: "retention_days", descKey: "settingsPage.setting.retentionDays", defaultValue: "30" },
] as const;

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
            {KNOWN_SETTINGS.map(({ key, descKey }) => (
              <div className="form-row" key={key}>
                <label htmlFor={`setting-${key}`}>{key}</label>
                <input
                  value={values[key] ?? ""}
                  onChange={(e) => setValues((v) => ({ ...v, [key]: e.target.value }))}
                  id={`setting-${key}`}
                  aria-describedby={`setting-${key}-hint`}
                />
                <span className="hint" id={`setting-${key}-hint`}>
                  {t(descKey)}
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
