import { useCallback, useEffect, useRef, useState } from "react";
import { api, Dashboard, ModelOut, Provider, Setting } from "../api/types";
import { useSse } from "./useSse";

export interface DashboardState {
  dashboard: Dashboard | null;
  providers: Provider[];
  modelsByProvider: Record<string, ModelOut[]>;
  settings: Setting[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

export function useDashboard(
  autoRefreshOnSse = true,
  options: { loadModels?: boolean; loadProviders?: boolean } = {},
): DashboardState {
  const loadModels = options.loadModels ?? true;
  const loadProviders = options.loadProviders ?? true;
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [modelsByProvider, setModelsByProvider] = useState<Record<string, ModelOut[]>>({});
  const [settings, setSettings] = useState<Setting[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Two-tier refresh:
  //  - fast tier: dashboard + providers + settings. Cheap. The
  //    dashboard already has aggregated per-provider stats and
  //    favorites with latency/error info, which is what the
  //    dashboard cards actually need.
  //  - slow tier: per-provider model lists. 1 request per provider.
  //    Consumed by ModelsPage (Favorite Models grid). Previously also
  //    by ProvidersPage, deleted 2026-06-06. Fields used (model_id /
  //    is_favorite / status) change at human, not burst, speed.
  //
  // SSE-driven refresh only fires the fast tier. Slow tier runs
  // on mount, on explicit refresh() calls, and on a slow interval.
  // This caps a refresh-all burst at ~1 fast refresh, not 50.
  const inFlightFastRef = useRef<Promise<void> | null>(null);
  const inFlightSlowRef = useRef(false);

  const refreshFast = useCallback(async () => {
    // Dedupe: if a fast refresh is already in flight, return the same
    // promise so concurrent triggers (SSE coalesce + interval + manual)
    // collapse to a single request.
    if (inFlightFastRef.current) return inFlightFastRef.current;
    setError(null);
    const p = (async () => {
      try {
        const [d, ps, ss] = await Promise.all([
          api.dashboard(),
          loadProviders ? api.providers() : Promise.resolve([]),
          api.settings(),
        ]);
        setDashboard(d);
        setProviders(ps);
        setSettings(ss);
      } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
      inFlightFastRef.current = null;
    }
    })();
    inFlightFastRef.current = p;
    return p;
  }, [loadProviders]);

  const refreshSlow = useCallback(async () => {
    if (!loadModels) return;
    if (inFlightSlowRef.current) return;
    inFlightSlowRef.current = true;
    try {
      // Snapshot current provider list — if providers changes mid-call
      // we still write consistent state from one snapshot.
      const ps = await api.providers();
      const entries = await Promise.all(
        ps.map(async (p) => [p.id, await api.models(p.id)] as const),
      );
      setModelsByProvider(Object.fromEntries(entries));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      inFlightSlowRef.current = false;
    }
  }, [loadModels]);

  // Public refresh: both tiers. Used by user-initiated actions
  // (delete, patch, etc.) where consistency matters.
  const refresh = useCallback(async () => {
    await refreshFast();
    await refreshSlow();
  }, [refreshFast, refreshSlow]);

  useEffect(() => {
    void refreshFast();
    if (loadModels) void refreshSlow();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadModels]);

  useEffect(() => {
    const t = setInterval(refreshFast, 30_000);
    return () => clearInterval(t);
  }, [refreshFast]);

  useEffect(() => {
    if (!loadModels) return;
    const t = setInterval(refreshSlow, 5 * 60_000);
    return () => clearInterval(t);
  }, [loadModels, refreshSlow]);

  // Debounce SSE-driven refresh. The probe scheduler can fire
  // `probe.completed` 50+ times in a few seconds (e.g. right after
  // a "Refresh all" click that scheduled every model). Without
  // coalescing, each event would re-fetch and re-render → noticeable
  // jank on the dashboard. Coalesce to one refresh per coalesce
  // window; the next probe batch's events will trigger another
  // refresh, so freshness is bounded at ~500ms.
  const coalesceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useSse((event) => {
    if (!autoRefreshOnSse) return;
    if (
      event !== "probe.completed" &&
      event !== "model.updated" &&
      event !== "provider.updated"
    )
      return;
    if (coalesceRef.current) clearTimeout(coalesceRef.current);
    coalesceRef.current = setTimeout(() => {
      coalesceRef.current = null;
      void refreshFast();
    }, 500);
  });

  return { dashboard, providers, modelsByProvider, settings, loading, error, refresh };
}
