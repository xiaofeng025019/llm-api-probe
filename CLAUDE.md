# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目当前状态

**设计稿阶段 — 尚未实现**。仓库内只有设计文档（`docs/superpowers/specs/2026-06-03-llm-usability-design.md`），没有后端 / 前端 / Docker 代码。`/init` 任务目前的素材是这一份设计稿；后续工作按该 spec 落地。

## 项目目标

本地部署的 LLM API 可用性检测服务。浏览器中查看各服务商（provider）与模型的实时状态、对收藏模型重点监测、查看 24h 可用率与 1h / 24h / 7d / 30d 趋势。

## 计划中的架构（单进程单体）

```
FastAPI process (uvicorn) @ localhost:8000
├── REST API        /api/v1/*
├── APScheduler     (in-process, 与 uvicorn 共享 event loop)
├── StaticFiles     mount frontend/dist/   (React SPA)
├── Service Layer   ── SQLAlchemy (async) ──▶ SQLite (WAL)
└── Prober Adapters ── httpx (async)      ──▶ 上游 LLM API
```

- 后端事件循环内：`AsyncIOScheduler` + `SQLAlchemyJobStore`（重启任务不丢）+ 全局 `httpx.AsyncClient`（HTTP/2、连接池）。
- 每 provider 一把 `asyncio.Semaphore`，全局再加一把 `max_concurrency` 根 semaphore。
- 静态前端：`fastapi.staticfiles.StaticFiles` mount `frontend/dist/`，SPA 路由由前端自身处理。
- 无用户鉴权，仅绑 `127.0.0.1`。

## 计划技术栈

- **后端**：Python 3.12、FastAPI、SQLAlchemy 2 (async)、Alembic、APScheduler、httpx、pydantic v2、pydantic-settings、loguru、uvicorn。
- **存储**：SQLite WAL — `sqlite+aiosqlite:///./data/llm_usability.db`。
- **前端**：React 18 + Vite + TypeScript + Tailwind + react-router v6 + recharts + TanStack Query。
- **包管理**：`uv`（后端）+ `pnpm`（前端）。
- **质量**：ruff（lint + format）、mypy、pytest + respx（mock httpx）。
- **部署**：单服务 Docker Compose；`Dockerfile.backend` 多阶段 build。

## 计划仓库布局

```
llm_usability/
├── backend/                  # FastAPI app
│   ├── app/
│   ├── tests/
│   ├── pyproject.toml
│   └── alembic/
├── frontend/                 # React + Vite
├── docker-compose.yml
├── Dockerfile.backend
├── .env.example
├── docs/
└── README.md
```

## 数据模型要点

四张表：`providers`、`models`、`probe_results`、`settings` + 一张 `job_states`（jobstore 用）。详见 `docs/.../2026-06-03-llm-usability-design.md` §4。

- `Provider.kind` ∈ `{openai, openai_compat, anthropic, gemini}`。
- `Model.type` ∈ `{chat, vision, audio, image, embedding, code, unknown}`，由 `MODEL_TYPE_RULES` 从 model_id 推断（`*-realtime`→audio；`dall-e*`→image；`*-embedding*`→embedding；`*-vision*` / `claude-3*`→vision；`*-tts*`/`whisper*`→audio；`*-image-*`→image）；适配器可在 `post_process_models()` 钩子中追加。
- `ProbeResult.error_code` ∈ `{auth, rate_limit, timeout, server, network, other}`。
- 索引：`probe_results(provider_id, model_id, checked_at DESC)`、`probe_results(checked_at)`。
- 保留期 `setting.retention_days=30`，后台任务 `cleanup_old_results` 每天 03:30 跑。

## Prober 适配器

统一 `Prober` Protocol：`list_models` + `probe_chat(stream=True, max_tokens=1)` + `classify_model_type`。
- `openai` / `openai_compat`：`GET /v1/models` + `POST /v1/chat/completions`，`Authorization: Bearer …`。
- `anthropic`：官方未公开 list，模型由 UI 维护或 headers 预设；`POST /v1/messages` 用 `x-api-key` + `anthropic-version`。
- `gemini`：`GET /v1beta/models?key=…` + `POST /v1beta/models/{model}:streamGenerateContent?key=…`，鉴权走 query。

