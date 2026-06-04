# Repository Guidelines

## Project Structure & Module Organization

This repository is a local LLM provider availability monitor with a Python backend and React frontend.

- `backend/app/` contains the FastAPI application, grouped by `api/`, `core/`, `db/`, `schemas/`, `services/`, and `probers/`.
- `backend/tests/` contains pytest coverage for API routes, services, scheduler behavior, probers, and smoke tests.
- `backend/alembic/` holds database migration configuration and versioned migration scripts.
- `frontend/src/` contains the Vite React app: pages in `pages/`, shared UI in `components/`, hooks in `hooks/`, and helpers in `lib/`.
- `docs/superpowers/` contains design and migration documentation.

## Build, Test, and Development Commands

- `cd backend && uv sync` installs backend dependencies.
- `cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload` runs the backend API in development mode.
- `cd frontend && pnpm install` installs frontend dependencies.
- `cd frontend && pnpm dev` runs Vite at `http://localhost:5173` with `/api` proxied to the backend.
- `cd frontend && pnpm build` type-checks and builds the production frontend bundle.
- `docker compose up -d` runs the packaged local service.

## Coding Style & Naming Conventions

Backend code targets Python 3.12. Use Ruff with a 110-character line length and sorted imports. Prefer explicit async SQLAlchemy and service-layer logic over route-level database work. Name pytest files `test_*.py`.

Frontend code uses TypeScript and React function components. Use `PascalCase` for components/pages, `camelCase` for hooks/helpers, and keep API types in `frontend/src/api/types.ts`.

## Testing Guidelines

Run backend checks before changing API, database, scheduler, or prober behavior:

```bash
cd backend
uv run pytest
uv run ruff check app tests
uv run ruff format --check app tests
uv run mypy app
```

Use `bash scripts/e2e_smoke.sh` from `backend/` for API and SPA smoke coverage against a temporary SQLite database. Avoid manual `curl` tests against `data/llm_usability.db`; they can pollute local user data.

## Commit & Pull Request Guidelines

Recent commits use Conventional Commits with scopes, such as `feat(dashboard): ...`, `fix(frontend+backend): ...`, and `refactor(dashboard): ...`. Keep messages concise.

Pull requests should include a short description, linked context when relevant, test commands run, and screenshots for visible frontend changes. Include Alembic migrations with any `backend/app/db/models.py` schema change.

## Security & Configuration Tips

Configuration is read from `.env` and runtime settings are stored in SQLite. API keys are stored in plaintext by design for local use, so keep the service bound to `127.0.0.1` unless adding external authentication through a reverse proxy.
