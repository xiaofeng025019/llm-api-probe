export type ProviderKind = "openai" | "openai_compat" | "anthropic" | "gemini";
export type ModelType =
  | "chat"
  | "vision"
  | "audio"
  | "image"
  | "embedding"
  | "code"
  | "unknown";
export type ModelHealthStatus =
  | "online"
  | "suspect"
  | "offline"
  | "rate_limited"
  | "unauthorized"
  | "not_found"
  | "unknown";

export interface Provider {
  id: string;
  name: string;
  kind: ProviderKind;
  base_url: string;
  enabled: boolean;
  interval_seconds: number;
  timeout_seconds: number;
  proxy: string | null;
  headers_json: string | null;
  api_key?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ModelOut {
  id: string;
  provider_id: string;
  model_id: string;
  display_name: string | null;
  type: ModelType;
  enabled: boolean;
  is_favorite: boolean;
  last_seen_at: string;
  status: ModelHealthStatus;
  status_reason: string | null;
  status_checked_at: string | null;
  status_confirmed_at: string | null;
  last_success_at: string | null;
  consecutive_failures: number;
}

export interface DashboardFavoriteModel {
  id: string;
  model_id: string;
  display_name: string | null;
  type: ModelType;
  enabled: boolean;
  status: ModelHealthStatus | null;
  status_reason: string | null;
  status_checked_at: string | null;
  status_confirmed_at: string | null;
  last_success_at: string | null;
  last_checked_at: string | null;
  latency_ms: number | null;
  ttfb_ms: number | null;
  error_code: string | null;
  error_message: string | null;
  availability_24h: number | null;
  samples_24h: number;
  p95_latency_ms_24h: number | null;
  p95_ttfb_ms_24h: number | null;
  consecutive_failures: number;
}

export interface DashboardProvider {
  provider_id: string;
  name: string;
  kind: ProviderKind;
  base_url: string;
  enabled: boolean;
  interval_seconds: number;
  timeout_seconds: number;
  proxy: string | null;
  headers_json: string | null;
  model_count: number;
  last_checked_at: string | null;
  last_status: "ok" | "degraded" | "fail" | null;
  availability_24h: number | null;
  avg_latency_ms_24h: number | null;
  p95_latency_ms_24h: number | null;
  p95_ttfb_ms_24h: number | null;
  samples_24h: number;
  failures_24h: number;
  error_counts_24h: Record<string, number>;
  list_models_status: "ok" | "fail" | null;
  list_models_latency_ms: number | null;
  list_models_checked_at: string | null;
  list_models_error_code: string | null;
  available_models_online: number;
  favorite_models_online: number;
  favorite_models_total: number;
  favorite_models: DashboardFavoriteModel[];
}

export interface Dashboard {
  providers: DashboardProvider[];
  totals: {
    providers: number;
    models: number;
    ok: number;
    degraded?: number;
    failing: number;
    /** Number of models whose most recent probe in the last 24h succeeded. */
    available_models?: number;
    favorites_online: number;
    favorites_total: number;
    /** Same favorites_online metric but at the time the 24h window
     *  started. Used to show "↑2 vs 24h ago" delta on the Favorite
     *  Models card. */
    favorites_online_24h_ago?: number;
  };
}

export interface ProbeResult {
  id: string;
  provider_id: string;
  model_id: string | null;
  target: "list_models" | "chat_completion";
  success: boolean;
  http_status: number | null;
  latency_ms: number | null;
  ttfb_ms: number | null;
  error_code: string | null;
  error_message: string | null;
  checked_at: string;
  provider_uuid_at_probe: string;
  model_uuid_at_probe: string | null;
  provider_name_at_probe: string;
  model_id_at_probe: string | null;
  pinned: boolean;
}

/** One entry in the error history page list. Same shape as
 *  ProbeResult (so we can reuse formatting) but the list is
 *  curated: failed-only, pinned-first. */
export type ErrorEvent = ProbeResult;

/** Shape of the `favorites` entries inside an ExportPayload. */
export interface FavoriteEntry {
  provider_uuid: string | null;
  provider_name: string;
  model_ids: string[];
}

/** Shape of the export/import payload. Both `favorites` (new, uuid-keyed)
 *  and `favorites_by_provider` (legacy, name-keyed) are accepted on
 *  import. On export, both are emitted. */
export interface ExportPayload {
  providers: Array<{
    /** Stable identifier. Re-imported to preserve the provider's
     *  identity across export/import cycles. Optional on import for
     *  legacy files; the DB auto-generates one when missing. */
    uuid_id?: string;
    name: string;
    kind: ProviderKind;
    base_url: string;
    api_key: string;
    proxy: string | null;
    enabled: boolean;
    interval_seconds: number;
    timeout_seconds: number;
    headers_json: string | null;
  }>;
  favorites: FavoriteEntry[];
  favorites_by_provider: Record<string, string[]>;
  settings: Record<string, string>;
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
  getProvider: (id: string) => request<Provider>(`/api/v1/providers/${id}`),
  patchProvider: (id: string, body: Partial<Provider>) =>
    request<Provider>(`/api/v1/providers/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  syncModels: (id: string) =>
    request<ModelOut[]>(`/api/v1/providers/${id}/sync-models`, { method: "POST" }),
  runNow: (id: string) =>
    request<{ scheduled: number; skipped?: number }>(`/api/v1/providers/${id}/run`, { method: "POST" }),
  patchModel: (id: string, body: { is_favorite?: boolean; enabled?: boolean }) =>
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
  deleteProvider: (id: string) =>
    request<{ deleted: string }>(`/api/v1/providers/${id}`, { method: "DELETE" }),
  models: (id: string) => request<ModelOut[]>(`/api/v1/providers/${id}/models`),
  addModel: (providerId: string, body: { model_id: string; display_name?: string | null; type?: ModelType }) =>
    request<ModelOut>(`/api/v1/providers/${providerId}/models`, { method: "POST", body: JSON.stringify(body) }),
  results: (params: { provider_id?: string; model_id?: string; hours?: number; limit?: number } = {}) => {
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
  exportConfig: () =>
    request<ExportPayload>("/api/v1/export", { method: "POST" }),
  importConfig: (payload: ExportPayload | unknown) =>
    request<{ providers_created: number; providers_updated: number }>(
      "/api/v1/import",
      { method: "POST", body: JSON.stringify(payload) },
    ),
  probeNow: (providerId?: string, modelId?: string) => {
    const q = new URLSearchParams();
    if (providerId != null) q.set("provider_id", String(providerId));
    if (modelId != null) q.set("model_id", String(modelId));
    return request<{ scheduled: number | boolean; skipped?: number }>(
      `/api/v1/probe/run?${q.toString()}`,
      { method: "POST" },
    );
  },
  /** Schedule a fresh status check for every enabled provider + model.
   *  Returns immediately; results land asynchronously. */
  probeAll: () =>
    request<{ scheduled: number; skipped: number }>("/api/v1/probe/run-all", { method: "POST" }),
  /** Recent failure list for the error history page. Pinned
   *  errors float to the top; non-pinned are bounded by the
   *  server-side retention_days cleanup. */
  errors: (params: { limit?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.limit != null) q.set("limit", String(params.limit));
    return request<ErrorEvent[]>(`/api/v1/errors?${q.toString()}`);
  },
  pinError: (id: string) =>
    request<{ pinned: boolean }>(`/api/v1/errors/${id}/pin`, { method: "POST" }),
  unpinError: (id: string) =>
    request<{ pinned: boolean }>(`/api/v1/errors/${id}/pin`, { method: "DELETE" }),
};
