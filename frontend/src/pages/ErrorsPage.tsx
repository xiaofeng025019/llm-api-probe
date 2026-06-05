import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ErrorEvent, ProbeResult } from "../api/types";
import { useT } from "../hooks/useT";
import { formatRelative } from "../lib/format";
import { pushToast } from "../components/Toast";
import { withErrorToast } from "../lib/action";
import { IconAlertTriangle, IconPin, IconPinFilled, IconRefresh } from "../components/Icons";

const ERROR_LIMIT = 200;

export function ErrorsPage() {
  const t = useT();
  const [errors, setErrors] = useState<ErrorEvent[] | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [busyIds, setBusyIds] = useState<Record<string, boolean>>({});

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const list = await api.errors({ limit: ERROR_LIMIT });
      setErrors(list);
    } catch (e) {
      pushToast("fail", t("errors.toast.loadFailed"), e instanceof Error ? e.message : String(e));
    } finally {
      setRefreshing(false);
    }
  }, [t]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const onPin = useCallback(
    async (e: ErrorEvent) => {
      setBusyIds((m) => ({ ...m, [e.id]: true }));
      try {
        if (e.pinned) {
          await withErrorToast(api.unpinError(e.id), t("errors.toast.unpin"));
        } else {
          await withErrorToast(api.pinError(e.id), t("errors.toast.pin"));
        }
        await refresh();
      } finally {
        setBusyIds((m) => {
          const next = { ...m };
          delete next[e.id];
          return next;
        });
      }
    },
    [refresh, t],
  );

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>{t("errors.title")}</h1>
          <div className="subtitle">{t("errors.subtitle")}</div>
        </div>
        <div className="page-header-actions">
          <button
            type="button"
            className="btn btn-ghost"
            onClick={refresh}
            disabled={refreshing}
            aria-label={t("errors.refresh.ariaLabel")}
            title={t("errors.refresh.title")}
          >
            <span className={refreshing ? "spin" : ""} aria-hidden="true">
              <IconRefresh />
            </span>
            <span>{t("errors.refresh.label")}</span>
          </button>
        </div>
      </div>

      {errors === null ? (
        <div className="empty-state">
          <div className="empty-state-icon" aria-hidden="true">
            <IconAlertTriangle />
          </div>
          <p>{t("errors.loading")}</p>
        </div>
      ) : errors.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon" aria-hidden="true">
            <IconAlertTriangle />
          </div>
          <h2>{t("errors.empty.title")}</h2>
          <p>{t("errors.empty.subtitle")}</p>
        </div>
      ) : (
        <ul className="error-list stagger" role="list">
          {errors.map((e) => (
            <li
              key={e.id}
              className={`error-card${e.pinned ? " error-card-pinned" : ""}`}
              data-pinned={e.pinned ? "true" : "false"}
            >
              <div className="error-card-head">
                <div className="error-card-target">
                  <span className="error-code-pill" data-code={e.error_code ?? "unknown"}>
                    {e.error_code ?? t("errors.unknownCode")}
                  </span>
                  <span className="error-card-provider">
                    {e.provider_name_at_probe}
                    {e.model_id_at_probe ? (
                      <>
                        <span className="error-card-sep" aria-hidden="true">
                          /
                        </span>
                        <span className="error-card-model">{e.model_id_at_probe}</span>
                      </>
                    ) : (
                      <span className="error-card-sep" aria-hidden="true">
                        /list_models
                      </span>
                    )}
                  </span>
                </div>
                <div className="error-card-meta">
                  <span className="error-card-ago" title={e.checked_at}>
                    {formatRelative(e.checked_at)}
                  </span>
                  <Link
                    to={`/providers/${e.provider_uuid_at_probe}`}
                    className="error-card-link"
                    title={t("errors.openProvider")}
                  >
                    →
                  </Link>
                  <button
                    type="button"
                    className="error-pin-btn"
                    onClick={() => onPin(e)}
                    disabled={busyIds[e.id]}
                    aria-pressed={e.pinned}
                    title={e.pinned ? t("errors.unpin.title") : t("errors.pin.title")}
                    aria-label={e.pinned ? t("errors.unpin.title") : t("errors.pin.title")}
                  >
                    {e.pinned ? <IconPinFilled /> : <IconPin />}
                  </button>
                </div>
              </div>
              {e.error_message && (
                <details className="error-card-msg">
                  <summary>
                    <span className="error-msg-summary-text">{summarize(e.error_message)}</span>
                  </summary>
                  <pre className="error-msg-full">{e.error_message}</pre>
                </details>
              )}
            </li>
          ))}
        </ul>
      )}

      {errors && errors.length >= ERROR_LIMIT && (
        <p className="muted" role="status">
          {t("errors.truncated", { limit: ERROR_LIMIT })}
        </p>
      )}
    </div>
  );
}

/** Short, single-line summary of an error_message. Many error
 *  payloads are JSON blobs from upstream APIs; we surface the first
 *  key:value pair as a useful hint without flooding the row. */
function summarize(msg: string): string {
  const trimmed = msg.trim();
  if (trimmed.length <= 120) return trimmed;
  // Try to find a human-readable first line
  const firstLine = trimmed.split(/[\r\n]+/, 1)[0];
  if (firstLine.length <= 200) return firstLine;
  return firstLine.slice(0, 200) + "…";
}

// Silence unused import warning — ProbeResult is referenced for
// type re-export in api/types.ts.
export type { ProbeResult };
