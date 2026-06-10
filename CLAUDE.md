# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current status

**Implemented MVP** — FastAPI backend + React frontend (Vite + TS), Alembic migrations, Docker Compose, single-process monolith. See `CHANGELOG.md` for release history.

## Where to look

| Need | File |
| --- | --- |
| Repo conventions (build, test, naming, PR process) | `AGENTS.md` |
| Full design spec | `docs/superpowers/specs/2026-06-03-llm-api-probe-design.md` |
| Architecture / process model / data flow | `docs/architecture.md` |
| API reference (envelope, endpoints, status codes) | `docs/api.md` |
| DB migration history | `docs/superpowers/db-migrations.md` |
| User-facing docs (quick start, screenshots) | `README.md` |
| Recent changes | `CHANGELOG.md` |
| How to contribute | `CONTRIBUTING.md` |
| Security disclosures | `SECURITY.md` |

## Architecture (mental model)

Single `uvicorn` process on `127.0.0.1:6200` that owns the HTTP server, the in-process `AsyncIOScheduler`, a shared `httpx.AsyncClient`, the SSE pub/sub broker, and a mount of the built React bundle (SPA fallback for non-`/api` paths). See `docs/architecture.md` for the diagram; the non-obvious conventions worth pinning:

- **Two-tier config.** `app/core/config.py:Settings` is the bootstrap layer (env / `.env`, read once at import — host/port, DB URL, `max_concurrency`, timezone, `frontend_dist`). `app.services.settings` reads/writes the `settings` DB table for runtime tunables (probe intervals, rate limit, the backoff/idle toggles, `retention_days`). Three keys (`default_interval_seconds`, `default_timeout_seconds`, `retention_days`) are seeded into the `settings` table on first launch but only the env value is actually read by the code — the DB rows are vestigial. Don't add new dual-write keys without a stronger reason.
- **Stable API identity via `uuid_id`.** The entity tables (`Provider`, `Model`, `ProbeResult`) have both an `int` PK and a separate `uuid_id`; the API uses `uuid_id` exclusively. `Setting` (string PK = `key`) and `JobState` (string PK = `job_key`, internal-only) are the exceptions. `ProbeResult` additionally stores `provider_uuid_at_probe` / `model_uuid_at_probe` snapshots so historical reporting keeps working after a hard delete or a re-create with reassigned int PKs — query the snapshot column, not the live FK.
- **Scheduler cost-saving.** `app/core/scheduler.py` layers three mechanisms on a plain `IntervalTrigger` (see the file for the exact curves and triggers):
  - **Adaptive backoff** — consecutive-success streak → 1×→2×→4×→8× interval multiplier; one failure halves the streak (floor 0, degrades toward 1×).
  - **Idle throttling** — no SSE subscriber → 5× interval. The actual handlers are `_on_sse_wake` / `_on_sse_sleep` in `app/core/scheduler.py`; `init_sse_hooks()` wires them via `SseManager.set_lifecycle_hooks` (which is just a setter). First-subscriber fires a one-shot full sweep, last-unsubscribe pushes already-scheduled jobs out.
  - **Random continuous sweep** — a background job picks a few random enabled models each tick so the dashboard stays fresh between scheduled fires.

  All multipliers are runtime-tunable; the cached toggles are refreshed on every probe, so a Settings save propagates on the next probe (bounded by the 60s random sweep, not the modified model's own interval).
- **Concurrency.** The 4 trigger sources (periodic APScheduler, random sweep, SSE wake, manual API) all submit to a unified `TriggerQueue` (MANUAL > SWEEP > PERIODIC priority, dedup by key, 503 backpressure at 1000 items). A worker pool (`max_concurrency` workers) drains the queue. Each probe acquires a root semaphore (`max_concurrency`) and a per-provider semaphore (default 1). A per-provider sliding-window rate limiter caps requests per minute, and a per-provider 429 Retry-After cooldown skips probes after an upstream rate-limit response — see `scheduler.py` for the gate and the bucket.
- **SSE.** Probe completion fans out via `SseManager.broadcast` on `/api/v1/events`; the dashboard coalesces updates at ~500ms (see `useSse` + `useDashboard`). A `job.error` event is broadcast only for failed favorite-model probes and surfaces as a global toast.
- **Prober Protocol.** All provider integrations implement the `Prober` Protocol (`app/probers/types.py`) and are wired via the `get_prober` factory — never import a prober class directly from a route.
- **Job IDs.** `p{provider_uuid}:{target}:m{model_uuid_or_-}` — used by `sync_jobs_for_provider` for reconciliation and by `trigger_now` to fast-forward via `modify_job`. The `background:random_probe_sweep` job is the only one without that prefix.
- **Lifespan (`main.py`).** Startup: start scheduler → reconcile jobs → install SSE hooks → fire-and-forget startup probe-all so a freshly restarted process shows fresh signal within seconds. Shutdown must drain in-flight probes before closing the httpx client — don't change the order.
- **Static mount.** `SPAStaticFiles` is registered **after** the API router. Extensionless paths fall back to `index.html` (SPA deep links); paths with an unknown extension 404.

## Hard rules

- **Never** commit secrets, real API keys, or the local `data/` SQLite file. `.env` is git-ignored; `.env.example` is the source of truth for config shape.
- **Never** mutate `backend/app/db/models.py` without a paired Alembic migration in `backend/alembic/versions/`. Once committed, **never delete a migration file** — it will break other developers' local DBs.
- **Never** edit `CHANGELOG.md` for already-released entries — only add a new "Unreleased" section.
- **Never** push to `main` directly — open a PR so CI runs.
- **Never** run ad-hoc HTTP smoke tests against `http://127.0.0.1:6200/...` that pollute local user data — use `bash backend/scripts/e2e_smoke.sh` instead (it spins up a throwaway SQLite DB and tears it down on exit).
- **Never** store API keys in env vars at runtime — they live in SQLite. See `SECURITY.md` for the rationale.

## Conventions specific to this repo

- Commit messages: Conventional Commits with scope (`feat(backend): ...`, `fix(frontend): ...`).
- Backend changes that touch the schema **must** ship a migration in the same commit. Schema-only renames are forbidden; add a new column, dual-write, then drop.
- Frontend public API: types live in `frontend/src/api/types.ts`. Update the type and any call sites in the same change.
- Hardcoded colors or hex values in `.tsx`/`.ts` are a smell — use the CSS variables defined in `frontend/src/styles.css`.
- API responses use the `{data, error}` envelope — never return raw JSON from a new endpoint. (The infrastructure endpoints `/api/v1/healthz` and `/api/v1/readyz` in `main.py` are the only exceptions; they return `{"status": "ok"}` directly.) The `ValueError` exception handler in `main.py` is a shortcut that maps to `400` with that envelope; prefer raising `HTTPException` for non-400s.
- Backend code targets Python 3.12. Ruff (line length 110, sorted imports, the rule set in `backend/pyproject.toml`). mypy `strict_optional`, `warn_unused_ignores`. Prefer explicit async SQLAlchemy + service-layer logic over route-level DB work. pytest is async-mode auto.
- Frontend: TypeScript, React function components, `PascalCase` for components/pages, `camelCase` for hooks/helpers.

## Common tasks

### Run tests / lint

```bash
# Backend — all at once
cd backend && uv sync && uv run pytest -q

# Backend — single test (by keyword, no nodeid needed)
cd backend && uv sync && uv run pytest -k idle -q

# Backend — lint + type-check (CI runs these as three independent steps)
cd backend && uv run ruff check
cd backend && uv run ruff format --check
cd backend && uv run mypy app

# Frontend — type-check + production build (CI uses this)
cd frontend && pnpm install --frozen-lockfile && pnpm build

# End-to-end smoke (throwaway SQLite on a second uvicorn at :18700, auto-cleanup)
bash backend/scripts/e2e_smoke.sh  # override port with `PORT=...`
```

### Run the app

```bash
# Dev mode (two terminals)
# Terminal 1: backend on :6200
cd backend  && uv run uvicorn app.main:app --host 127.0.0.1 --port 6200 --reload
# Terminal 2: Vite on :5173 (proxies /api to :6200)
cd frontend && pnpm dev
```

### Add a new endpoint

1. Implement the service in `backend/app/services/`.
2. Add the route in `backend/app/api/v1/` and include the router in `__init__.py` if it's a new file.
3. Add a Pydantic schema in `backend/app/schemas/api.py` (request/response shapes stay inside the `{data, error}` envelope).
4. Write a pytest in `backend/tests/`.
5. Add a frontend type + `api` call in `frontend/src/api/types.ts`.
6. Add a CHANGELOG entry under "Unreleased".

### Add a new prober

1. Implement the `Prober` Protocol in `backend/app/probers/`.
2. Register it in `backend/app/probers/__init__.py:get_prober`.
3. Add a `ProviderKind` enum entry in `backend/app/db/models.py` if a new SQLAlchemy-mapped column needs it (SQLite stores the enum as VARCHAR, so a code-only enum addition does **not** need a migration).
4. Add tests in `backend/tests/test_probers.py`.

### Clean up local test data

```bash
cd backend
uv run python -m app.cli cleanup-test-data      # dry-run; lists matches
uv run python -m app.cli cleanup-test-data --yes # actually delete
```

The CLI auto-runs `alembic upgrade head` first, so it's safe to invoke before the app has ever started.

## Project memory

This repo's design and intent are documented in `docs/superpowers/specs/2026-06-03-llm-api-probe-design.md`. When in doubt, that spec wins.
