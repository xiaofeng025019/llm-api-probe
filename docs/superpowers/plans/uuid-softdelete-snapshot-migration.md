# UUID + 软删除 + 快照字段迁移计划

**日期**: 2026-06-04  
**状态**: 计划中  
**预估工作量**: 8-10 小时

## 背景

当前系统使用自增 INTEGER 作为 `providers.id` 和 `models.id`，存在以下风险：
1. **ID 回收**: SQLite 在 DELETE 后可能复用 rowid，导致历史 probe results 指向错误的实体
2. **级联删除丢失上下文**: provider 被删除时 cascade 删除所有 probe results，丢失历史数据
3. **模型 ID 漂移**: probe result 引用 `model.id`（自增数字），但业务标识是 `model.model_id` 字符串

## 解决方案

### 1. UUID 替代自增 ID
- `providers.id`: `INTEGER` → `UUID` (36 字符)
- `models.id`: `INTEGER` → `UUID` (36 字符)
- `probe_results.id`: `INTEGER` → `UUID` (36 字符)
- 所有 FK 列同步更新

### 2. 软删除机制
- `providers` 和 `models` 表添加 `deleted_at: datetime | NULL`
- "删除"操作改为 `SET deleted_at = NOW()`
- 所有查询加 `WHERE deleted_at IS NULL`
- UI 显示已删除项时灰化/删除线

### 3. 快照字段
- `probe_results` 表添加:
  - `provider_name_at_probe: VARCHAR(120)` — probe 时的 provider name
  - `model_id_at_probe: VARCHAR(500)` — probe 时的 model.model_id 字符串
- 保留 FK 用于关联，但查询时优先用快照字段展示

## 影响范围

根据扫描报告，共涉及：
- **后端**: 20 个文件，约 175 处修改
- **前端**: 6 个文件，约 37 处修改
- **测试**: 4 个文件，约 33 处修改
- **迁移**: 2-3 个 alembic revision

## 分阶段实施计划

### Phase 1: 数据库模型更新 (预估 2h)
**目标**: 添加 UUID 列、快照字段、deleted_at 字段（保留旧列）

**步骤**:
1. 更新 `app/db/models.py`:
   - 添加 `uuid_id: Mapped[UUID]` 列（`default=uuid4`, `unique=True`, `index=True`）
   - 添加 `deleted_at: Mapped[datetime | None]` 列（默认 NULL）
   - `probe_results` 添加 `provider_name_at_probe: str` 和 `model_id_at_probe: str | None`
   - 保留旧的 `id: int` 列（暂时不删除，用于数据迁移）

2. 创建 alembic 迁移: `add_uuid_and_snapshot_fields.py`
   - 添加新列（nullable 或 default 填充）
   - 为现有数据生成 UUID
   - 添加索引和唯一约束

3. 更新 `app/core/scheduler.py`:
   - `_job_key()` 改用 `uuid_id`
   - `_provider_sems` 改用 UUID key
   - 所有 `_run_probe` 参数改为 UUID

4. **验收标准**:
   - `alembic upgrade head` 成功
   - 现有数据都有 UUID
   - `pytest tests/test_db_init.py` 通过
   - `pytest tests/test_scheduler.py` 通过

**提交**: `feat(db): add UUID columns, snapshot fields, and soft-delete support`

---

### Phase 2: Service 层更新 (预估 2h)
**目标**: 所有 service 函数改用 UUID 查询，添加软删除过滤

**步骤**:
1. `app/services/providers.py`:
   - 所有函数参数 `provider_id: int` → `provider_id: UUID`
   - 所有查询加 `.where(Provider.deleted_at.is_(None))`
   - `delete_provider()` 改为 `SET deleted_at = now()`
   - 返回的 provider 对象包含 `uuid_id`

2. `app/services/models.py`:
   - 所有函数参数 `provider_id: int`, `model_id: int` → `UUID`
   - 所有查询加 `.where(Model.deleted_at.is_(None))`
   - `delete_model()` 改为 `SET deleted_at = now()`（如果需要）
   - `upsert_discovered()` 使用 `uuid_id` 作为 key

