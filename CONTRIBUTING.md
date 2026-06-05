# Contributing to LLM Usability

Thanks for your interest in contributing! This document covers everything you need to get started.

## Quick links

- [Code of Conduct](CODE_OF_CONDUCT.md)
- [Security policy](SECURITY.md)
- [Architecture](docs/architecture.md)
- [API reference](docs/api.md)

## Development setup

### Prerequisites

| Tool | Version | Notes |
| --- | --- | --- |
| Python | 3.12+ | Managed by `uv` |
| uv | latest | [Install](https://docs.astral.sh/uv/) |
| Node.js | 20+ | Frontend build |
| pnpm | 9+ | Frontend deps |
| SQLite | bundled | Default storage |

### Backend

```bash
cd backend
uv sync --extra dev

# Run tests
uv run pytest

# Lint + format
uv run ruff check
uv run ruff format --check

# Type check
uv run mypy app

# Auto-migrate DB and start dev server
uv run uvicorn app.main:app --host 127.0.0.1 --port 6200 --reload
```

### Frontend

```bash
cd frontend
pnpm install

# Dev server (proxies /api to :6200)
pnpm dev

# Production build
pnpm build

# Type check (runs as part of build)
```

### End-to-end smoke

```bash
cd backend
bash scripts/e2e_smoke.sh    # uses throwaway SQLite, auto-cleanup
```

## How to contribute

1. **Open an issue first** for non-trivial changes. Describe the problem, your approach, and trade-offs.
2. **Fork the repo** and create a feature branch (`git checkout -b feature/short-description`).
3. **Write code** that matches the surrounding style. Match comment density, naming, and idioms.
4. **Add tests** for new logic. Bug fixes should include a regression test.
5. **Run the full check suite** locally before pushing:
   ```bash
   cd backend && uv run pytest && uv run ruff check
   cd ../frontend && pnpm build
   ```
6. **Write a clear commit message** explaining the *why*, not just the *what*.
7. **Open a Pull Request** and link the related issue.

## Pull request guidelines

- Keep PRs focused — one feature or fix per PR.
- Include screenshots for UI changes.
- Update the [CHANGELOG](CHANGELOG.md) under the "Unreleased" section.
- Ensure CI passes (lint, types, tests, build).
- Be responsive to review feedback.

## Coding conventions

- **Python**: type hints everywhere, follow PEP 8 (enforced by ruff), prefer `async` I/O in API/service code.
- **TypeScript**: strict mode, prefer functional components, no `any` unless unavoidable.
- **CSS**: use existing CSS variables (`--bg`, `--text`, `--ok`, etc.). Don't hardcode colors.
- **Commits**: imperative mood, present tense ("Add feature" not "Added feature").
- **Branch names**: `feature/...`, `fix/...`, `refactor/...`, `docs/...`.

## Project structure

```
backend/          FastAPI app, async SQLAlchemy 2, APScheduler
  app/
    api/          REST endpoints
    core/         config, scheduler, SSE
    db/           models, sessions, UUID helpers
    probers/      LLM provider adapters
    schemas/      Pydantic schemas
    services/     business logic
  alembic/        DB migrations
  tests/          pytest suite
frontend/         React 18 + Vite + TypeScript + Tailwind-style CSS
  src/
    api/          typed fetch wrapper
    components/   shared UI (cards, icons, toast, chart)
    hooks/        useDashboard, useSse, useTheme
    lib/          formatters, actions
    pages/        route components
docs/             architecture, API, db-migrations
```

## Reporting issues

- **Bugs**: use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.md). Include reproduction steps and environment.
- **Features**: use the [feature request template](.github/ISSUE_TEMPLATE/feature_request.md). Explain the use case.
- **Security**: see [SECURITY.md](SECURITY.md). Do **not** file public issues for security bugs.

## Getting help

Open a GitHub Discussion for questions, ideas, or general help. For bugs, use the issue tracker.
