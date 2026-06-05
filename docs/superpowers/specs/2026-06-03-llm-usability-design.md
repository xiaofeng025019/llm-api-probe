# LLM API 可用性检测服务 — 设计

**日期**：2026-06-03
**项目目录**：`/home/test/xf_ws/llm_usability`
**状态**：已实现（62 tests, mypy + ruff clean）

> 以下为原始设计稿。实际实现在此基础上有以下差异：
> - Dashboard 卡片从 5 张合并为 3 张：OK/Failing 合并为 "Available Models"（含进度条），Favorite Models 含 24h delta
> - 导入/导出总是含 api_key（去掉了 `?include_keys` toggle），导出含 favorites_by_provider
> - 2 个 alembic 迁移：`4a7be4f2d8b0` (initial) + `6fe570e1adbe` (UTC default)

## 1. 目标与范围

构建一个本地部署的 LLM API 服务商可用性检测服务。用户在浏览器中查看各服务商与模型的实时状态、对收藏模型做重点监测。

**核心功能**
- 服务商（provider）增删改查，每个 provider 持有 base_url / api_key / proxy / 检测频率 / 超时。
- 运行时自动从 `/v1/models` 拉取模型列表并按 ID 推断模型类型（chat / vision / audio / image / embedding / code / unknown）。
- 定时主动探测：先 `list_models`，再对每个启用模型发一次流式 `chat_completion`（`max_tokens=1`），记录状态、HTTP 码、延迟、TTFB、错误码、错误信息。
- 收藏（favorite）模型：可标记，UI 中单独高亮与汇总。
- 历史：保存全部探测结果；UI 展示 24h 可用率与 1h / 24h / 7d / 30d 趋势。
- 导入 / 导出：配置（含 key 可选）JSON。
- Docker Compose 一键启动；亦支持本地裸跑。

**非目标（YAGNI）**
- 邮件 / Slack / Webhook 通知（不做）。
- 多用户 / 鉴权（仅绑 localhost）。
- 分布式 worker / Celery + Redis。
- 主动 fail-over、调用重路由。

## 2. 总体架构

单进程单体：

```
┌─────────────────────────────────────────────────────────┐
│           FastAPI process (uvicorn)                     │
│  ┌────────────┐  ┌──────────────┐  ┌─────────────────┐  │
│  │ REST API   │  │ APScheduler  │  │ StaticFiles     │  │
│  │ /api/v1/*  │  │ (in-process) │  │ (React build/)  │  │
│  └─────┬──────┘  └──────┬───────┘  └─────────────────┘  │
│        └─────┬──────────┘                                │
│              │                                           │
│       ┌──────▼──────┐                                    │
│       │  Service    │  ─── SQLAlchemy ──▶  SQLite (WAL)  │
│       │  Layer      │                                    │
│       └──────┬──────┘                                    │
│              │                                           │
│       ┌──────▼──────┐                                    │
│       │ Prober      │  ─── httpx (async) ──▶  上游 API  │
│       │ Adapters    │                                    │
│       └─────────────┘                                    │
└─────────────────────────────────────────────────────────┘
   localhost:6200
   浏览器 → http://localhost:6200/  → React SPA
                → http://localhost:6200/api/v1/*  → JSON
```

- **FastAPI** 通过 `lifespan` 启动时初始化 DB、调度器、provider 适配器。
- **APScheduler `AsyncIOScheduler`** 与 uvicorn 共享 event loop；jobstore 用 SQLAlchemy → SQLite，重启任务不丢。
- **httpx.AsyncClient** 全局连接池 + HTTP/2；每 provider 一把 `asyncio.Semaphore` 限流。
- **静态前端**：`fastapi.staticfiles.StaticFiles` mount `frontend/dist/`，SPA 路由由前端自身处理。

## 3. 技术栈

