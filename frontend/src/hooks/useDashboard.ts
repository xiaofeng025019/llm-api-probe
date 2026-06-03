import { useCallback, useEffect, useState } from "react";
import { api, Dashboard, ModelOut, Provider } from "../api/types";
import { useSse } from "./useSse";

export interface DashboardState {
  dashboard: Dashboard | null;
  providers: Provider[];
  modelsByProvider: Record<number, ModelOut[]>;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

export function useDashboard(autoRefreshOnSse = true): DashboardState {
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [modelsByProvider, setModelsByProvider] = useState<Record<number, ModelOut[]>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const [d, ps] = await Promise.all([api.dashboard(), api.providers()]);
      setDashboard(d);
      setProviders(ps);
      const entries = await Promise.all(
        ps.map(async (p) => [p.id, await api.models(p.id)] as const),
      );
      setModelsByProvider(Object.fromEntries(entries));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    const t = setInterval(refresh, 30_000);
    return () => clearInterval(t);
  }, [refresh]);

  useSse((event) => {
    if (!autoRefreshOnSse) return;
    if (
      event === "probe.completed" ||
      event === "model.updated" ||
      event === "provider.updated"
    ) {
      void refresh();
    }
  });

  return { dashboard, providers, modelsByProvider, loading, error, refresh };
}
