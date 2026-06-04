import { useEffect, useRef } from "react";

/**
 * Subscribe to /api/v1/events. The latest callback is held in a ref so the
 * underlying EventSource is opened exactly once per component lifecycle and
 * never torn down on parent re-render. Reconnects on error with exponential
 * backoff up to 30s.
 */
export function useSse(onEvent: (event: string, data: unknown) => void) {
  const callbackRef = useRef(onEvent);
  callbackRef.current = onEvent;

  useEffect(() => {
    let es: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let backoff = 1000;
    let stopped = false;

    const handler = (e: MessageEvent) => {
      try {
        callbackRef.current(e.type, JSON.parse(e.data));
      } catch {
        callbackRef.current(e.type, e.data);
      }
    };

    const connect = () => {
      if (stopped) return;
      es = new EventSource("/api/v1/events");
      es.addEventListener("ping", handler as EventListener);
      es.addEventListener("probe.completed", handler as EventListener);
      es.addEventListener("provider.updated", handler as EventListener);
      es.addEventListener("model.updated", handler as EventListener);
      es.addEventListener("job.error", handler as EventListener);
      es.onerror = () => {
        // Browser only auto-reconnects on transient network errors, not on
        // 4xx/5xx responses (CLOSED state). We close explicitly and reconnect
        // ourselves with backoff so any HTTP-level error recovers.
        if (es) {
          es.close();
          es = null;
        }
        if (stopped) return;
        reconnectTimer = setTimeout(connect, backoff);
        backoff = Math.min(backoff * 2, 30_000);
      };
      // reset backoff on a clean open
      es.onopen = () => {
        backoff = 1000;
      };
    };

    connect();

    return () => {
      stopped = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (es) es.close();
    };
  }, []);
}