- 后端：Python 3.12、FastAPI、SQLAlchemy 2（async）、Alembic、APScheduler、httpx、pydantic v2、pydantic-settings、loguru、uvicorn。
- 存储：SQLite (WAL)；`sqlite+aiosqlite:///./data/llm_usability.db`。
- 前端：React 18 + Vite + TypeScript + Tailwind + react-router v6 + recharts + TanStack Query。
- 包管理：`uv`（后端）+ `pnpm`（前端）。
- 质量：ruff（lint + format）、mypy（type check）、pytest + respx（mock httpx）。
- 部署：Docker Compose，单 `app` 服务；`Dockerfile.backend` 多阶段 build。

## 4. 数据模型（SQLite / SQLAlchemy）

```python
class Provider(Base):
    id: int (pk)                                       # 内部 FK 引用
    uuid_id: UUID (unique, indexed)                    # 外部 API 暴露 ID
    name: str (partial unique, 仅活跃行唯一)           # sqlite_where: deleted_at IS NULL
    kind: enum { openai | openai_compat | anthropic | gemini }
    base_url: str
    api_key: str                                       # 明文（已确认）
    proxy: str | null
    enabled: bool
    interval_seconds: int                              # 默认 300
    timeout_seconds: int                               # 默认 30
    headers_json: str                                  # 自定义 header, JSON 字符串
    created_at, updated_at
    deleted_at: datetime | null                        # 软删除标记

class Model(Base):
    id: int (pk)                                       # 内部 FK 引用
    uuid_id: UUID (unique, indexed)                    # 外部 API 暴露 ID
    provider_id: int (fk -> providers)
    model_id: str                                      # 上游 id
    display_name: str | null
    type: enum { chat | vision | audio | image | embedding | code | unknown }
    enabled: bool
    is_favorite: bool
    last_seen_at: datetime                             # 用于清理下线
    deleted_at: datetime | null                        # 软删除标记

class ProbeResult(Base):
    id: int (pk)                                       # 内部 FK 引用
    uuid_id: UUID (unique, indexed)                    # 外部 API 暴露 ID
    provider_id: int (fk)
    model_id: int (fk, nullable)                       # list_models 时为 null
    target: enum { list_models | chat_completion }
    success: bool
    http_status: int | null
    latency_ms: int | null
    ttfb_ms: int | null                                # 仅流式探测
    error_code: enum | null { auth | rate_limit | timeout | server | network | other }
    error_message: str | null
    checked_at: datetime (indexed)
    provider_name_at_probe: str                        # 快照：探测时的 provider 名称
    model_id_at_probe: str | null                     # 快照：探测时的 model_id 字符串

class Setting(Base):
    key: str (pk)
    value: str
    updated_at
    # e.g.  retention_days=30, default_interval=300, max_concurrency=10

class JobState(Base):
    job_key: str (pk)                                  # f"{provider_id}:{model_id or 'list'}"
    next_run_at: datetime
    last_run_at: datetime | null
    last_status: str | null
```

**索引**
- `probe_results(provider_id, model_id, checked_at DESC)`
- `probe_results(checked_at)`

**保留期**：`setting.retention_days`（默认 30），后台任务 `cleanup_old_results` 每天 03:30 跑。

**类型推断**（`/v1/models` 响应 → `type`）：
- 内置 `MODEL_TYPE_RULES` 映射；不匹配默认 `chat`。
- 例：`*-realtime`→audio；`dall-e*`→image；`*-embedding*`→embedding；`*-vision*` / `claude-3*`→vision；`*-tts*`/`whisper*`→audio；`*-image-*`→image。
- 适配器可在 `post_process_models()` 钩子中追加规则。

## 5. Prober 适配器

统一接口：

```python
class ProbeOutcome:
    success: bool
    http_status: int | None
    latency_ms: int
    ttfb_ms: int | None
    error_code: ErrorCode | None
    error_message: str | None
    models: list[DiscoveredModel] | None = None       # 仅 list_models 填充

class Prober(Protocol):
    name: str
    async def list_models(self, provider: Provider) -> ProbeOutcome: ...
    async def probe_chat(
        self, provider: Provider, model_id: str,
        prompt: str = "hi", max_tokens: int = 1, stream: bool = True,
    ) -> ProbeOutcome: ...
    def classify_model_type(self, model_id: str) -> ModelType: ...
```

