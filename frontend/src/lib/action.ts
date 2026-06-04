import { pushToast } from "../components/Toast";

/** Show a failure toast for an unhandled error from a user action. */
export function describeError(e: unknown): { title: string; detail?: string } {
  if (e instanceof Error) {
    // fetch() throws TypeError on network failure; the message is usually
    // "Failed to fetch" or "NetworkError when attempting to fetch resource".
    if (e instanceof TypeError && /fetch|network/i.test(e.message)) {
      return { title: "Network error", detail: "Check your connection and try again" };
    }
    return { title: "Action failed", detail: e.message };
  }
  return { title: "Action failed", detail: String(e) };
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
