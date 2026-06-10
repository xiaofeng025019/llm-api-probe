# Changelog

## Unreleased

### ✨ Added
- **Renamed project to LLM API Probe** across backend metadata, frontend UI, Docker assets, documentation, and default SQLite database naming; added a dedicated SVG app icon / favicon.
- **MiniMax model list fallback**: `openai_base.py` serves a hard-coded model list when `GET /v1/models` returns 404, so providers like MiniMax that don't expose the endpoint still auto-populate models.
- **Manual model add API**: `POST /api/v1/providers/{id}/models` lets users add a model by ID when auto-discovery isn't available.
- **API Key visibility toggle**: Provider dialog shows an inline eye icon to switch between password dots and plaintext; pre-fills the current key on edit.
- **Provider name editable**: Edit dialog no longer locks the name field.
- **Probe target filtering**: `chat_completion` probes only run on `chat` and `vision` models; `image`/`audio`/`embedding` models are skipped (they don't support the chat completions endpoint).
- **Sync-models records ProbeResult**: The `sync-models` endpoint now persists the `list_models` outcome so the dashboard sees fresh status immediately.
- **Loguru-based logging** with rotating file sink (`data/app.log`, 10 MB × 5 keep) per the original spec, plus stdout. `InterceptHandler` forwards all stdlib `logging` records and uvicorn's three named loggers so existing `log.info()` calls keep working unchanged.
- **SSE `provider.updated` / `model.updated` broadcasts**: previously the frontend subscribed to these events but the backend never emitted them; provider/model PATCH, POST, DELETE, and `/sync-models` now broadcast so the dashboard refreshes within ~500 ms without waiting for the 30-second poll.
- **Unified trigger queue** with priority (MANUAL > SWEEP > PERIODIC), dedup, and 503 backpressure. All 4 trigger sources (periodic scheduler, random sweep, SSE wake, manual API) submit to a single priority queue drained by a worker pool.
- **Upstream 429 Retry-After cooldown**: per-provider cooldown honored when upstream returns `Retry-After` on 429; subsequent probes are skipped until cooldown expires.
- **Auto-probe-all on startup**: fires a one-shot full sweep after restart/sleep so the dashboard sees fresh signal within seconds instead of waiting up to the max effective interval.
- **Model "stale" display status**: the dashboard demotes still-online models to "stale" when the last probe is older than `MAX_EFFECTIVE_MULTIPLIER × STALE_THRESHOLD_MULTIPLIER × base_interval` (accounts for adaptive backoff + idle throttling stretching the probe gap).

### 🔧 Changed
- **Removed local synthetic `rate_limit`**: The scheduler no longer manufactures fake `rate_limit` errors when the per-provider sliding-window limit is hit; it simply skips the probe. Only upstream-returned 429s are recorded.
- **URL builder deduplicates `/v1`**: `_make_url()` avoids double `/v1` when `base_url` already ends with it (e.g. `https://api.minimaxi.com/v1`).
- **`ProviderDialog` extracted to `components/ProviderDialog.tsx`**; the obsolete `ProvidersPage` (redirect-only since 2026-06-04) was deleted. The `/providers` route still redirects to `/` for backward-compatibility with bookmarks.
- **Settings split documented**: `app/core/config.py` (bootstrap env) and `app/services/settings.py` (runtime DB tunables) now carry mirror docstrings explaining which layer to use for new values.
- **Probe-trigger endpoints documented**: `POST /probe/run` and `POST /providers/{id}/run` now have cross-referencing docstrings clarifying the per-model vs provider-wide split; behavior unchanged.
- **Shared streaming helper** (`_streaming.py`): the 3 probers' copy-pasted ~50-line streaming + TTFB + error-mapping logic is now one function. Each prober just provides URL, body, headers, and terminator byte sequence.
- **Import/export business logic extracted** to `services/import_export.py` — per-spec merge logic is now unit-testable independent of the HTTP route.
- **Dropped 4 vestigial DB-seeded settings keys** (`default_interval_seconds`, `default_timeout_seconds`, `retention_days`). Only the env value was actually read; the DB rows were misleading dead weight.
- **Adaptive backoff streak halving**: on probe failure the streak is halved (floor 0) instead of reset to 0. A model with a 25-probe streak that gets one transient timeout stays at 12 (4× tier) instead of dropping to 1×. Consecutive failures decay naturally: 60→30→15→7→3→1→0 over 6 failures.
- **Default timeout: 30→60 seconds**. The old 30s was too aggressive for streaming models (TTFB 10-40s common); 80% of 咸鱼-MiniMax probes timed out at 30s. The 60s default is still conservative (validated ≤600s).
- **Random sweep scales by fleet size** (~5% per tick, clamped to [3, 50]) instead of a fixed 3 probes/tick — avoids over-probing single-provider setups and under-probing large fleets.
- **Log rotation uses zip compression** instead of plain rotation — cuts disk usage ~10× for rotated log files.

### 🐛 Fixed
- **MiniMax chat probes 404**: Fixed `base_url` double `/v1` causing all MiniMax chat probes to hit a non-existent endpoint.
- **Dashboard stale `list_models` status**: After clicking "Sync models" the dashboard now reflects the latest result instead of an old failure.
- **SSE broadcast skips empty subscribers**: `SseManager.broadcast()` short-circuits when there are no connected clients.
- **Docker image runtime defaults**: container image now listens on `0.0.0.0:6200` by default, exposes port 6200, and starts with `uv run --no-sync` so runtime startup does not attempt dependency installation.
- **Docker Compose project name**: compose file declares `name: llm-api-probe`, so generated networks/containers no longer inherit the old checkout directory name.
- **Docker port mismatch**: `Dockerfile.backend` hard-coded `--port 8000`, ignoring `docker-compose.yml`'s `APP_PORT=6200`; the published 127.0.0.1:6200 mapping landed on a dead port. CMD now reads `${APP_HOST}` / `${APP_PORT}` from env.
- **Session read txn released before network call**: `_run_probe` commits the implicit read transaction before the probe network call, preventing a slow upstream from starving dashboard/settings probes for the full timeout window.
- **User-facing error messages**: error history page now shows human-readable strings ("Request timed out") instead of Python `repr()` output.
- **Stale detection accounts for max effective multiplier**: a model at peak backoff (8×) + idle throttle (5×) probes at most every `base_interval × 40`; the stale check now uses this ceiling instead of the raw base interval, fixing false "stale" labels on healthy models.
- **Provider state pruned in `sync_all_jobs`**: per-provider semaphores, rate-limit buckets, and success-streak entries for deleted providers/models are now cleaned up.
- **Probe failure logs at WARNING** (was ERROR), no traceback: probe failure is steady-state, not exceptional. Every per-model-per-interval failure previously logged a full Python traceback (10-30 KB per record → 3.5 MB over 35 hours). Now a single WARNING breadcrumb with `repr(exc)` for debugging context.

### 🖥️ Frontend
- **`useSse` hardened**: JSON.parse guard on malformed SSE data, per-consumer error isolation (one bad callback doesn't kill the stream), visibility-change pause/resume.
- **"View details" button** on dashboard provider cards for explicit drill-down.
- **`ModelHealthStatus` includes `"stale"`**: TypeScript type now matches the backend's dashboard response.

### 📊 Test coverage
- **180** backend tests (pytest) — covers trigger queue, 429 cooldown, streaming log level, stale detection, interval defaults, worker pool, import/export, unique model constraints, and more.

---

## 2026-06-04 (Phase 2)

### ✨ Added
- **UUID external IDs**: Provider / Model / ProbeResult expose `uuid_id` as stable external-facing ID; internal `id` (integer) remains for FK joins. All API endpoints accept/return UUIDs.
- **Soft-delete**: `deleted_at` column on Provider and Model; `DELETE /providers/{id}` sets timestamp instead of row delete. All queries filter by `deleted_at IS NULL`.
- **Snapshot fields on ProbeResult**: `provider_name_at_probe` / `model_id_at_probe` capture state at probe time for historical integrity.
- **Partial unique index**: `ix_providers_name_active` (sqlite_where: `deleted_at IS NULL`) replaces the global unique constraint on `providers.name`, allowing same-name creation after soft-delete.
- **Import restores soft-deleted providers**: import endpoint detects soft-deleted rows and restores them directly.
- **set_favorites handles soft-deleted models**: restores soft-deleted models and re-marks as favorite.
- **3 Alembic migrations**: `0158e57e3489` (uuid + snapshot + soft-delete columns), `7ad1aec504b8` (partial unique index).

### 🔧 Changed
- **API params migrated from `int` to `uuid.UUID`**: all provider_id / model_id query params and path params.
- **Pydantic schemas use validation_alias / serialization_alias**: `uuid_id → id`, `provider_uuid → provider_id`, `model_uuid → model_id`.
- **ProbeResult gains `provider_rel` relationship + `provider_uuid` / `model_uuid` properties** for API serialization.
- **Dashboard excludes soft-deleted providers and models**.
- **Scheduler excludes soft-deleted data** from job sync and probe runs.

### 🐛 Fixed
- **Import with soft-deleted provider**: previously `patch_provider()` couldn't reach soft-deleted rows (filtered by `include_deleted=False`); now restored directly in import handler.
- **`Provider.name` unique constraint blocked recreation after soft-delete**: replaced with partial unique index.
- **`test_services.py` used `p.id` (int) as `provider_id` in `list_results`**: UUID filter silently returned empty; fixed to `p.uuid_id`.

### 📊 Test coverage
- **67** backend tests (pytest)

---

## 2026-06-04

### ✨ Added
- **Dashboard 3-card layout**: OK/Failing merged into "Available Models" (model-level availability with progress bar); Favorite Models card with progress bar and "↑N vs 24h 前" delta display.
- **Import/export favorites round-trip**: export includes `favorites_by_provider` map; import restores favorites per provider (creates placeholder model rows if needed).
- **Alembic schema migrations**: `alembic upgrade head` runs at startup; 2 revisions (`4a7be4f2d8b0` initial, `6fe570e1adbe` UTC default).
- **CLI management**: `uv run python -m app.cli list-providers` / `cleanup-test-data [--yes]`.
- **e2e smoke test**: `scripts/e2e_smoke.sh` — 14 API checks against a throwaway SQLite, auto-cleanup.
- **Dark mode** with `[data-theme="dark"]` CSS variables, persisted to localStorage.
- **40+ inline SVG icons** (no external dependency), grouped by category.
- **Animations**: CountUp tween, skeleton shimmer, stagger entrance, toast slide-in, ring progress spinner.
- **Filterable providers page**: `?status=ok|fail` and `?favorites=1` via URL params.
- **Clickable dashboard stat cards**: real `<Link>` elements (not div hacks), middle-click/right-click works.
- **SSE toast notifications**: `job.error` events surface as push toasts.

### 🔧 Changed
- **Import/export always carries `api_key`** — the `?include_keys` query param has been removed. Export files are now self-contained backups.
- **Provider card grid replaces the old table** on Dashboard and Providers pages.
- **Models table removed from Dashboard** (redundant — click any provider card to see its models).
- **`favorites_online` now uses "latest probe per model" semantics** (same as `available_models`), not "any success in window". Fixes the "Favorites all green but Available 0" inconsistency.
- **Status filter pills use `role=toolbar` + `aria-pressed`** instead of fake `role=tablist`/`tab`.

### 🐛 Fixed
- **3 probers (openai/anthropic/gemini)**: stream error responses (401/429/5xx) now return the real error code and body instead of `StreamConsumed → ErrorCode.other`.
- **probe_run validates `model.provider_id == provider_id`** — mismatched pairs return 400 instead of writing poisoned ProbeResult rows.
- **useSse** stable callback via ref (no more EventSource reopen on every re-render); onerror reconnects with exponential backoff.
- **CountUp** no longer jumps backward when value changes mid-animation.
- **Modal dialog** focus no longer stolen by SSE-driven parent re-renders.
- **SPA deep-link 404 leak**: `/api/v1/nonexistent` now returns real 404 instead of the SPA index.
- **Lifespan shutdown**: `sched.shutdown(wait=True)` before `aclose_client()` (in-flight probes no longer silently swallowed).
- **Dead code removed**: `_provider_locks` dict.
- **All user actions now have error toast feedback** via `withErrorToast()` helper.

### 📊 Test coverage
- **62** backend tests (pytest) → later upgraded to 67
- **14/14** e2e smoke steps
- mypy strict (31 files, 0 errors)
- ruff clean (check + format)

## 2026-06-03

### ✨ Initial MVP
- FastAPI backend with 18 REST endpoints + SSE event stream
- 4 prober adapters: OpenAI, OpenAI-compat, Anthropic, Gemini
- APScheduler in-process scheduling with per-provider semaphore
- SQLite WAL storage via SQLAlchemy 2 async
- React 18 + Vite + recharts frontend (5 pages)
- Docker Compose deployment
- 40 backend tests (pytest + respx)