**内置实现**

| `kind` | list_models 端点 | chat 端点 | 鉴权 |
|---|---|---|---|
| `openai` | `GET {base_url}/v1/models` | `POST {base_url}/v1/chat/completions` | `Authorization: Bearer …` |
| `openai_compat` | 同上 | 同上 | 同上 |
| `anthropic` | 官方未公开 list；模型 id 由用户在 UI 维护或从 `headers` 预设；可探测 `/v1/messages` 验证鉴权 | `POST {base_url}/v1/messages` | `x-api-key` + `anthropic-version` |
| `gemini` | `GET {base_url}/v1beta/models?key=…` | `POST {base_url}/v1beta/models/{model}:streamGenerateContent?key=…` | query `key=` |

**错误码映射**（httpx 异常 / HTTP 码 → ErrorCode）：
- 401/403 → `auth`
- 408 / `httpx.TimeoutException` → `timeout`
- 429 → `rate_limit`
- 5xx → `server`
- `httpx.ConnectError` / `RemoteProtocolError` → `network`
- 其它 4xx → `other`

**流式 TTFB 测量**：
- `stream=true` 发起请求，记录 `t0`。
- 首字节 SSE `data:` 到达记 `t_ttfb`。
- 收到 `[DONE]` 记 `t_eof`。
- 中途异常 → success=false，记录已读字节 + error。

**限流**：每 provider 一个 `asyncio.Semaphore`，默认 permit=1（每次探测独占），可在 provider 设置中调。

**`list_models` 入库**：成功时 upsert `models` 表，标 `last_seen_at=now`；超过 7 天未出现的模型 `enabled=false`（软下线）。

## 6. 调度与并发

**任务模型**：每个 `(provider_id, target)` 一个 Job。
- `provider=1, target=list_models` × 1
- `provider=1, model=gpt-4o, target=chat_completion` × 1
- 合计 N + M 个 Job。

**APScheduler 配置**
- `AsyncIOScheduler`，与 FastAPI event loop 共享。
- `jobstores`：`SQLAlchemyJobStore(url=DB_URL)`，重启不丢。
- 触发器：`IntervalTrigger(seconds=provider.interval_seconds)`，首跑 0~5s 抖动。
- 单任务 `coalesce=True, max_instances=1, misfire_grace_time=interval*2`。

**单次探测流程**
1. 校验 provider 仍 enabled、model 仍 enabled；否则 pause job 并 return。
2. 拿 provider 限流 semaphore。
3. 调对应 Prober 探测。
4. 写 `probe_results`（成功 / 失败都写）。
5. 更新 `jobs.last_run_at`、`last_status`。
6. 异常吞掉 + 写一条 `error_message="unexpected: {repr(e)}"` 的失败结果，绝不让任务死。

**手动 "立即检测"**
- `POST /api/v1/probe/run {provider_id?, model_id?}` → `scheduler.modify_job(next_run_time=now)`；不阻塞响应（提交到 loop 即可）。
- 结果写库后通过 `SseManager.broadcast()` 推 `probe.completed` 给前端。

**provider / model 增删改** → 同步 `scheduler.add_job / remove_job / pause_job`。

**全局并发上限**：根 `setting.max_concurrency`（默认 10）控制总 `asyncio.Semaphore`。

**启动错峰**：进程启动时给每个 Job 加 `jitter=5` 秒随机延迟。

## 7. REST API

`/api/v1` 前缀；统一响应壳 `{data, error}`；JSON Schema 用 Pydantic v2 双向。

