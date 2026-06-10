# Architecture

## Overview

LLM API Probe is a single-process monolith: one FastAPI process serves the
REST API, runs the in-process scheduler, hosts the static frontend, and
probes upstream LLM providers over HTTP.

```
┌──────────────────────────────────────────────────────────────┐
│  Browser (React SPA, served as static files by FastAPI)       │
└───────────────────────────────┬──────────────────────────────┘
                                │ HTTPS (loopback)
┌───────────────────────────────▼──────────────────────────────┐
│  FastAPI process (uvicorn @ 127.0.0.1:6200)                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐     │
│  │ REST API     │  │ APScheduler  │  │ StaticFiles      │     │
│  │ /api/v1/*    │  │ (in-process) │  │ frontend/dist/   │     │
│  └──────┬───────┘  └──────┬───────┘  └──────────────────┘     │
│         │                 │                                  │
│  ┌──────▼─────────────────▼──────────────────────────────┐   │
│  │  Service layer (async SQLAlchemy 2)                   │   │
│  └──────┬────────────────────────────────────────────────┘   │
└─────────┼──────────────────────────────────────────────────────┘
          │
          ▼
   ┌────────────────────┐         ┌──────────────────────────────┐
   │  SQLite (WAL)      │  ◀────  │  Upstream LLM providers      │
   │  data/llm_usage.db │  HTTP   │  (OpenAI, Anthropic, …)     │
   └────────────────────┘         └──────────────────────────────┘
```

## Process model

A single `uvicorn` process owns:

- The **HTTP server** (Starlette/FastAPI) bound to `127.0.0.1:6200`.
- An **APScheduler** `AsyncIOScheduler` running on the same event loop.
  Job store is SQLite (via `SQLAlchemyJobStore`) so jobs survive restarts.
- A **global `httpx.AsyncClient`** (HTTP/2, connection pool) used by all
  probers.
- A single **SSE pub/sub** (`SseManager`) that the scheduler pushes
  `probe.completed` / `job.error` events into, and `/events` subscribes to.

There is no separate worker process. This keeps the deployment model trivial
(Docker Compose, single service) at the cost of horizontal scale-out — by
design, since this is a personal-local tool.

## Concurrency

Three layers protect upstream providers from being overwhelmed:

- **Trigger queue** (priority FIFO): all 4 probe sources (periodic APScheduler job, random
  sweep, SSE wake sweep, manual API call) submit to a single `TriggerQueue` with
  MANUAL > SWEEP > PERIODIC priority. The queue deduplicates pending requests by
  (provider, model, target) key and provides 503 backpressure when full (1000 items).
- **Worker pool** (`max_concurrency` workers): drains the trigger queue. Each worker
  acquires the per-provider semaphore + root semaphore before running a probe.
- **Per-provider semaphore** + **root semaphore** (`asyncio.Semaphore`):
  - Root semaphore: global ceiling on simultaneous HTTP probes. Default `10`.
  - Per-provider semaphore: one per provider, default `1` (sequential probes
    per provider). Avoids one noisy provider saturating the global pool.

Additional cost-saving mechanisms (all in-memory, reset on restart):

- **Per-provider sliding-window rate limiter**: at most N requests in any 60s window
  per provider (default `20`). Exceeding the limit silently skips the probe.
- **Upstream 429 Retry-After cooldown**: when an upstream returns HTTP 429 with a
  `Retry-After` header, the provider enters cooldown. Subsequent probes are skipped
  until the cooldown expires (capped at 300s). Fallback of 30s when no header.
- **Adaptive backoff**: a model that succeeds N times in a row gets probed less often
  (1× → 2× → 4× → 8× of its base interval). A single failure halves the streak
  (floor 0), so a 25-probe streak → 12 after one transient timeout. Consecutive
  failures decay naturally: 60→30→15→7→3→1→0 over 6 failures.
- **Idle throttling**: when no SSE subscriber is connected, all chat-completion
  probes use a 5× interval. The moment a subscriber connects, the multiplier snaps
  to 1 and a one-shot full sweep refreshes everything.

Effective interval = base × backoff_mult × idle_mult.

## Data model

Five tables + APScheduler's internal job table.

```
providers ──┬─< models ──< probe_results
            │
            └─< probe_results (also direct FK)

settings    (single-row key/value bag for runtime config)
job_states  (last_run_at / last_status for each scheduled job)
```

