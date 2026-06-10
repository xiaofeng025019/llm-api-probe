# API Reference

Base URL: `http://127.0.0.1:6200/api/v1`

All endpoints return a unified envelope:

```json
{
  "data": <payload> | null,
  "error": { "code": "string", "message": "string", "details": ... } | null
}
```

Errors are also reflected in HTTP status:

| Status | Meaning |
| --- | --- |
| `200` | Success |
| `201` | Resource created |
| `400` | Validation error (bad request body / query param) |
| `404` | Resource not found |
| `409` | Conflict (e.g. duplicate name) |
| `422` | Pydantic validation error |
| `502` | Upstream provider failed |
| `500` | Internal server error |

The FastAPI auto-generated OpenAPI schema is at `http://127.0.0.1:6200/docs`
(Swagger UI) and `http://127.0.0.1:6200/openapi.json` (raw schema).

---

## Health

### `GET /healthz`

Liveness probe. Returns immediately.

```json
{ "status": "ok" }
```

### `GET /readyz`

Readiness probe. Same shape.

---

## Dashboard

### `GET /dashboard`

Aggregated overview: per-provider health, favorite models, error counts.

Returns: `Dashboard` schema — see [Schemas](#schemas) below.

---

## Providers

### `GET /providers`

List active (non-soft-deleted) providers, sorted by name.

### `POST /providers`

Create a new provider.

Request body (`ProviderCreate`):

```json
{
  "name": "openai-prod",
  "kind": "openai",
  "base_url": "https://api.openai.com",
  "api_key": "sk-...",
  "proxy": null,
  "enabled": true,
  "interval_seconds": 300,
  "timeout_seconds": 60,
  "headers_json": null
}
```

`kind` must be one of: `openai`, `openai_compat`, `anthropic`, `gemini`.

Returns `201` with the created `Provider`.

### `GET /providers/{provider_id}`

`provider_id` is a UUID.

### `PATCH /providers/{provider_id}`

All fields optional; only provided ones are updated. Re-syncs the scheduler for
that provider on success.

### `DELETE /providers/{provider_id}`

Soft-delete: sets `deleted_at = now()`. The row and its historical probe
results stay in the database. Removes all scheduler jobs for the provider.

### `POST /providers/{provider_id}/sync-models`

Calls the upstream `list_models` endpoint inline, upserts discovered models,
returns the current model list. Returns `502` if the upstream call fails.

### `GET /providers/{provider_id}/models`

List active (non-soft-deleted) models for a provider, sorted by `model_id`.

### `POST /providers/{provider_id}/run`

Trigger a `list_models` probe immediately. Returns `{ "data": { "scheduled": true } }`.

Returns `409` if the provider is disabled (or its `list_models` job is paused).

---

## Models

### `PATCH /models/{model_id}`

Toggle `enabled` and/or `is_favorite`.

```json
{ "is_favorite": true }
```

Re-syncs scheduler for the owning provider on success.

---

## Results

### `GET /results`

List probe results, newest first.

Query params:

| Param | Type | Default | Notes |
| --- | --- | --- | --- |
| `provider_id` | UUID | — | filter to one provider |
| `model_id` | UUID | — | filter to one model |
| `hours` | int (1–720) | `24` | time window |
| `limit` | int (1–1000) | `200` | max rows returned |

---

## Settings

### `GET /settings`

List all key/value rows. `value` is always a string on the wire.

### `PUT /settings`

Bulk upsert. Request body:

```json
{
  "items": {
    "retention_days": "30",
    "max_concurrency": "10",
    "favorite_model_interval_seconds": "300"
  }
}
```

Re-syncs scheduler intervals on success.

---

## Import / Export

### `POST /export`

Generate a full backup of providers, settings, and favorite model IDs.

Response (`ExportPayload`):

```json
{
  "providers": [
    {
      "name": "openai-prod",
      "kind": "openai",
      "base_url": "https://api.openai.com",
      "api_key": "sk-...",
      "proxy": null,
      "enabled": true,
      "interval_seconds": 300,
      "timeout_seconds": 60,
      "headers_json": null
    }
  ],
  "favorites_by_provider": { "openai-prod": ["gpt-4o", "gpt-4o-mini"] },
  "settings": { "retention_days": "30" }
}
```

### `POST /import`

Restore from an export payload.

Semantics:

- **Existing active provider** (name match) → patched with new fields.
- **Soft-deleted provider** (name match) → restored; `deleted_at` cleared.
- **New provider** → created (requires `api_key` in payload).
- **Favorites** are restored after providers exist. Missing model rows
  become placeholders; the next `/sync-models` fills in the real type.
- **Settings** are upserted.
- `probe_results` are never imported.

Response:

```json
{
  "providers_created": 0,
  "providers_updated": 2,
  "favorites_restored": 3
}
```

---

## Probe trigger

### `POST /probe/run`

Manually trigger a probe for a provider (or one specific model).

Query params:

| Param | Type | Notes |
| --- | --- |
| `provider_id` | UUID | required |
| `model_id` | UUID | optional — if omitted, runs `list_models` only |

Returns `404` if the provider/model is missing, `409` if disabled.

---

## SSE events

### `GET /events`

Server-Sent Events stream. Keep the connection open.

Initial frame (on connect):

```
event: ping
data: {"ts": "2026-06-04T07:30:00.000Z"}
```

Then a heartbeat `ping` every ~25s.

#### `probe.completed`

```json
{
  "id": "uuid",
  "provider_id": "uuid",
  "model_id": "uuid" | null,
  "target": "list_models" | "chat_completion",
  "success": true,
  "http_status": 200,
  "latency_ms": 1234,
  "ttfb_ms": 567,
  "error_code": null,
  "checked_at": "2026-06-04T07:30:00.000Z"
}
```

#### `provider.updated`

```json
{ "id": "uuid", "...": "..." }
```

#### `model.updated`

```json
{ "id": "uuid", "...": "..." }
```

#### `job.error`

```json
{
  "provider_id": "uuid",
  "model_id": "uuid" | null,
  "message": "string"
}
```

---

## Schemas

All schema fields are exposed in the OpenAPI document. The high-level shape:

### `Provider`

```ts
{
  id: string;        // UUID
  name: string;
  kind: "openai" | "openai_compat" | "anthropic" | "gemini";
  base_url: string;
  enabled: boolean;
  interval_seconds: number;
  timeout_seconds: number;
  proxy: string | null;
  headers_json: string | null;
  created_at: string;  // ISO 8601
  updated_at: string;
}
```

### `ModelOut`

```ts
{
  id: string;        // UUID
  provider_id: string; // UUID
  model_id: string;   // upstream identifier
  display_name: string | null;
  type: "chat" | "vision" | "audio" | "image" | "embedding" | "code" | "unknown";
  enabled: boolean;
  is_favorite: boolean;
  last_seen_at: string;
  status: "online" | "suspect" | "offline" | "rate_limited" | "unauthorized" | "not_found" | "unknown";
  status_reason: string | null;
  status_checked_at: string | null;
  status_confirmed_at: string | null;
  last_success_at: string | null;
  consecutive_failures: number;
}
```

### `ProbeResult`

```ts
{
  id: string;
  provider_id: string;
  model_id: string | null;
  target: "list_models" | "chat_completion";
  success: boolean;
  http_status: number | null;
  latency_ms: number | null;
  ttfb_ms: number | null;
  error_code: "auth" | "rate_limit" | "timeout" | "server" | "network" | "other" | null;
  error_message: string | null;
  checked_at: string;
  provider_uuid_at_probe: string;
  model_uuid_at_probe: string | null;
  provider_name_at_probe: string;
  model_id_at_probe: string | null;
  pinned: boolean;
}
```

### `Dashboard`

```ts
{
  providers: DashboardProvider[];
  totals: {
    providers: number;
    models: number;
    ok: number;
    degraded?: number;
    failing: number;
    available_models?: number;
    favorites_online: number;
    favorites_total: number;
    favorites_online_24h_ago?: number;
  };
}
```

See `frontend/src/api/types.ts` for the canonical TypeScript shapes.

---

## Frontend client

The frontend uses a thin fetch wrapper in `frontend/src/api/types.ts`:

```ts
api.dashboard()                        // → Dashboard
api.providers()                        // → Provider[]
api.getProvider(id)                     // → Provider
api.patchProvider(id, body)             // → Provider
api.createProvider(body)                // → Provider
api.deleteProvider(id)                 // → { deleted: id }
api.syncModels(id)                      // → ModelOut[]
api.runNow(id)                          // → { scheduled: true }
api.models(id)                          // → ModelOut[]
api.patchModel(id, body)                // → ModelOut
api.results({ provider_id, model_id, hours, limit })
api.settings()                          // → Setting[]
api.putSettings({ items })
api.exportConfig()                      // → ExportPayload
api.importConfig(payload)               // → { providers_created, providers_updated, favorites_restored }
api.probeNow(providerId, modelId?)
```
