# LLM API Probe

Local LLM API provider availability monitor. Browser dashboard for real-time status of every provider and model, with focused monitoring of your favorites and 24h / 7d / 30d trend charts.

## Highlights

- **Multi-provider**: OpenAI, OpenAI-compatible (DeepSeek / 硅基流动 / 豆包 / …), Anthropic, Google Gemini
- **Active probing**: `list_models` + streaming `chat_completion`, recording status, latency, TTFB, error code
- **Favorites**: pin the models you care about; gets a faster probe interval
- **Trend charts**: 1h / 24h / 7d / 30d windows (Recharts)
- **Import / Export**: JSON config with API keys + favorites, full round-trip
- **SSE event stream**: `probe.completed` / `provider.updated` / `model.updated` / `job.error` (auto-reconnect, exponential backoff)
- **Local-first**: single SQLite file, no external services, bound to `127.0.0.1` by default

> Design spec: [`docs/superpowers/specs/2026-06-03-llm-api-probe-design.md`](docs/superpowers/specs/2026-06-03-llm-api-probe-design.md)
> Architecture: [`docs/architecture.md`](docs/architecture.md)
> API reference: [`docs/api.md`](docs/api.md)
> Changelog: [`CHANGELOG.md`](CHANGELOG.md)

## Quick start

### Docker Compose (recommended)

```bash
git clone <repo-url> llm-api-probe
cd llm-api-probe
cp .env.example .env          # optional
docker compose up -d
# open http://127.0.0.1:6200
```

Data persists in `./data/llm_api_probe.db`; scheduler state in the same file.

### Bare metal

```bash
# Backend
cd backend
uv sync

# Frontend
cd ../frontend
pnpm install
pnpm build
cd ..

# Run
cd backend
uv run uvicorn app.main:app --host 127.0.0.1 --port 6200
```

### Development mode (hot reload)

```bash
# Terminal 1
cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 6200 --reload

# Terminal 2
cd frontend && pnpm dev
# Vite at http://localhost:5173, proxies /api to :6200
```

## Tech stack

| Layer | Stack |
| --- | --- |
| Backend | Python 3.12, FastAPI, SQLAlchemy 2 (async), Alembic, APScheduler, httpx, Pydantic v2, loguru, uvicorn |
| Storage | SQLite WAL (`sqlite+aiosqlite://`) |
| Frontend | React 18, Vite, TypeScript, react-router v6, Recharts, Geist Sans/Mono |
| Tooling | `uv` (backend), `pnpm` (frontend), ruff, mypy, pytest, respx |
| Deploy | Single-container Docker Compose |

## Project layout

```
llm-api-probe/
├── backend/             # FastAPI app
│   ├── app/
│   │   ├── api/         # REST endpoints
│   │   ├── core/        # config, scheduler, SSE
│   │   ├── db/          # models, sessions, UUID helpers
│   │   ├── probers/     # LLM provider adapters
│   │   ├── schemas/     # Pydantic schemas
│   │   └── services/    # business logic
│   ├── alembic/         # DB migrations
│   ├── scripts/         # e2e smoke
│   └── tests/           # pytest suite (180 tests)
├── frontend/            # React + Vite
│   └── src/
│       ├── api/         # typed fetch wrapper
│       ├── components/  # shared UI
│       ├── hooks/       # useDashboard, useSse, useTheme
│       ├── lib/         # formatters, actions
│       └── pages/       # route components
├── docs/                # architecture, API, design spec
├── .github/             # CI workflow, issue + PR templates
├── docker-compose.yml
├── Dockerfile.backend
├── .env.example
├── AGENTS.md            # repo-wide agent guidelines
├── CONTRIBUTING.md
├── CODE_OF_CONDUCT.md
├── SECURITY.md
└── LICENSE
```

## Configuration

All config comes from `.env` (template: `.env.example`). Runtime-tunable values
(`retention_days`, `max_concurrency`, favorite / regular model intervals) live in
the `settings` table and are editable from the **Settings** page.

## API overview

All endpoints under `/api/v1`. Response envelope: `{data, error}`.

| Method | Path | Notes |
| --- | --- |
| `GET`    | `/healthz` / `/readyz` | liveness / readiness |
| `GET`    | `/dashboard` | aggregated overview |
| `GET/POST/PATCH/DELETE` | `/providers[/{id}]` | provider CRUD |
| `POST`   | `/providers/{id}/sync-models` | manual model list refresh |
| `POST`   | `/providers/{id}/run` | immediate probe |
| `GET`    | `/providers/{id}/models` | list models for a provider |
| `PATCH`  | `/models/{id}` | toggle enabled / favorite |
| `GET`    | `/results?provider_id&model_id&hours&limit` | probe history |
| `GET/PUT` | `/settings` | global config |
| `POST`   | `/import` / `/export` | config backup + restore |
| `POST`   | `/probe/run?provider_id&model_id` | trigger probe |
| `GET`    | `/events` | **SSE** event stream |

See [`docs/api.md`](docs/api.md) for full request/response shapes.

## Testing

```bash
# Backend
cd backend
uv run pytest -q               # 180 tests
uv run ruff check
uv run ruff format --check
uv run mypy app

# Frontend (type-check + production build)
cd ../frontend
pnpm build

# End-to-end smoke (uses throwaway SQLite, auto-cleanup)
cd ../backend
bash scripts/e2e_smoke.sh
```

CI runs all of the above on every PR. See `.github/workflows/ci.yml`.

## Security

- API keys are stored **in plaintext** in SQLite by design (local-first use case).
- Default bind is `127.0.0.1`; **do not** expose to a public network without a reverse proxy + BasicAuth.
- API keys are truncated to `sk-…xxxx` in logs.
- See [SECURITY.md](SECURITY.md) for the full security model and disclosure process.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, conventions, and the PR process.
All participants are expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Limitations (YAGNI)

These are intentionally not built. Don't add them unless explicitly asked:

- Email / Slack / Webhook notifications
- Multi-user / authentication
- Distributed workers (Celery + Redis)
- Active fail-over / request rerouting
- Mobile client

## License

[MIT](LICENSE)