3. `app/services/results.py`:
   - `record_outcome()` 参数改为 UUID，填充 `provider_name_at_probe` 和 `model_id_at_probe`
   - `list_results()` 参数改为 UUID
   - `dashboard()` 中的 `recent_per_model` dict key 改为 UUID
   - `DashboardFavoriteModel` 和 `DashboardProvider` 的 `id` 字段改为 UUID

4. **验收标准**:
   - `pytest tests/test_services.py` 全部通过
   - `pytest tests/test_api.py` 全部通过（可能需要临时修改测试）

**提交**: `feat(services): migrate to UUID and add soft-delete filtering`

---

### Phase 3: API 层更新 (预估 1.5h)
**目标**: 所有 API 端点改用 UUID 参数，返回 UUID

**步骤**:
1. `app/schemas/api.py`:
   - `ProviderOut.id`: `int` → `UUID`
   - `ModelOut.id`, `provider_id`: `int` → `UUID`
   - `ProbeResultOut.id`, `provider_id`, `model_id`: `int` → `UUID`
   - 添加 `provider_name_at_probe`, `model_id_at_probe` 字段

2. `app/api/v1/providers.py`:
   - 所有路径参数 `provider_id: int` → `provider_id: UUID`
   - FastAPI 会自动将字符串转为 UUID 对象

3. `app/api/v1/models.py`:
   - 路径参数 `model_id: int` → `model_id: UUID`

4. `app/api/v1/dashboard.py`:
   - query 参数 `provider_id: int | None` → `UUID | None`
   - query 参数 `model_id: int | None` → `UUID | None`
   - `probe_run()` 参数改为 UUID

5. **验收标准**:
   - `pytest tests/test_api.py` 全部通过（更新测试中的 ID 类型）
   - 手动测试: `curl http://localhost:8000/api/v1/providers/{uuid}` 返回正确数据

**提交**: `feat(api): migrate to UUID parameters and responses`

---

### Phase 4: 调度器深度更新 (预估 1.5h)
**目标**: 调度器完全改用 UUID，job_key 格式重写

**步骤**:
1. `app/core/scheduler.py`:
   - `_job_key()`: `f"p{uuid}:{target}:m{uuid_or_-}"`
   - `_provider_sems`: `dict[UUID, Semaphore]`
   - `_provider_locks`: `dict[UUID, Lock]`
   - `_run_probe()` 所有参数改为 UUID
   - `sync_jobs_for_provider()` 参数改为 UUID
   - `trigger_now()` 参数改为 UUID

2. 更新所有 job 匹配逻辑:
   - `job.id.startswith(f"p{provider_id}:")` 仍有效（UUID 字符串前缀）
   - 确保 `_make_job_id()` 生成的 ID 格式一致

3. 更新 `app/db/models.py` 中的 `JobState`:
   - `provider_id: UUID`
   - `model_id: UUID | None`

4. **验收标准**:
   - `pytest tests/test_scheduler.py` 全部通过
   - `pytest tests/test_e2e_real_http.py` 全部通过
   - 手动测试: 触发 probe，观察 job_key 格式为 UUID

**提交**: `feat(scheduler): migrate job keys and all parameters to UUID`

---

### Phase 5: 前端更新 (预估 1h)
**目标**: 前端类型定义和页面组件改用 UUID 字符串

**步骤**:
1. `frontend/src/api/types.ts`:
   - `Provider.id`: `number` → `string`
   - `ModelOut.id`, `provider_id`: `number` → `string`
   - `ProbeResult.id`, `provider_id`, `model_id`: `number` → `string`
   - 添加 `provider_name_at_probe`, `model_id_at_probe` 字段

2. `frontend/src/pages/ProvidersPage.tsx`:
   - `p.id` 类型自动跟随（TypeScript 会从 types.ts 推断）
   - 删除 `Number(p.id)` 转换（如果有）

