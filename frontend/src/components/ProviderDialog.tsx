// ProviderDialog — add/edit provider modal.
//
// Lived inside `pages/ProvidersPage.tsx` until 2026-06-06 when that
// page was deleted (the route is unreachable, redirects to /). The
// dialog itself is still used by DashboardPage's "Add Provider" /
// edit buttons, so we extract it to its own component file rather
// than keeping a 600-line page just to host one export.

import { useEffect, useRef, useState } from "react";
import { api, Provider, ProviderKind } from "../api/types";
import { IconEye, IconEyeOff } from "./Icons";
import { withErrorToast } from "../lib/action";
import { useT } from "../hooks/useT";

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
