# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## Current status

**Implemented MVP** — FastAPI backend (67 pytest passing), React frontend (Vite + TS), Alembic migrations, Docker Compose. See `CHANGELOG.md` for release history.

## Where to look

| Need | File |
| --- | --- |
| Repo conventions (build, test, naming) | `AGENTS.md` |
| Full design spec | `docs/superpowers/specs/2026-06-03-llm-usability-design.md` |
| Architecture, data model | `docs/architecture.md` (see TODO) |
| API reference | `docs/api.md` (see TODO) |
| DB migration history | `docs/superpowers/db-migrations.md` |
| Recent changes | `CHANGELOG.md` |
| How to contribute | `CONTRIBUTING.md` |
| Security disclosures | `SECURITY.md` |

## Hard rules

- **Never** commit secrets, real API keys, or the local `data/` SQLite file. `.env` is git-ignored; `.env.example` is the source of truth for config shape.
- **Never** mutate `backend/app/db/models.py` without a paired Alembic migration in `backend/alembic/versions/`.
- **Never** edit `CHANGELOG.md` for already-released entries — only add a new "Unreleased" section.
- **Never** push to `main` directly — open a PR so CI runs.
- **Never** delete `backend/alembic/versions/*.py` once committed — it will break other developers' local DBs.

## Conventions specific to this repo

- Commit messages: `Conventional Commits` with scope (`feat(backend): ...`, `fix(frontend): ...`).
- Backend changes that touch the schema **must** ship a migration in the same commit.
- Frontend public API: types live in `frontend/src/api/types.ts`. Update the type and any call sites in the same change.
- Hardcoded colors or hex values in `.tsx`/`.ts` are a smell — use the CSS variables defined in `frontend/src/styles.css`.
- API responses use the `{data, error}` envelope — never return raw JSON from a new endpoint.

## Common tasks

### Run tests

```bash
# Backend
cd backend && uv run pytest -q

# Frontend (type-check + build)
cd frontend && pnpm build
```

### Add a new endpoint

1. Implement the service in `backend/app/services/`.
2. Add the route in `backend/app/api/v1/`.
3. Add a Pydantic schema in `backend/app/schemas/api.py`.
4. Write a pytest in `backend/tests/`.
5. Add a frontend type + `api` call in `frontend/src/api/types.ts`.
6. Add a CHANGELOG entry under "Unreleased".

### Add a new prober

1. Implement the `Prober` Protocol in `backend/app/probers/`.
2. Register it in `backend/app/probers/__init__.py:get_prober`.
3. Add `ProviderKind` enum entry in `backend/app/db/models.py` if needed.
4. Add tests in `backend/tests/test_probers.py`.

## Project memory

This repo's design and intent are documented in `docs/superpowers/specs/2026-06-03-llm-usability-design.md`. When in doubt, that spec wins.