### `providers`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer PK | internal FK only |
| `uuid_id` | UUID (unique) | external API ID |
| `name` | string | unique among **active** providers (partial unique index) |
| `kind` | enum | `openai` / `openai_compat` / `anthropic` / `gemini` |
| `base_url`, `api_key`, `proxy` | string | upstream config |
| `enabled` | bool | master monitoring switch |
| `interval_seconds` | int | `list_models` interval |
| `timeout_seconds` | int | per-probe timeout |
| `headers_json` | string | custom JSON headers |
| `created_at`, `updated_at`, `deleted_at` | timestamp | soft-delete via `deleted_at` |

### `models`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer PK | internal FK only |
| `uuid_id` | UUID (unique) | external API ID |
| `provider_id` | FK → `providers.id` | `ON DELETE CASCADE` |
| `model_id` | string | upstream model name (e.g. `gpt-4o`) |
| `type` | enum | `chat` / `vision` / `audio` / `image` / `embedding` / `code` / `unknown` |
| `enabled`, `is_favorite` | bool | |
| `status` | enum | `online` / `suspect` / `offline` / `rate_limited` / `unauthorized` / `not_found` / `unknown` |
| `status_reason` | string | short failure reason |
| `consecutive_failures`, `last_success_at` | | for status confirmation |
| `last_seen_at`, `deleted_at` | timestamp | |

### `probe_results`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer PK | internal FK only |
| `uuid_id` | UUID | external ID |
| `provider_id` | FK | cascade on provider delete |
| `model_id` | FK (nullable) | `null` for `list_models` probes; `SET NULL` on model delete |
| `target` | enum | `list_models` / `chat_completion` |
| `success`, `http_status` | bool, int | |
| `latency_ms`, `ttfb_ms` | int (nullable) | streaming probe only |
| `error_code` | enum | `auth` / `rate_limit` / `timeout` / `server` / `network` / `other` |
| `error_message` | string (truncated 1k) | |
| `retry_after_seconds` | int (nullable) | parsed from upstream `Retry-After` on 429 |
| `pinned` | bool | keeps error rows past retention cleanup |
| `checked_at` | timestamp | indexed |
| `provider_uuid_at_probe` | UUID snapshot | stable forever, survives hard-delete |
| `model_uuid_at_probe` | UUID snapshot (nullable) | stable forever |
| `provider_name_at_probe`, `model_id_at_probe` | string snapshot | human-readable display at probe time |

### Indexes

- `probe_results(provider_id, model_id, checked_at DESC)` — dashboard time series
- `probe_results(checked_at)` — retention cleanup
- `providers(uuid_id)` unique — API lookup
- `providers(name) WHERE deleted_at IS NULL` unique partial — soft-delete tolerant
- `models(uuid_id)` unique
- `models(provider_id, model_id)` — upsert lookup

## Soft-delete pattern

`Provider` and `Model` are soft-deleted by setting `deleted_at`. Hard delete
would cascade to `probe_results` and break historical charts. Soft-delete
keeps the model + its result history intact, hidden from active queries by
`WHERE deleted_at IS NULL`.

**Import** can revive a soft-deleted provider (detects by name, clears
`deleted_at`, applies current config). This is the same name-recycle story
described in `docs/superpowers/db-migrations.md`.

## UUID snapshot pattern (historical identity)

The `probe_results` table has TWO kinds of cross-references to its target
provider/model:

| Column | Type | Stable? | Purpose |
| --- | --- | --- | --- |
| `provider_id`, `model_id` | integer FK | ❌ reassigned on hard-delete, restore, schema re-seed | fast live JOINs |
| `provider_uuid_at_probe`, `model_uuid_at_probe` | UUID snapshot | ✅ stable forever | historical reporting |
| `provider_name_at_probe` | string snapshot | ⚠️ stable unless upstream renames | human-readable display |
| `model_id_at_probe` | string snapshot | ⚠️ stable unless upstream renames | human-readable display |

**When to use which:**

- **Live dashboards, "current state" queries** → use the integer FKs.
- **Historical reports, audit trails, cross-database restores** →
  use the UUID snapshots. The integer FK may have been NULL'd
  (`ON DELETE SET NULL` after hard-delete) or reassigned (after a
  schema re-seed); the UUID snapshot still resolves the original
  target via `find_provider_by_uuid()` / `find_model_by_uuid()`.

**Why both?**

The integer FK is convenient for the hot path (e.g. "give me all probe
results for this provider") and supports the soft-delete pattern (a
soft-deleted model row still has its old int PK, so `probe_results.model_id`
is still valid and JOINable). The UUID snapshot is the safety net for
when the int FK can no longer be trusted.

