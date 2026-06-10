# LLM API Probe — 设计

**日期**：2026-06-03
**项目目录**：`/home/test/xf_ws/llm-api-probe`
**状态**：已实现（2026-06-06 时 101 tests, ruff clean）

> **此文档是 2026-06-03 的原始设计稿。**
> 实现在三天内有大量演进，全部增量见文末 **附录 A：实现演进（2026-06-03 → 2026-06-06）**。
> 数据模型、调度器、REST 表、前端结构、SSE 等都已与本节描述偏离；以附录 A 为准。
> 文中的旧"实现差异"提示行（dashboard 卡片合并、include_keys 取消、迁移数）已被附录 A 覆盖。

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
- 存储：SQLite (WAL)；`sqlite+aiosqlite:///./data/llm_api_probe.db`。
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
    timeout_seconds: int                               # 默认 60
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
llm-api-probe/
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
DATABASE_URL=sqlite+aiosqlite:///./app/data/llm_api_probe.db
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

---

# 附录 A：实现演进（2026-06-03 → 2026-06-06）

> 写于 2026-06-06。本附录覆盖原 §1–§13 中所有与现状不符的部分；
> 当 §1–§13 与本附录冲突时，**以本附录为准**。
> 列表里的每条都标了实际所在的源文件，便于追踪。

## A.1 数据模型补充

### Provider（`app/db/models.py:59`）
新增字段：
- `uuid_id: UUID(unique, indexed)` —— **对外 API 统一暴露的稳定 ID**。`id`（int PK）仅做内部 FK。
- `deleted_at: datetime | null` —— 软删除标记。
- `name` 索引改为 `partial unique where deleted_at IS NULL` —— 软删后允许同名复用。

### Model（`app/db/models.py:85`）
新增字段（**全部不在原 §4 内**）：
- `uuid_id`、`deleted_at` —— 同 Provider。
- `is_favorite: bool` —— 收藏标记。原 §4 把 favorite 放在 spec 文字里但缺字段。
- `status: str(40), default "unknown"` —— 当前可达状态（online/suspect/offline/disabled/…）。
- `status_reason: str(300) | null` —— 例如 "auth"、"rate_limit"。
- `status_checked_at: datetime | null` —— 最近一次状态检查时间。
- `status_confirmed_at: datetime | null` —— 状态被**确认**为 offline 的时间（基于连续失败次数）。
- `last_success_at: datetime | null` —— 最近成功探测。
- `consecutive_failures: int, default 0` —— 用于"连续 N 次失败才标 offline"的确认窗口（favorite N=2，regular N=3，见 A.6）。

### ProbeResult（`app/db/models.py:117`）
新增字段：
- `uuid_id` —— 对外 ID。
- `ttfb_ms: int | null` —— 流式探测的首字节延迟，**原 §4 漏列**。
- `pinned: bool, default False` —— 错误"钉住"标记，逃过保留期清理（见 A.4 errors 端点）。
- **快照字段（snapshot at probe time）**：
  - `provider_name_at_probe: str`
  - `model_id_at_probe: str | null`
  - `provider_uuid_at_probe: UUID`
  - `model_uuid_at_probe: UUID | null`
- FK 变更：`provider_id` 和 `model_id` 从 `ON DELETE CASCADE` 改为 `SET NULL`，**保证 provider/model 硬删后历史结果仍可读**（依赖上面的 UUID/字符串快照定位归属）。
- 索引新增：`ix_probe_results_provider_uuid_at_probe`、`ix_probe_results_model_uuid_at_probe`、`ix_probe_results_pinned_checked_at`。
- 两个 `@property`：`provider_uuid`、`model_uuid` —— 优先取活 FK，回落到快照。

### 不变
- `Setting`、`JobState` 表与原 §4 一致。
- `ErrorCode`、`ProbeTarget`、`ProviderKind` 枚举一致。

## A.2 Alembic 迁移：实际 8 条（不是 2 条）

按 down_revision 链顺序（`backend/alembic/versions/`）：