| Method | Path | 用途 |
|---|---|---|
| `GET` | `/providers` | 列表（含 enabled、统计） |
| `POST` | `/providers` | 新建 |
| `GET` | `/providers/{id}` | 详情 |
| `PATCH` | `/providers/{id}` | 局部更新 |
| `DELETE` | `/providers/{id}` | 级联删 models + results |
| `POST` | `/providers/{id}/sync-models` | 手动拉取 list_models |
| `GET` | `/providers/{id}/models` | 列出该 provider 的模型 |
| `PATCH` | `/models/{id}` | 改 enabled / is_favorite |
| `GET` | `/dashboard` | 总览：每 provider 的绿 / 黄 / 红计数 + 最近时间 |
| `GET` | `/results` | 历史 `?provider_id&model_id&since&until&limit` |
| `GET` | `/results/summary` | 24h 可用率 / 平均延迟 / p50 / p95 / 失败次数 |
| `POST` | `/probe/run` | 手动触发 `{provider_id?, model_id?}` |
| `GET` | `/settings` | 取所有 |
| `PUT` | `/settings` | 批量改 |
| `POST` | `/export` | 导出 JSON `{providers, models, settings}`（`?include_keys=true`） |
| `POST` | `/import` | 导入 JSON（按 `name` upsert；同 name 时 `api_key` 仅在导入文件包含时覆盖，否则保留原值；`probe_results` 不导入） |
| `GET` | `/events` | **SSE** 实时事件流 |
| `GET` | `/healthz` | liveness |
| `GET` | `/readyz` | DB 可写、scheduler 在跑 |

**SSE 事件类型**
- `probe.completed { provider_id, model_id, target, success, latency_ms, checked_at }`
- `provider.updated { id }`
- `model.updated { id }`
- `job.error { provider_id, model_id, message }`
- 心跳：每 25s 一行 `: ping`。

**鉴权**：无（仅绑 `127.0.0.1`）。**CORS**：开发期 `http://localhost:5173`；生产同源无 CORS。

## 8. 前端

**目录**
```
frontend/
  src/
    app/             # 路由
    pages/           # Dashboard / Providers / ProviderDetail / Models / Settings
    components/      # StatusDot, LatencyChart, ModelTypeBadge, ApiKeyInput
    api/             # fetch 封装 + 类型
    hooks/           # useSse
    lib/             # format, modelType 颜色
  index.html
  vite.config.ts
```

**页面 & 组件**

1. `DashboardPage /`
   - 顶部 4 卡：在线 provider 数 / 在线 model 数 / 失败 > 1 / favorite 是否全在线。
   - 主体表格：provider / kind / 模型数 / 状态点 / 上次检测 / 24h 可用率 / 「立即检测」。
   - 收藏模型专属区：favorite 模型 × provider 矩阵，红黄绿点。

2. `ProvidersPage /providers`
   - 列表 + 增 / 改 / 删对话框。kind 不同表单字段不同（Anthropic 需 `anthropic-version`，Gemini 可走 `?key=`）。

3. `ProviderDetailPage /providers/:id`
   - provider 元信息 + 「立即检测」「同步模型列表」按钮。
   - 模型表：model_id / 类型徽章 / enabled / favorite / 状态点 / 上次延迟 / 24h 可用率 / 折线图。
   - 时间窗切换 1h / 24h / 7d / 30d。

4. `ModelsPage /models`（收藏管理）
   - 全局 favorite 列表 + 类型过滤 + 批量取消 favorite。

5. `SettingsPage /settings`
   - 全局：默认 interval / timeout / max_concurrency / retention_days。
   - 导入 / 导出区。

**实时刷新**
- 每页 `useSse('/api/v1/events')`，收到 `probe.completed` 局部更新（按 id 替换），不重拉整表。
- 30s 兜底轮询保活（避免 SSE 中断无感）。

**图表**：`recharts` LineChart，画可用率 / p95 延迟。
**状态色**：🟢 ok（最近一次成功）/ 🟡 stale（>2 interval 未跑）/ 🔴 fail（最近一次失败）。
**类型徽章**：chat/vision/audio/image/embedding/code 各一色。

