import { pushToast } from "../components/Toast";
import { t as i18nT } from "./i18n";
import { ApiError } from "../api/types";

/** Show a failure toast for an unhandled error from a user action. */
export function describeError(e: unknown): { title: string; detail?: string } {
  // ApiError carries server-side detail + (on 429/503) a Retry-After hint.
  // Surface both so the user sees "wait 5s" instead of a bare "503".
  if (e instanceof ApiError) {
    const base = e.message;
    const retry = e.retry_after_seconds;
    const detail =
      typeof retry === "number"
        ? `${base} — ${i18nT("errors.retryIn", { seconds: retry })}`
        : base;
    return { title: i18nT("errors.action.title"), detail };
  }
  if (e instanceof Error) {
    // fetch() throws TypeError on network failure; the message is usually
    // "Failed to fetch" or "NetworkError when attempting to fetch resource".
    if (e instanceof TypeError && /fetch|network/i.test(e.message)) {
      return {
        title: i18nT("errors.network.title"),
        detail: i18nT("errors.network.detail"),
      };
    }
    return { title: i18nT("errors.action.title"), detail: e.message };
  }
  return { title: i18nT("errors.action.title"), detail: String(e) };
}

/**
 * Wrap a user-action promise with a toast on failure. Returns the original
 * promise so callers can chain their own `.then` (e.g. refresh state).
 */
export function withErrorToast<T>(p: Promise<T>, label = "Action"): Promise<T> {
  return p.catch((e: unknown) => {
    const { title, detail } = describeError(e);
    pushToast("fail", `${label}: ${title}`, detail);
    throw e;
  });
}
