"""Alembic environment — driven by the project's settings/env.

Uses a sync engine (Alembic's standard mode) because:
  * Migrations on SQLite are a synchronous op — no benefit to async.
  * The async app uses a separate async engine at runtime.
  * The async template forces aiosqlite, but Alembic's own runtime
    and CLI tooling don't need it.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# Make `app.*` importable regardless of CWD.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings
from app.db.session import Base

config = context.config

# Override sqlalchemy.url in alembic.ini with the runtime value.
config.set_main_option("sqlalchemy.url", get_settings().sync_database_url)

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# target_metadata is what `alembic revision --autogenerate` diffs against.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a live DB)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations online using a sync engine."""
    from sqlalchemy import event as _sa_event

    section = dict(config.get_section(config.config_ini_section, {}))
    section["sqlalchemy.connect_args"] = {"check_same_thread": False, "timeout": 10}
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    @_sa_event.listens_for(connectable, "connect")
    def _set_pragma(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # SQLite-friendly ALTER TABLE
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
