# Changelog

## Unreleased

### ✨ Added
- **MiniMax model list fallback**: `openai_base.py` serves a hard-coded model list when `GET /v1/models` returns 404, so providers like MiniMax that don't expose the endpoint still auto-populate models.
- **Manual model add API**: `POST /api/v1/providers/{id}/models` lets users add a model by ID when auto-discovery isn't available.
- **API Key visibility toggle**: Provider dialog shows an inline eye icon to switch between password dots and plaintext; pre-fills the current key on edit.
- **Provider name editable**: Edit dialog no longer locks the name field.
- **Probe target filtering**: `chat_completion` probes only run on `chat` and `vision` models; `image`/`audio`/`embedding` models are skipped (they don't support the chat completions endpoint).
- **Sync-models records ProbeResult**: The `sync-models` endpoint now persists the `list_models` outcome so the dashboard sees fresh status immediately.

### 🔧 Changed
- **Removed local synthetic `rate_limit`**: The scheduler no longer manufactures fake `rate_limit` errors when the per-provider sliding-window limit is hit; it simply skips the probe. Only upstream-returned 429s are recorded.
- **URL builder deduplicates `/v1`**: `_make_url()` avoids double `/v1` when `base_url` already ends with it (e.g. `https://api.minimaxi.com/v1`).

### 🐛 Fixed
- **MiniMax chat probes 404**: Fixed `base_url` double `/v1` causing all MiniMax chat probes to hit a non-existent endpoint.
- **Dashboard stale `list_models` status**: After clicking "Sync models" the dashboard now reflects the latest result instead of an old failure.
- **SSE broadcast skips empty subscribers**: `SseManager.broadcast()` short-circuits when there are no connected clients.

### 📊 Test coverage
- **90** backend tests (pytest)

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
