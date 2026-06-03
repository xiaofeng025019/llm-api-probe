import { useEffect } from "react";

export function useSse(onEvent: (event: string, data: unknown) => void) {
  useEffect(() => {
    const es = new EventSource("/api/v1/events");
    const handler = (e: MessageEvent) => {
      try {
        onEvent(e.type, JSON.parse(e.data));
      } catch {
        onEvent(e.type, e.data);
      }
    };
    es.addEventListener("ping", handler as EventListener);
    es.addEventListener("probe.completed", handler as EventListener);
    es.addEventListener("provider.updated", handler as EventListener);
    es.addEventListener("model.updated", handler as EventListener);
    es.addEventListener("job.error", handler as EventListener);
    es.onerror = () => {
      // browser auto-reconnects
    };
    return () => es.close();
  }, [onEvent]);
}
