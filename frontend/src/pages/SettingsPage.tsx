import { useEffect, useState } from "react";
import { api, Setting } from "../api/types";
import { Icon } from "../components/Icons";
import { withErrorToast, describeError } from "../lib/action";
import { pushToast } from "../components/Toast";

const KNOWN_KEYS = [
  { key: "default_interval_seconds", desc: "默认模型清单更新间隔（秒）", defaultValue: "300" },
  { key: "favorite_model_interval_seconds", desc: "重点关注模型状态检测间隔（秒）", defaultValue: "300" },
  { key: "regular_model_interval_seconds", desc: "普通模型状态检测间隔（秒）", defaultValue: "600" },
  { key: "default_timeout_seconds", desc: "默认超时（秒）", defaultValue: "30" },
  { key: "max_concurrency", desc: "全局最大并发探测数", defaultValue: "10" },
  { key: "retention_days", desc: "历史结果保留天数", defaultValue: "30" },
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
      await withErrorToast(api.putSettings(values), "保存");
      setMsg("✓ 已保存");
      await load();
    } catch (e) {
      const { detail } = describeError(e);
      setMsg(detail ?? "保存失败");
    } finally {
      setBusy(false);
    }
  }

  async function doExport() {
    try {
      const data = await withErrorToast(api.exportConfig(), "导出");
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
          "导入",
        );
        setMsg(`✓ 导入：新建 ${r.providers_created}，更新 ${r.providers_updated}`);
        await load();
        pushToast("ok", "导入完成", `新建 ${r.providers_created}, 更新 ${r.providers_updated}`);
      } catch (e) {
        const { detail } = describeError(e);
        setImportErr(detail ?? "导入失败");
      }
    };
    input.click();
  }

  const otherSettings = settings.filter((s) => !KNOWN_KEYS.find((k) => k.key === s.key));

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>设置</h1>
          <div className="subtitle">全局配置和导入导出</div>
        </div>
      </div>

      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Icon.Settings />
            全局设置
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
                  <span className="spinner" /> 保存中…
                </>
              ) : (
                "保存"
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
                其它设置 ({otherSettings.length})
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
            导入 / 导出
          </div>
        </div>
        <div className="card settings-transfer-card">
          <div className="settings-transfer-grid">
            <div className="settings-transfer-panel">
              <div className="settings-transfer-icon">
                <Icon.Download />
              </div>
              <div className="settings-transfer-copy">
                <strong>导出配置</strong>
                <span>下载当前 providers、重点关注模型和 settings 的 JSON 快照。</span>
              </div>
              <button className="secondary settings-transfer-action" onClick={() => doExport()}>
                <Icon.Download />
                导出配置
              </button>
            </div>
            <div className="settings-transfer-panel">
              <div className="settings-transfer-icon">
                <Icon.Upload />
              </div>
              <div className="settings-transfer-copy">
                <strong>导入配置</strong>
                <span>选择 JSON 文件并更新 provider 配置、收藏模型和全局设置。</span>
              </div>
              <button className="settings-transfer-action" onClick={() => doImport()}>
                <Icon.Upload />
                导入配置
              </button>
            </div>
          </div>
          {importErr && <div className="error">{importErr}</div>}
        </div>
      </div>
    </div>
  );
}