1. `4a7be4f2d8b0_initial_schema` —— 初始化所有表。
2. `0158e57e3489_add_uuid_snapshot_softdelete_fields` —— Provider / Model 加 `uuid_id` + `deleted_at`；ProbeResult 加 `uuid_id` + 字符串快照。
3. `7ad1aec504b8_add_partial_unique_index_on_provider_name` —— providers.name → 部分唯一索引。
4. `b6f1c2d4a9e1_add_model_status_confirmation_fields` —— Model 加 status*/last_success_at/consecutive_failures。
5. `c9a5e4d7b2f0_backfill_missing_probe_result_uuids` —— 数据迁移：旧行回填 uuid_id。
6. `d4f8a2c6b1e3_add_probe_result_uuid_snapshots` —— ProbeResult 加 UUID 快照列 + 索引。
7. `e1f3a7b2c594_probe_results_provider_nullable_set_null` —— FK CASCADE → SET NULL（SQLite 表重建）。
8. `c4f7e1a9b3d2_add_probe_result_pinned` —— 加 `pinned` 列 + 索引。

## A.3 Probers 演进

注册表 4 个 kind 没变（`app/probers/__init__.py:14`），但：
- **MiniMax 静态 fallback**（`openai_base.py:28-33` + `:107-117`）：当 `GET /v1/models` 非 200 且 host 命中 `minimaxi.com`，注入硬编码模型列表（`MiniMax-M3`、`MiniMax-M2.1`），避免 MiniMax 不提供 `/v1/models` 时 dashboard 空空如也。
- **URL 去重**（`openai_base.py` 中的 `_make_url`）：当 `base_url` 本身已以 `/v1` 结尾，不再重复拼。修了之前一次 MiniMax base_url 配错的真实事故。
- **模型类型推断**集中在 `app/probers/model_classify.py`，按 model_id 关键词分类。

## A.4 REST API：实际端点

`/api/v1` 前缀。**加粗为原 §7 未列**。

### `/providers`（`app/api/v1/providers.py`）

| 方法 | 路径 |
|---|---|
| GET | `/providers` |
| POST | `/providers` —— 同名 + (base_url, api_key) 双重去重；409 |
| GET | `/providers/{provider_id}` |
| PATCH | `/providers/{provider_id}` |
| DELETE | `/providers/{provider_id}` —— 软删 |
| POST | `/providers/{provider_id}/sync-models` —— 同时调 `record_outcome` 反映到 dashboard |
| GET | `/providers/{provider_id}/models` |
| **POST** | **`/providers/{provider_id}/models`** —— 手工添加（list_models 不可用时的兜底） |
| **POST** | **`/providers/{provider_id}/run`** —— 单 provider 立即重测 |

### `/models`（`app/api/v1/models.py`）

| 方法 | 路径 |
|---|---|
| PATCH | `/models/{model_id}` |

### 其他（`app/api/v1/dashboard.py`）

| 方法 | 路径 |
|---|---|
| GET | `/dashboard` |
| GET | `/results` |
| GET | `/settings` |
| PUT | `/settings` —— 写后调 `sync_all_jobs()` |
| POST | `/import` |
| POST | `/export` —— **总是含 api_key**（去掉了原 spec 的 `?include_keys` 开关） |
| POST | `/probe/run` |
| **POST** | **`/probe/run-all`** —— 全量立即重测（dashboard "刷新全部" 按钮）|
| **GET** | **`/errors`** —— 最近失败列表（含 pinned 在顶）|
| **POST** | **`/errors/{probe_uuid}/pin`** |
| **DELETE** | **`/errors/{probe_uuid}/pin`** |
| GET | `/events` —— SSE |

### Health（`app/main.py`）
- `GET /api/v1/healthz`
- **`GET /api/v1/readyz`** —— scheduler 是否在跑

## A.5 SSE 实际事件

原 §7 列了 4 种，**真正 broadcast 的只有 2 种**（`app/core/scheduler.py:434, 450`）：

| 事件 | 触发 | payload |
|---|---|---|
| `probe.completed` | 每次探测结束 | id, provider_id, model_id, target, success, http_status, latency_ms, ttfb_ms, error_code, checked_at |
| `job.error` | **仅** model.is_favorite 且失败 | provider_id/name, model_id/name, model_is_favorite, error_code, message |

外加：
- 25s 心跳 `: ping`（dashboard.py:380）
- `SseManager.active_subscribers()` + 0↔1 lifecycle hooks，被调度器拿来做"空闲降频"（见 A.6）。

`provider.updated` / `model.updated` 在前端 `useSse.ts` 已订阅但后端从未发——是死代码，需要清理或者补 broadcast。

## A.6 设置（Setting 表）：重新设计