3. `frontend/src/pages/ProviderDetailPage.tsx`:
   - `const providerId = id;`（不再 `Number(id)`）
   - 删除 `Number.isFinite(providerId)` 检查（改为 UUID 格式校验）

4. `frontend/src/pages/ModelsPage.tsx`:
   - `model_id: string` 类型更新

5. `frontend/src/pages/DashboardPage.tsx`:
   - `runningProviders: Record<string, boolean>`
   - `provider_id` 比较直接用字符串

6. `frontend/src/hooks/useDashboard.ts`:
   - `modelsByProvider: Record<string, ModelOut[]>`

7. **验收标准**:
   - `pnpm build` 成功（TypeScript 编译通过）
   - 手动测试: 前端页面正常显示，UUID 在 URL 和 UI 中正确显示

**提交**: `feat(frontend): migrate types and components to UUID strings`

---

### Phase 6: 清理旧列 (预估 0.5h)
**目标**: 删除旧的 `id: int` 列，完成迁移

**步骤**:
1. 创建 alembic 迁移: `drop_legacy_int_id_columns.py`
   - 删除 `providers.id`, `models.id`, `probe_results.id` 列
   - 重命名 `uuid_id` → `id`（或保留 `uuid_id`，取决于 ORM 配置）
   - 更新所有 FK 约束

2. 更新 `app/db/models.py`:
   - 删除旧的 `id: int` 列定义
   - 将 `uuid_id` 重命名为 `id`（如果选择重命名）

3. **验收标准**:
   - `alembic upgrade head` 成功
   - `pytest` 全部通过
   - 数据库中不再有 `INTEGER` 类型的 `id` 列

**提交**: `refactor(db): drop legacy INTEGER id columns, finalize UUID migration`

---

### Phase 7: 集成测试与文档更新 (预估 0.5h)
**目标**: 确保所有集成测试通过，更新文档

**步骤**:
1. 运行完整测试套件:
   - `pytest` — 后端所有测试
   - `pnpm test` — 前端测试（如果有）
   - `pnpm build` — 前端构建

2. 更新文档:
   - `CHANGELOG.md` — 添加 UUID 迁移条目
   - `docs/superpowers/db-migrations.md` — 添加新 revision 说明
   - `README.md` — 如果有 API 变更，更新 API 概览

3. **验收标准**:
   - 所有测试通过
   - 文档更新完成

**提交**: `test: verify UUID migration, update docs`

---

## 风险与缓解措施

### 风险 1: 数据迁移失败
**缓解**: 
- Phase 1 保留旧列，Phase 6 才删除
- 每个 phase 后提交，可随时回滚到上一个 phase

### 风险 2: 调度器 job_key 冲突
**缓解**:
- Phase 4 前备份 `alembic_version` 表
- 迁移后检查 `apscheduler_jobs` 表，确认所有 job 已更新

### 风险 3: 前端 URL 路由变化
**缓解**:
- Phase 5 只做类型更新，不改路由定义
- UUID 字符串在 URL 中仍可用，只是更长

### 风险 4: 测试中的硬编码 ID
**缓解**:
- Phase 2-4 逐步更新测试
- 使用 fixture 动态生成 UUID

## 回滚策略

如果某个 phase 失败：
1. `git revert <commit>` 回滚到上一个 phase
2. `alembic downgrade -1` 回滚数据库
3. 修复问题后重新执行该 phase

## 时间线

- **Phase 1**: 2h — 数据库模型更新
- **Phase 2**: 2h — Service 层更新
- **Phase 3**: 1.5h — API 层更新
- **Phase 4**: 1.5h — 调度器更新
- **Phase 5**: 1h — 前端更新
- **Phase 6**: 0.5h — 清理旧列
- **Phase 7**: 0.5h — 集成测试与文档

**总计**: 8-10 小时

## 下一步

确认此计划后，我将按 Phase 顺序逐步实施，每个 phase 完成后提交一次。
