export type ProviderKind = "openai" | "openai_compat" | "anthropic" | "gemini";
export type ModelType =
  | "chat"
  | "vision"
  | "audio"
  | "image"
  | "embedding"
  | "code"
  | "unknown";

export interface Provider {
  id: number;
  name: string;
  kind: ProviderKind;
  base_url: string;
  enabled: boolean;
  interval_seconds: number;
  timeout_seconds: number;
  proxy: string | null;
  headers_json: string | null;
  created_at: string;
  updated_at: string;
}

export interface ModelOut {
  id: number;
  provider_id: number;
  model_id: string;
  display_name: string | null;
  type: ModelType;
  enabled: boolean;
  is_favorite: boolean;
  last_seen_at: string;
}

export interface DashboardProvider {
  provider_id: number;
  name: string;
  kind: ProviderKind;
  enabled: boolean;
  model_count: number;
  last_checked_at: string | null;
  last_status: "ok" | "fail" | null;
  availability_24h: number | null;
  avg_latency_ms_24h: number | null;
  favorite_models_online: number;
  favorite_models_total: number;
}

export interface Dashboard {
  providers: DashboardProvider[];
  totals: {
    providers: number;
    models: number;
    ok: number;
    failing: number;
    favorites_online: number;
    favorites_total: number;
  };
}

export interface ProbeResult {
  id: number;
  provider_id: number;
  model_id: number | null;
  target: "list_models" | "chat_completion";
  success: boolean;
  http_status: number | null;
  latency_ms: number | null;
  ttfb_ms: number | null;
  error_code: string | null;
  error_message: string | null;
  checked_at: string;
}

export interface Setting {
  key: string;
  value: string;
  updated_at: string;
}

export interface ApiResponse<T> {
  data: T | null;
  error: { code: string; message: string; details?: unknown } | null;
}

const BASE = "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(BASE + path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  const body = (await r.json()) as ApiResponse<T>;
  if (body.error) {
    throw new Error(`${body.error.code}: ${body.error.message}`);
  }
  return body.data as T;
}

export const api = {
  dashboard: () => request<Dashboard>("/api/v1/dashboard"),
  providers: () => request<Provider[]>("/api/v1/providers"),
  getProvider: (id: number) => request<Provider>(`/api/v1/providers/${id}`),
  patchProvider: (id: number, body: Partial<Provider>) =>
    request<Provider>(`/api/v1/providers/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  syncModels: (id: number) =>
    request<ModelOut[]>(`/api/v1/providers/${id}/sync-models`, { method: "POST" }),
  runNow: (id: number) =>
    request<{ scheduled: boolean }>(`/api/v1/providers/${id}/run`, { method: "POST" }),
  patchModel: (id: number, body: { is_favorite?: boolean; enabled?: boolean }) =>
    request<ModelOut>(`/api/v1/models/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  createProvider: (body: {
    name: string;
    kind: ProviderKind;
    base_url: string;
    api_key: string;
    proxy?: string | null;
    interval_seconds?: number;
    timeout_seconds?: number;
    headers_json?: string | null;
    enabled?: boolean;
  }) =>
    request<Provider>("/api/v1/providers", { method: "POST", body: JSON.stringify(body) }),
  deleteProvider: (id: number) =>
    request<{ deleted: number }>(`/api/v1/providers/${id}`, { method: "DELETE" }),
  models: (id: number) => request<ModelOut[]>(`/api/v1/providers/${id}/models`),
  results: (params: { provider_id?: number; model_id?: number; hours?: number; limit?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.provider_id != null) q.set("provider_id", String(params.provider_id));
    if (params.model_id != null) q.set("model_id", String(params.model_id));
    if (params.hours != null) q.set("hours", String(params.hours));
    if (params.limit != null) q.set("limit", String(params.limit));
    return request<ProbeResult[]>(`/api/v1/results?${q.toString()}`);
  },
  settings: () => request<Setting[]>("/api/v1/settings"),
  putSettings: (items: Record<string, string>) =>
    request<Setting[]>("/api/v1/settings", {
      method: "PUT",
      body: JSON.stringify({ items }),
    }),
  exportConfig: (includeKeys: boolean) =>
    request<unknown>(`/api/v1/export?include_keys=${includeKeys}`, { method: "POST" }),
  importConfig: (payload: unknown, includeKeys: boolean) =>
    request<{ providers_created: number; providers_updated: number }>(
      `/api/v1/import?include_keys=${includeKeys}`,
      { method: "POST", body: JSON.stringify(payload) },
    ),
  probeNow: (providerId?: number, modelId?: number) => {
    const q = new URLSearchParams();
    if (providerId != null) q.set("provider_id", String(providerId));
    if (modelId != null) q.set("model_id", String(modelId));
    return request<{ scheduled: boolean }>(`/api/v1/probe/run?${q.toString()}`, { method: "POST" });
  },
};

