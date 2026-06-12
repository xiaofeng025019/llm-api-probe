# LLM API Probe

A local-first LLM API provider availability monitor. It runs scheduled probes
against every configured provider and model, surfaces real-time status in a
browser dashboard, prioritizes the models you pin as favorites, and tracks
availability trends across 1h / 24h / 7d / 30d windows.

## Highlights

- **Multi-provider support**: OpenAI, OpenAI-compatible endpoints (DeepSeek, 硅基流动, 豆包, and others), Anthropic, and Google Gemini.
- **Active probing**: scheduled calls to `list_models` followed by a streaming `chat_completion` per enabled model, recording HTTP status, latency, TTFB, and error code.
- **Favorites**: pin the models you care about to shorten their effective probe interval and surface regressions faster.
- **Trend charts**: availability, latency, and error rate over 1h / 24h / 7d / 30d windows, rendered with Recharts.
- **Import / Export**: provider and favorite configuration as JSON, with API keys optional, supporting full round-trip restoration.
- **Server-Sent Events**: live updates on `probe.completed`, `provider.updated`, `model.updated`, and `job.error`, with auto-reconnect and exponential backoff.
- **Local-first deployment**: single SQLite file, no external services required, bound to `127.0.0.1` by default.

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

The repository is organized into a Python backend, a TypeScript frontend, and
shared documentation and configuration at the root.

```text
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

Configuration is split into two layers:

- **Bootstrap config** in `.env` (template: `.env.example`) — host, port, database URL, log level, and other values read once at process start. The full set of keys is documented in `.env.example`.
- **Runtime-tunable values** in the `settings` table — `retention_days`, `max_concurrency`, favorite and regular model probe intervals, the adaptive-backoff and idle-throttling toggles, and similar. These are editable from the **Settings** page without restarting the process.

## API overview

All endpoints under `/api/v1`. Response envelope: `{data, error}`.

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/healthz` / `/readyz` | liveness / readiness |
| `GET` | `/dashboard` | aggregated overview |
| `GET/POST/PATCH/DELETE` | `/providers[/{id}]` | provider CRUD |
| `POST` | `/providers/{id}/sync-models` | manual model list refresh |
| `POST` | `/providers/{id}/run` | immediate probe |
| `GET` | `/providers/{id}/models` | list models for a provider |
| `PATCH` | `/models/{id}` | toggle enabled / favorite |
| `GET` | `/results?provider_id&model_id&hours&limit` | probe history |
| `GET/PUT` | `/settings` | global config |
| `POST` | `/import` / `/export` | config backup + restore |
| `POST` | `/probe/run?provider_id&model_id` | trigger probe |
| `GET` | `/events` | **SSE** event stream |

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
