import { useEffect, useRef } from "react";

/**
 * Subscribe to /api/v1/events. The latest callback is held in a ref so the
 * underlying EventSource is opened exactly once per component lifecycle and
 * never torn down on parent re-render. Reconnects on error with exponential
 * backoff (full jitter, capped at 30s, resets to 1s only after the
 * connection has been open for at least 30s).
 *
 * Pauses on `visibilitychange` — when the tab is hidden, the EventSource
 * is closed and the reconnect timer is cancelled, so background tabs
 * don't drain battery or hold an idle connection. When the tab becomes
 * visible again, the connection is re-established.
 */
export function useSse(onEvent: (event: string, data: unknown) => void) {
  const callbackRef = useRef(onEvent);
  callbackRef.current = onEvent;

  useEffect(() => {
    let es: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let backoff = 1000;
    let openSince = 0; // timestamp of last successful onopen
    let stopped = false;
    let hidden = false;

    const handleMessage = (e: MessageEvent) => {
      // Skip empty payloads (partial frame, server hot-reload, etc.).
      if (e.data === undefined || e.data === null || e.data === "") return;
      let parsed: unknown;
      try {
        parsed = JSON.parse(e.data);
      } catch {
        // Malformed JSON — skip rather than handing a raw string to
        // the consumer. A previous version of this hook called the
        // callback with `e.data` on parse failure, which silently
        // killed the stream for some consumers (e.g. GlobalSse in
        // main.tsx checks `typeof data === "object"` and no-ops on
        // strings — every subsequent event then falls into a dead
        // branch).
        return;
      }
      try {
        callbackRef.current(e.type, parsed);
      } catch (err) {
        // A consumer that throws must not kill the EventSource.
        // We log and continue with the next event.
        console.warn("SSE consumer threw", err);
      }
    };

    const connect = () => {
      if (stopped || hidden) return;
      es = new EventSource("/api/v1/events");
      es.addEventListener("ping", handleMessage as EventListener);
      es.addEventListener("probe.completed", handleMessage as EventListener);
      es.addEventListener("provider.updated", handleMessage as EventListener);
      es.addEventListener("model.updated", handleMessage as EventListener);
      es.addEventListener("job.error", handleMessage as EventListener);
      es.onerror = () => {
        // Browser only auto-reconnects on transient network errors, not on
        // 4xx/5xx responses (CLOSED state). We close explicitly and reconnect
        // ourselves with backoff so any HTTP-level error recovers.
        if (es) {
          es.close();
          es = null;
        }
        if (stopped || hidden) return;
        // Full jitter: spread the reconnect attempts to avoid thundering-herd
        // if many clients reconnect in lockstep after a server hiccup.
        const delay = backoff + Math.random() * 250;
        reconnectTimer = setTimeout(connect, delay);
        backoff = Math.min(backoff * 2, 30_000);
      };
      es.onopen = () => {
        // Only reset backoff if the connection has actually been sustained —
        // a server that crash-loops every 5s should not get fresh 1s retries.
        const now = Date.now();
        if (now - openSince > 30_000) {
          backoff = 1000;
        }
        openSince = now;
      };
    };

    const onVisibility = () => {
      if (document.hidden) {
        // Pause: drop the connection and cancel any pending reconnect.
        // Frees the network radio on background tabs.
        hidden = true;
        if (reconnectTimer) {
          clearTimeout(reconnectTimer);
          reconnectTimer = null;
        }
        if (es) {
          es.close();
          es = null;
        }
      } else {
        // Resume: reconnect with a clean backoff state so a long-hidden
        // tab doesn't have to wait the full 30s cap on reattach.
        if (hidden) {
          hidden = false;
          backoff = 1000;
          openSince = 0;
          connect();
        }
      }
    };

    document.addEventListener("visibilitychange", onVisibility);
    connect();

    return () => {
      stopped = true;
      document.removeEventListener("visibilitychange", onVisibility);
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (es) es.close();
    };
  }, []);
}