原 §4 / §7 提到 `retention_days / default_interval / max_concurrency` 应该写在 Setting 表。**实际上这三个仍由 `app/core/config.py` 的环境变量管**；DB Setting 表存的是另一套更细粒度的 key（`app/services/settings.py`）：

| Setting Key | 默认 | 用途 |
|---|---|---|
| `favorite_model_interval_seconds` | 300 | 收藏模型探测间隔（base） |
| `regular_model_interval_seconds` | 120 | 普通模型探测间隔（base） |
| `favorite_model_failure_confirmations` | 2 | 收藏模型连续失败几次才标 offline |
| `regular_model_failure_confirmations` | 3 | 普通模型同上 |
| `provider_rate_limit_per_minute` | 20 | 每 provider 滑窗速率上限（见 A.7） |
| `adaptive_backoff_enabled` | true | 见 A.7 |
| `idle_throttle_enabled` | true | 见 A.7 |

环境变量（`config.py`）：`default_interval_seconds`、`default_timeout_seconds`、`max_concurrency`、`retention_days`、`probe_prompt`、`probe_max_tokens`、`tz` 等仍在。

## A.7 调度器：相比原 §6 的大量增强

原 §6 描述的"每 provider+target 一个 Job、IntervalTrigger"骨架仍在，但围绕成本控制和稳定性增加了五层机制（全部在 `app/core/scheduler.py`）：

1. **每 provider 滑动窗口速率限制**（`:114, :117`）—— 内存 deque，60s 内最多 N 次（默认 20）。超出直接 return，不打上游。修了之前用户被多账号 + 频繁探测打爆配额的问题。

2. **自适应退避（adaptive backoff）**（`:165–:230`）—— 内存 `_model_success_streak[model_uuid]` 累计连续成功次数；按 1×/2×/4×/8× 阶梯（streak <3 / <10 / <30 / ≥30）放大 base interval。一次失败立刻清零回 1×。开关 `adaptive_backoff_enabled`。

3. **空闲降频（idle throttle）**（`:194, :259, :285, :318`）—— 通过 `SseManager.active_subscribers()` 判断 dashboard 是否有人开。无人时所有 chat 探测 ×5；0→1 转换立即触发 `trigger_all_models_now()`；1→0 把现有队列推远 ×5。开关 `idle_throttle_enabled`。两机制叠乘：稳定 + 无人 = `base × 8 × 5 = 40×`。

4. **后台随机扫描（random sweep）**（`:782, :863`）—— 每 20s 选最多 3 个超过 `max(30s, eff/2)` 未探测的模型补一次。让上游流量不像精确周期任务、并让 dashboard 在刚打开时数据看起来"持续刷新"。Backoff-aware：稳定模型不会被打扰。

5. **启动追赶探测**（`app/main.py:60–67`）—— `lifespan` 中 spawn 一个 `trigger_all_models_now()`，让笔记本休眠 / 容器重启后 dashboard 在数秒内有最新数据，而不是等到下个 interval。

调度器同时维护：
- 全局 `Semaphore(max_concurrency)`
- 每 provider `Semaphore(1)`（同 provider 探测顺序化）
- 每个新 job 首跑 `next_run_time=now`，jitter=10% of interval（最小 5s）
- `coalesce=True`、`max_instances=1`、`misfire_grace_time=interval*2`

每次探测结束后会 `modify_job(next_run_time=…)` 把下一次触发时间写成 `base × backoff × idle + jitter`——这是 backoff/idle 真正生效的地方（而不是改 trigger）。

## A.8 测试

实际 **9 个文件 / 101 个测试**（不是 spec 提到的 62）：

| 文件 | 数量 | 覆盖 |
|---|---|---|
| `test_services.py` | 35 | providers / models / results / settings service 层 |
| `test_probers.py` | 19 | 三家 prober + MiniMax fallback + error mapping |
| `test_scheduler.py` | 18 | sync_jobs、backoff、idle、rate limit、random sweep、trigger_* |
| `test_api.py` | 18 | REST + SSE + cascade preservation |
| `test_e2e_real_http.py` | 4 | respx 端到端流 |
| `test_cli.py` | 4 | `app.cli` 工具 |
| `test_db_init.py` | 2 | Alembic 初始化 |
| `test_healthz.py` | 1 | healthz |

## A.9 日志

