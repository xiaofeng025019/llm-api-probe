# LLM 可用性检测服务

本地部署的 LLM API 服务商可用性检测服务。浏览器查看各服务商与模型的实时状态，对收藏模型重点监测。

> **设计稿**：[`docs/superpowers/specs/2026-06-03-llm-usability-design.md`](docs/superpowers/specs/2026-06-03-llm-usability-design.md)
>
> **状态**：MVP — 后端 40 测试通过，前端 5 页面可运行，Docker 化已就绪。

## 特性

- 支持 OpenAI、OpenAI 兼容（DeepSeek / 硅基流动 / 豆包等）、Anthropic、Google Gemini 四类 provider
- 定时主动探测：list_models + 流式 chat_completion，记录状态、延迟、TTFB、错误码
- 模型类型自动推断（chat / vision / audio / image / embedding / code）
- 收藏模型单独高亮
- 24h / 7d / 30d 趋势图（recharts）
- 导入 / 导出 JSON 配置（含 key 可选）
- SSE 实时事件流（probe.completed / provider.updated / model.updated / job.error）
- 本地单进程单 SQLite，无外部依赖

## 技术栈

- **后端**：Python 3.12 / FastAPI / SQLAlchemy 2 async / APScheduler 3 / httpx / pydantic v2 / loguru / uvicorn
- **存储**：SQLite WAL
- **前端**：React 18 + Vite + TypeScript + react-router v6 + recharts
- **包管理**：`uv`（后端）+ `pnpm`（前端）
- **质量**：ruff / mypy / pytest + respx

## 启动

### 方式 A：Docker Compose（推荐）

```bash
cp .env.example .env   # 可选，按需修改
docker compose up -d
# 浏览器打开 http://127.0.0.1:8000
```

数据持久化在 `./data/llm_usability.db`，调度任务也存于同一文件。

### 方式 B：本地裸跑

```bash
# 后端
cd backend
uv sync
cd ..

# 前端
cd frontend
pnpm install
pnpm build
cd ..

# 启动
cd backend
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### 方式 C：开发态（前后端分离热更新）

```bash
# 终端 1
cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# 终端 2
cd frontend && pnpm dev   # http://localhost:5173，/api 自动代理到 :8000
```

## 配置

所有配置走 `.env`（参考 `.env.example`）。运行期可改项（`retention_days` / `max_concurrency` 等）落 `settings` 表，UI 在 **Settings** 页改。

## 测试

```bash
cd backend
uv run pytest            # 40 tests
uv run ruff check app    # lint
uv run ruff format --check app
uv run mypy app
```

## API 概览

| Method | Path | 说明 |
|---|---|---|
| `GET`    | `/api/v1/healthz` / `/readyz` | 健康 / 就绪 |
| `GET`    | `/api/v1/dashboard` | 总览 |
| `GET/POST/PATCH/DELETE` | `/api/v1/providers[/{id}]` | 服务商 CRUD |
| `POST`   | `/api/v1/providers/{id}/sync-models` | 手动拉取模型 |
| `POST`   | `/api/v1/providers/{id}/run` | 立即探测 |
| `GET`    | `/api/v1/providers/{id}/models` | 模型列表 |
| `PATCH`  | `/api/v1/models/{id}` | enabled / is_favorite |
| `GET`    | `/api/v1/results` | 历史探测结果（`?provider_id&model_id&hours&limit`） |
| `GET/PUT` | `/api/v1/settings` | 全局配置 |
| `POST`   | `/api/v1/import` / `/export` | 配置导入 / 导出（`?include_keys=true` 时含明文 key） |
| `POST`   | `/api/v1/probe/run` | 手动触发探测 |
| `GET`    | `/api/v1/events` | **SSE** 实时事件 |

统一响应壳：`{data, error}`。

## 安全声明

- API Key **明文存 SQLite**。本服务默认绑定 `127.0.0.1`，适合个人本地使用。
- **不推荐**公网 / 多人共用。部署到公网前请自行加反向代理 + BasicAuth。
- 日志中 `api_key` 会被截断为 `sk-…xxxx` 形式，不会完整打印。

## 限制（YAGNI）

- 不做邮件 / Slack / Webhook 通知
- 不做多用户 / 鉴权
- 不做分布式 worker（Celery + Redis）
- 不做主动 fail-over / 调用重路由