**流式 TTFB 测量**：`stream=true` 记录 `t0`；首字节 `data:` 到 `t_ttfb`；`[DONE]` 到 `t_eof`。
**HTTP 错误映射**：401/403→`auth`；408/TimeoutException→`timeout`；429→`rate_limit`；5xx→`server`；ConnectError/RemoteProtocolError→`network`；其它 4xx→`other`。
**`list_models` 入库**：upsert `models` 表并 `last_seen_at=now`；超过 7 天未出现软下线 `enabled=false`。

## 调度

- 每个 `(provider_id, target)` 一个 Job；`target ∈ {list_models, chat_completion}`。
- `AsyncIOScheduler` + `SQLAlchemyJobStore`，`IntervalTrigger(seconds=provider.interval_seconds)`，启动加 5s 抖动。
- `coalesce=True, max_instances=1, misfire_grace_time=interval*2`。
- 任务流程：校验 enabled → 拿 semaphore → 探测 → 写 `probe_results` → 更新 `JobState` → 异常吞掉并写一条 `error_message="unexpected: {repr(e)}"`。
- provider / model 增删改需同步 `scheduler.add_job / remove_job / pause_job`。
- 手动"立即检测"：`POST /api/v1/probe/run` → `scheduler.modify_job(next_run_time=now)`；结果走 `SseManager.broadcast()` 推 `probe.completed`。

## REST API（`/api/v1` 前缀，统一 `{data, error}` 响应壳）

`providers` CRUD、`PATCH /models/{id}`（enabled / is_favorite）、`/dashboard`、`/results`、`/results/summary`、`/probe/run`、`/settings`、`/export`、`/import`、`/events`（SSE）、`/healthz`、`/readyz`。

**导入语义（spec 已澄清）**：`api_key` 仅在导入文件包含时覆盖；同名 provider 缺省时保留原值；`probe_results` 不导入。

**SSE 事件**：`probe.completed`、`provider.updated`、`model.updated`、`job.error`；心跳 `: ping` 每 25s。

## 计划中的命令

> 这些命令是 spec 推导出的目标命令；当前仓库尚未落地实现。

**本地裸跑**
```bash
cd backend && uv sync
cd ../frontend && pnpm install && pnpm build && cd ..
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**开发态**：前端 `vite.config.ts` 配 `server.proxy: { "/api": "http://localhost:8000" }`；前端 `pnpm dev` 直跑 5173。

**质量**：`ruff check`、`ruff format --check`、`mypy app`、`pytest -q`。覆盖目标：行覆盖 ≥ 80%，核心模块（probers / services / api）≥ 90%。

**Docker Compose**：单服务 `app` 绑 `127.0.0.1:8000:8000`，卷 `./data:/app/data`、`./.env:/app/.env:ro`，健康检查 `GET /healthz` 间隔 30s，`unless-stopped` 重启。

## 安全与日志

- API Key **明文存 SQLite**（用户已确认）—— 不推荐公网 / 多人共用，部署到公网前自行加反向代理 + BasicAuth。默认 `127.0.0.1`。
- 日志：`loguru` → stdout + `data/app.log`（10MB × 5 rotate）。**绝不在日志中打印完整 `api_key`**，统一截断为 `sk-…xxxx`。
- 关键事件：`probe.start / probe.success / probe.fail / scheduler.job_added / api.request`。

## 非目标（YAGNI，不要顺手做）

邮件 / Slack / Webhook 通知；多用户 / 鉴权；分布式 worker（Celery + Redis）；主动 fail-over / 调用重路由；移动端。

## 设计文档入口

- `docs/superpowers/specs/2026-06-03-llm-usability-design.md` — 完整设计稿（14 章）。所有架构决策、数据模型、API 表、调度策略、错误映射、测试覆盖目标都在此文件，**以它为准**。