2026-06-06 之前用 stdlib `logging`（uvicorn 的 stdout 即日志，无文件落盘），与 spec §11 写的 loguru + `data/app.log` rotation 不符。**2026-06-06 已按 spec 接入 loguru**：`app/core/logging.py:configure_logging()` 在 `main.py` 顶部最先调用，安装：
- stdout sink（彩色，匹配 uvicorn 风格）
- 文件 sink `data/app.log`，rotation `10 MB`、retention `5`（与 spec 一致）
- `InterceptHandler` 把 stdlib + uvicorn 三个 named logger（`uvicorn`/`uvicorn.error`/`uvicorn.access`）的记录都转进 loguru —— 所以**全部既有 `logging.getLogger(__name__).info(...)` 调用不用改**
- data dir 不可写时 fallback 到 stdout-only，不让日志故障打挂应用

测试：`test_logging.py` 三条（idempotent / root handler 单一 / uvicorn 三 logger handlers 清空且 propagate=True）。

## A.10 前端：与 §8 的偏差

- **新增页 ErrorsPage**（`pages/ErrorsPage.tsx`）—— 接 `GET /errors` + pin/unpin。原 §8 五页扩到六页。
- **ProvidersPage 已删除**（2026-06-06）：路由 `/providers` 保留 redirect 到 `/`（兼容旧书签），`ProviderDialog` 已抽到 `components/ProviderDialog.tsx` 给 DashboardPage 复用。
- **Tailwind 未使用**——改为单文件 `src/styles.css`（约 2600 行）+ CSS 变量 + `data-theme` 切换（`useTheme.ts`）。所有色彩都走 `var(--*)` 间接层。
- **TanStack Query 未引入**——改为自写 `useDashboard` hook（`hooks/useDashboard.ts`），分快/慢两层：
  - Fast tier（dashboard + providers + settings）每 30s 刷一次 + SSE 触发 + 500ms 防抖
  - Slow tier（每 provider 的 models 扇出）每 5 min 刷一次
- **i18n 完整实现**（spec 没写）：`lib/i18n.ts` + `locales/{en,zh}.ts` + `useT()` + `LanguageToggle`（globe 图标 + 下拉）。约 80 行核心代码，零依赖，typed-keys (`Key<Messages>`) 通过 mapped type 推导。详见 `frontend/src/lib/i18n.ts`。
- **图表**用 recharts，theme-reactive：`ResultsChart.tsx` 读 `useTheme()` 切换 axis/grid 颜色。
- **SSE**：`useSse.ts` 共享单 EventSource，指数退避重连（1s→30s cap）。订阅 `ping/probe.completed/provider.updated/model.updated/job.error`，**5 类全部后端真发**（2026-06-06 起：provider/model PATCH/POST/DELETE 都补了 broadcast）。
- **全局 SSE 监听器** `GlobalSse`（`main.tsx:26-50`）只在 favorite 模型 `job.error` 时弹 toast。
- **顶部导航**只有 Dashboard / Errors / Settings；`/models` 和 `/providers/:id` 走链接进入。

## A.11 Docker / 部署：一个真 bug

`Dockerfile.backend` 的 `CMD` 硬编码 `--port 8000`，但 `docker-compose.yml` 同时设了 `APP_PORT=6200` 且把 `127.0.0.1:6200:6200` 暴露出来——**这意味着 compose 启动后容器实际仍监听 8000，6200 端口映射打不到任何东西**。已在 2026-06-06 修复（见 commit history）：把 CMD 改为透传 `${APP_HOST}/${APP_PORT}`。

## A.12 待办进度（2026-06-06 全部清理完毕）

1. ✅ ~~死代码 / 未发事件~~：provider/model PATCH/POST/DELETE + sync-models 全部 broadcast `provider.updated` / `model.updated`。
2. ✅ ~~重复 settings 来源~~：`config.py` Settings 类与 `services/settings.py` 顶端都加了对照 docstring 解释"启动期 env vs 运行期 DB"的设计。
3. ✅ ~~`ProvidersPage` 半弃用~~：`ProviderDialog` 已抽到 `components/ProviderDialog.tsx`，`ProvidersPage` 删除，路由仍 redirect 兼容旧书签。
4. ✅ ~~loguru 与文件 rotation~~：按 spec 接入 loguru + `data/app.log` 10MB×5 rotation（详见 §A.9）。
5. ✅ ~~`/providers/{id}/run` vs `/probe/run` 重叠~~：两端点 docstring 互相引用，明确分工（前者 provider-wide REST，后者 model-grained query-string）。


