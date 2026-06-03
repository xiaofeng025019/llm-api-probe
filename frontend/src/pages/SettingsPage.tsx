import { useEffect, useState } from "react";
import { api, Setting } from "../api/types";

const KNOWN_KEYS = [
  "default_interval_seconds",
  "default_timeout_seconds",
  "max_concurrency",
  "retention_days",
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
    setValues(Object.fromEntries(s.map((x) => [x.key, x.value])));
  }

  useEffect(() => {
    load();
  }, []);

  async function save() {
    setBusy(true);
    setMsg(null);
    try {
      await api.putSettings(values);
      setMsg("保存成功");
      await load();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function doExport(includeKeys: boolean) {
    const data = await api.exportConfig(includeKeys);
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `llm-usability-config-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function doImport(includeKeys: boolean) {
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
        const r = await api.importConfig(payload, includeKeys);
        setMsg(`导入：新建 ${r.providers_created}，更新 ${r.providers_updated}`);
        await load();
      } catch (e) {
        setImportErr(e instanceof Error ? e.message : String(e));
      }
    };
    input.click();
  }

  return (
    <div>
      <div className="card" style={{ marginBottom: 16 }}>
        <h2 style={{ marginTop: 0 }}>全局设置</h2>
        {KNOWN_KEYS.map((k) => (
          <div className="form-row" key={k}>
            <label>{k}</label>
            <input
              value={values[k] ?? ""}
              onChange={(e) => setValues((v) => ({ ...v, [k]: e.target.value }))}
            />
          </div>
        ))}
        <button onClick={save} disabled={busy}>
          {busy ? "保存中…" : "保存"}
        </button>
        {msg && <span style={{ marginLeft: 12 }} className="muted">{msg}</span>}
        <details style={{ marginTop: 12 }}>
          <summary>其它设置 ({settings.length - KNOWN_KEYS.length})</summary>
          <pre style={{ background: "#f3f4f6", padding: 8, fontSize: 12 }}>
            {JSON.stringify(
              settings.filter((s) => !KNOWN_KEYS.includes(s.key)),
              null,
              2,
            )}
          </pre>
        </details>
      </div>

      <div className="card">
        <h2 style={{ marginTop: 0 }}>导入 / 导出</h2>
        <p className="muted" style={{ marginTop: 0 }}>
          配置文件是 JSON 格式，包含 providers 元信息和 settings。
        </p>
        <div className="row-actions">
          <button className="secondary" onClick={() => doExport(false)}>
            导出（不含 key）
          </button>
          <button onClick={() => doExport(true)}>导出（含 key）</button>
          <button className="secondary" onClick={() => doImport(false)}>
            导入（保留原 key）
          </button>
          <button onClick={() => doImport(true)}>导入（覆盖 key）</button>
        </div>
        {importErr && <div className="error">{importErr}</div>}
      </div>
    </div>
  );
}
