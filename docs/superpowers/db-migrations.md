# 数据库迁移（Alembic）

本文档解释如何修改 schema 并把变更安全地应用到本地和生产数据库。

## 工作流程

```
1. 改 app/db/models.py（SQLAlchemy 模型）
2. uv run alembic revision --autogenerate -m "说明"
3. 检查生成的 versions/<rev>_说明.py，确认 upgrade()/downgrade()
4. 本地: uv run alembic upgrade head
5. 提交模型 + 迁移文件
6. 生产: 部署后服务启动时自动 alembic upgrade head（lifespan 调 init_db）
```

## 为什么需要它

之前 `init_db` 用 `Base.metadata.create_all()`，**只在表不存在时建表**。问题：

| 改动 | `create_all` | Alembic |
|---|---|---|
| 新增列 | ❌ 旧库没新列 | ✅ 升级脚本加列 |
| 删除列 | ❌ 旧库仍保留 | ✅ 升级脚本删列 |
| 改类型 | ❌ 旧库不变 | ✅ 类型转换脚本 |
| 改索引 | ❌ 旧库不变 | ✅ 索引迁移 |
| 重命名 | ❌ 不会发生 | ✅ rename |

第二次启动之后任何 schema 改动，依赖 `create_all` 都会**无声失败**，导致代码假设的列不存在、查询炸。

## 改 schema 实操

### 1. 改模型
```python
# app/db/models.py
class Provider(Base):
    # ... 新加一列
    region: Mapped[str | None] = mapped_column(String(40), nullable=True)
```

### 2. autogenerate
```bash
cd backend
uv run alembic revision --autogenerate -m "add region to providers"
```

会在 `alembic/versions/<hash>_add_region_to_providers.py` 生成脚本：
```python
def upgrade() -> None:
    with op.batch_alter_table('providers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('region', sa.String(length=40), nullable=True))

def downgrade() -> None:
    with op.batch_alter_table('providers', schema=None) as batch_op:
        batch_op.drop_column('region')
```

**autogenerate 不是万能** — 它不会检测：
- 表/列重命名（会显示成 drop+add，丢失数据）
- 复杂 CHECK 约束
- enum 改名
- 数据迁移

这些需要手写。

### 3. 检查 + 调
打开生成的脚本，确认：
- `upgrade()` 符合预期
- `downgrade()` 能回滚
- 数据迁移用 `op.execute("UPDATE ...")` 而不是裸 SQL

### 4. 本地试
```bash
uv run alembic upgrade head     # 应用
uv run alembic downgrade -1     # 回滚一版
uv run alembic upgrade head     # 再应用
```

跑测试确认 schema 匹配模型：
```bash
uv run pytest
```

### 5. 提交
`git add` 两个文件：
- `backend/app/db/models.py`（模型）
- `backend/alembic/versions/<rev>_*.py`（迁移）

**两个一起提交**。单独提交迁移或单独提交模型，部署会炸。

## 部署时

`app/db/__init__.py:init_db` 在 lifespan 启动时自动跑 `alembic upgrade head`。所以：
- 部署新版本 → 启动时自动迁移
- 启动失败 → 退出码非 0，Docker 自动重启
- 不会丢数据：迁移在事务里（SQLite 借 `render_as_batch=True`）

## 回滚

```bash
uv run alembic downgrade -1    # 回滚一版
uv run alembic downgrade base  # 回到起点（删所有表）
```

回滚到 base **会丢所有数据**。生产回滚前先备份 `data/llm_usability.db`。

## 常见操作速查

| 命令 | 作用 |
|---|---|
| `alembic current` | 当前版本 |
| `alembic history --verbose` | 所有迁移列表 |
| `alembic upgrade head` | 升级到最新 |
| `alembic downgrade -1` | 降一版 |
| `alembic stamp head` | 标记为最新（不跑迁移，新库已有同 schema 时用） |
| `alembic revision -m "msg"` | 手写空迁移（autogenerate 帮不上时） |
| `alembic revision --autogenerate -m "msg"` | 自动生成（推荐） |

## 测试

`tests/test_db_init.py` 端到端验证：
- 空 DB → init_db 跑完 → 5 张业务表 + alembic_version 存在 + 默认 settings 写入
- 重复 init_db 不会出错（幂等）

```bash
uv run pytest tests/test_db_init.py -v
```

## 一次性数据迁移范例

如果需要改列类型且要保留数据：

```python
def upgrade() -> None:
    # 1) 加新列
    with op.batch_alter_table('providers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('interval_seconds_new', sa.Integer()))
    # 2) 拷贝数据
    op.execute("UPDATE providers SET interval_seconds_new = interval_seconds")
    # 3) 删旧列
    with op.batch_alter_table('providers', schema=None) as batch_op:
        batch_op.drop_column('interval_seconds')
        batch_op.alter_column('interval_seconds_new', new_column_name='interval_seconds')
```

这种用单事务包，但 SQLite + `render_as_batch=True` 会自动拆 batch。

## 不要做的事

- ❌ 在 `init_db` 里手写 `op.create_table` — 那是迁移的工作
- ❌ 直接改 `data/llm_usability.db` 绕开迁移 — 下次启动会试图迁移然后失败
- ❌ 删 `alembic_version` 表的行 — alembic 会以为从未跑过迁移，重头跑一遍
- ❌ 修改已发布的迁移脚本（改 hash）— 别人的本地库会进入 unknown revision 状态

## 现有用户升级路径（pre-Alembic → Alembic）

如果你在引入 Alembic **之前**已经用过本服务（用旧的 `create_all` 建过表），DB 已经有 5 张业务表但**没有** `alembic_version` 表。下次启动时 `alembic upgrade head` 会试图跑 `4a7be4f2d8b0` 迁移，里面全是 `op.create_table(...)`，会因"表已存在"失败。

**一次性 fix**：
```bash
cd backend
uv run alembic stamp head
```

`stamp head` 把当前 DB 标记为"已经在 head 修订"，不实际跑任何迁移。下次启动 `alembic upgrade head` 看到标记就直接跳过。

这步只需要在升级到含 Alembic 的版本时做一次。之后所有 schema 变更都按标准流程走。