**构建**
- `pnpm build` → `frontend/dist/`。
- 后端 `app.mount("/", StaticFiles(directory="frontend/dist", html=True))` 兜底 SPA。

**开发态**：`vite.config.ts` 配 `server.proxy: { "/api": "http://localhost:6200" }`。

## 9. 部署

### 9.1 仓库布局
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

### 9.2 Docker Compose
- 单服务 `app`：基于 `python:3.12-slim`，`uv pip install` 安装后端。
- 多阶段 build：build 阶段跑 `pnpm --dir frontend install && pnpm --dir frontend build`；运行时只保留 `dist/`。
- 卷挂载：
  - `./data:/app/data`（SQLite + 调度持久化）
  - `./.env:/app/.env:ro`
- 端口：`127.0.0.1:6200:6200`。
- 重启策略：`unless-stopped`。
- 健康检查：`GET /healthz`，间隔 30s。

### 9.3 本地裸跑
```bash
cd backend && uv sync
cd ../frontend && pnpm install && pnpm build && cd ..
uv run uvicorn app.main:app --host 127.0.0.1 --port 6200
```

### 9.4 .env
```
APP_HOST=127.0.0.1
APP_PORT=6200
DATABASE_URL=sqlite+aiosqlite:///./app/data/llm_usability.db
LOG_LEVEL=INFO
DEFAULT_INTERVAL_SECONDS=300
DEFAULT_TIMEOUT_SECONDS=30
MAX_CONCURRENCY=10
RETENTION_DAYS=30
PROBE_PROMPT=hi
PROBE_MAX_TOKENS=1
TZ=Asia/Shanghai
```

## 10. 错误处理

- **API 层**：统一异常处理器 → `{error: {code, message, details?}}`，HTTP 码语义化。
- **探测层**：绝不让异常出 `probe_chat`；吞掉 + 写失败结果。
- **调度层**：`max_instances=1, coalesce=True, misfire_grace_time=interval*2`。
- **SSE**：客户端断开自动重连；服务端心跳 25s `: ping`。
- **DB 写**：commit 失败重试 3 次（指数退避），仍失败记日志 + 触发 SSE `job.error`。
- **启动失败**：DB / 调度器初始化抛错 → 进程退出码非 0，由 Docker 重启；UI 启动时显示 `/readyz` 失败。

## 11. 日志

- `loguru`，输出到 stdout + 文件 `data/app.log`（10MB × 5 rotate）。
- 关键事件：`probe.start / probe.success / probe.fail / scheduler.job_added / api.request`。
- **绝不在日志中打印完整 `api_key`**，统一截断为 `sk-…xxxx`。
- 访问日志由 uvicorn 开启。

## 12. 测试

| 层 | 工具 | 覆盖 |
|---|---|---|
| Prober 适配器 | `respx` 模拟 httpx | list_models 解析、错误码映射、流式 TTFB |
| Service 层 | 内存 SQLite | upsert 行为、保留期清理 |
| API 层 | `httpx.AsyncClient(app=app)` | 增删改查 + 错误码 |
| Scheduler | 调快 interval + 假 adapter | Job 触发、并发上限、enabled=false 暂停 |
| 导入导出 | 假数据 | round-trip |

**目标**：行覆盖 ≥ 80%，核心模块（probers / services / api）≥ 90%。
**CI**：`ruff check`、`ruff format --check`、`mypy app`、`pytest -q`。

## 13. 安全声明（README 强调）

- API Key 明文存 SQLite（已与用户确认）；**不推荐公网 / 多人共用**。
- 部署到公网前自行加反向代理（Nginx / Caddy）+ BasicAuth。
- 默认绑定 `127.0.0.1`，文档列出已知风险。

## 14. 未来扩展（Out of Scope）

- 通知（邮件 / Slack / Webhook）
- 多用户 / 鉴权
- 分布式 worker
- 调用重路由
- 移动端