**Backfill guarantee.** When a row is inserted via `record_outcome()`, the
service code reads `provider.uuid_id` (and `model.uuid_id` if a model is
present) and writes them to the snapshot columns. For migrations of
existing data, `0158e57e3489` (initial) and `d4f8a2c6b1e3` (this one)
backfill from the live FKs at migration time.

**Hard-delete scenario.** If a model is hard-deleted (rare — usually only
via direct DB intervention), `probe_results.model_id` becomes NULL via
`ON DELETE SET NULL`, but `model_uuid_at_probe` remains on the row.
`find_model_by_uuid()` then returns None (the entity is truly gone),
but the **probe_result itself is preserved** so historical reports
can still surface it as "model: gpt-4o (since-deleted, uuid: 8c…)".

> ⚠️ The `Model.results` ORM relationship has `cascade="all, delete-orphan"`,
> which means an ORM-level `session.delete(model)` removes the related
> probe_results entirely (the DB's `ON DELETE SET NULL` never fires).
> To preserve probe_results across a model hard-delete, use a raw SQL
> `DELETE FROM models WHERE id = ?` — this bypasses the ORM cascade.

## Request flow (probe)

The 4 trigger sources all submit to a single priority queue; a worker pool drains it:

```
Trigger sources:
  1. APScheduler periodic tick (every interval)
  2. Random sweep (every 60s, ~5% of fleet)
  3. SSE wake (0→1 subscriber — full sweep)
  4. Manual API call (POST /probe/run)

Each source calls enqueue_trigger(priority, ...)
  └─→ TriggerQueue.enqueue()
        ├─ Dedup: drop if same (provider, model, target) already pending
        ├─ Priority: MANUAL > SWEEP > PERIODIC
        └─ Overflow: evict lowest-priority, return 503 if MANUAL can't fit

Worker pool (N = max_concurrency workers):
  └─→ TriggerQueue.dequeue()
        └─→ _run_probe(provider_uuid, model_uuid, target)
              ├─ _resolve_probe_target  — DB lookup + skip predicates
              ├─ commit read txn        — release before network call
              ├─ acquire root_sem, provider_sem
              ├─ _refresh_cached_settings
              ├─ _check_provider_cooldown    — skip if 429 Retry-After active
              ├─ _check_provider_rate_limit  — skip if sliding-window full
              ├─ get_prober(kind) → Prober instance
              ├─ probe.list_models(provider)  or  probe.probe_chat(...)
              │   └─ httpx request to upstream (with asyncio.wait_for watchdog)
              ├─ record_outcome(...)
              │   ├─ map HTTP status / exception → ErrorCode
              │   ├─ snapshot provider_uuid + model_uuid + names
              │   ├─ INSERT probe_results row
              │   └─ UPDATE model status (online / suspect / offline / …)
              ├─ if target == list_models and success:
              │     upsert_discovered(...)  # write discovered models
              ├─ if HTTP 429: _set_provider_cooldown(seconds)
              ├─ SseManager.broadcast("probe.completed", {...})
              ├─ if failure + favorite: SseManager.broadcast("job.error", {...})
              └─ _post_probe_admin: JobState + streak + reschedule
```

## Request flow (SSE)

```
Client GET /api/v1/events
  └─ EventSource opens persistent connection
  └─ server yields `event: ping` every 25s (keep-alive)
  └─ on probe.completed / job.error from scheduler:
        SseManager.broadcast(event, payload)
        └─ per-subscriber queue.put(payload)
        └─ EventSourceResponse reads queue, yields event to client
```

Client (`frontend/src/hooks/useSse.ts`) reconnects on error with
exponential backoff (1s → 30s cap).

## Error mapping (probers → ErrorCode)

| Source | Code |
| --- | --- |
| HTTP 401 / 403 | `auth` |
| HTTP 408 / `httpx.TimeoutException` | `timeout` |
| HTTP 429 | `rate_limit` |
| HTTP 5xx | `server` |
| `httpx.ConnectError` / `RemoteProtocolError` | `network` |
| Other 4xx | `other` |
| Stream error (e.g. `StreamConsumed`) | `other` (or the upstream status if read) |

## Status confirmation (model health)

After each probe, the model is updated:

- `success` and HTTP 2xx → `online`. Consecutive failures reset to 0.
- `consecutive_failures` increments on failure, resets on success.
- `status_confirmed_at` records when the current status was last corroborated.
- Status thresholds (favorite: 2 failures → `offline`, regular: 3 failures → `offline`)
  are runtime-tunable via the Settings page.

### Model statuses

| Status | Meaning |
| --- | --- |
| `online` | Latest probe succeeded |
| `stale` | Status is `online` but last probe is older than `MAX_EFFECTIVE_MULTIPLIER × STALE_THRESHOLD_MULTIPLIER × base_interval` (display-only; not persisted) |
| `suspect` | One or more failures, but below the `offline` threshold |
| `offline` | Consecutive failures ≥ threshold |
| `rate_limited` | Upstream returned HTTP 429 |
| `unauthorized` | Upstream returned HTTP 401 / 403 |
| `not_found` | Upstream returned HTTP 404 or "model not found" |
| `unknown` | Never probed yet |

## Adaptive backoff + idle throttling

Two complementary cost-saving mechanisms, both purely in-memory (reset on restart):

### Adaptive backoff

| Streak | Multiplier |
| --- | --- |
| 0–2 | 1× (base interval; recent fresh signal) |
| 3–9 | 2× (mildly stable) |
| 10–29 | 4× (very stable) |
| 30+ | 8× (rock-solid; capped) |

On success: streak +1. On failure: streak halved (floor 0). Halving preserves
built-up trust — a model with a 25-probe streak at 4× stays at 12 (still 4×)
after one transient timeout. Consecutive failures decay naturally:
60→30→15→7→3→1→0 over 6 failures.

### Idle throttling

When no SSE subscriber is connected (nobody watching the dashboard), all
chat-completion probes use a 5× interval multiplier. On first subscriber
connect, multiplier snaps to 1× and a one-shot full sweep refreshes all
providers. On last disconnect, already-scheduled jobs are pushed further out
by `IDLE_MULTIPLIER × remaining_gap`.

Both toggles (`adaptive_backoff_enabled`, `idle_throttle_enabled`) are
runtime-tunable via the Settings page.

### Effective interval

```
effective = base_interval × backoff_multiplier × idle_multiplier
```

Post-probe reschedule uses the effective interval + 10% jitter (min 5s) to
spread batch probes across time.

## Frontend architecture

- **Single SPA** built with Vite. No router state library; `react-router-dom`
  with `BrowserRouter` + `Routes`.
- **Server state**: hand-rolled `useDashboard` hook (no TanStack Query).
  Polls `/dashboard` every 30s + reacts to SSE `probe.completed` / `model.updated`
  / `provider.updated` by re-fetching.
- **Theme**: `data-theme` attribute on `<html>`, persisted to `localStorage`,
  respects `prefers-color-scheme` on first load.
- **Type contract**: `frontend/src/api/types.ts` is the single source of
  truth for backend response shapes. Pydantic ↔ TypeScript drift is
  detected at `pnpm build` time (the build runs `tsc` first).

## Deployment

Docker Compose runs one service:

```yaml
services:
  app:
    build: .
    ports: ["127.0.0.1:6200:6200"]    # loopback only
    volumes:
      - ./data:/app/data              # SQLite
      - ./.env:/app/.env:ro
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:6200/api/v1/healthz"]
      interval: 30s
```

The image is built from `Dockerfile.backend` (multi-stage: builder with `uv`
to install deps, runtime with minimal Python image). The frontend is built
locally before `docker build` (or in CI) and the static assets are copied
into the image at `/app/frontend/dist`.

On startup, `app/db/__init__.py:init_db` runs `alembic upgrade head`
synchronously (SQLite needs the schema before accepting requests).

## Why a single process?

- **Simplicity**: one binary, one DB file, one port. Docker Compose is a
  one-liner. No reverse proxy needed for local use.
- **Determinism**: scheduler and HTTP share an event loop; no cross-process
  locking needed for in-memory semaphores.
- **No background workers**: no need for Redis / Celery / RQ.

Trade-offs accepted:

- No horizontal scale-out (one process, one machine).
- SQLite write throughput caps at roughly hundreds of writes/s — comfortable
  for a personal monitor, not a fleet.
- A bug in the scheduler can take down the API. Mitigated by `lifespan`
  cleanup and `wait=True` on shutdown.

## Non-goals (architectural)

These were considered and rejected (see [README YAGNI list](README.md)):

- Authentication / multi-user: adds a session layer for no current value.
- Distributed probes: needs a queue and external store.
- Live reconfiguration without restart: scheduler state is rebuilt on boot
  from the DB anyway, so restarts are cheap.
