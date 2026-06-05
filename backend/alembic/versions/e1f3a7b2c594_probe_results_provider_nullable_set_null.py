"""probe_results_provider_nullable_set_null

Make `probe_results.provider_id` nullable and change its ON DELETE
behavior from CASCADE to SET NULL, so that hard-deleting a provider
preserves the historical probe_results rows. Combined with the
`provider_uuid_at_probe` snapshot column, this lets historical reports
attribute probes to their original provider even after the int FK is
gone.

Revision ID: e1f3a7b2c594
Revises: d4f8a2c6b1e3
Create Date: 2026-06-05 02:10:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "e1f3a7b2c594"
down_revision: Union[str, Sequence[str], None] = "d4f8a2c6b1e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Rebuild probe_results to make provider_id nullable and change
    the FK from CASCADE to SET NULL. SQLite requires the full
    table-rebuild dance for this since FK behavior is part of the
    column definition, not a separate alterable constraint."""
    conn = op.get_bind()

    # Capture the existing schema for the columns we need to preserve.
    # We get the CREATE TABLE statement by inspecting sqlite_master.
    conn.execute(sa.text("PRAGMA foreign_keys = OFF;"))

    # Rename the original table to a temporary name.
    conn.execute(sa.text("ALTER TABLE probe_results RENAME TO _probe_results_old;"))

    # Recreate the table with the new schema. We re-declare every
    # column so the new table has the right types, NULL/NOT NULL
    # settings, and FK behaviors. The schema is sourced from the
    # current ORM model (app/db/models.py:ProbeResult).
    conn.execute(
        sa.text(
            """
            CREATE TABLE probe_results (
                id INTEGER NOT NULL,
                uuid_id CHAR(32) NOT NULL,
                provider_id INTEGER,
                model_id INTEGER,
                target VARCHAR(20) NOT NULL,
                success BOOLEAN NOT NULL,
                http_status INTEGER,
                latency_ms INTEGER,
                ttfb_ms INTEGER,
                error_code VARCHAR(10),
                error_message TEXT,
                checked_at DATETIME NOT NULL,
                provider_name_at_probe VARCHAR(120) NOT NULL,
                model_id_at_probe VARCHAR(300),
                provider_uuid_at_probe CHAR(32) NOT NULL,
                model_uuid_at_probe CHAR(32),
                PRIMARY KEY (id),
                CONSTRAINT ix_probe_results_uuid_id UNIQUE (uuid_id),
                FOREIGN KEY (model_id) REFERENCES models (id) ON DELETE SET NULL,
                FOREIGN KEY (provider_id) REFERENCES providers (id) ON DELETE SET NULL
            )
            """
        )
    )

    # Copy the data over.
    conn.execute(
        sa.text(
            """
            INSERT INTO probe_results (
                id, uuid_id, provider_id, model_id, target, success,
                http_status, latency_ms, ttfb_ms, error_code, error_message,
                checked_at, provider_name_at_probe, model_id_at_probe,
                provider_uuid_at_probe, model_uuid_at_probe
            )
            SELECT
                id, uuid_id, provider_id, model_id, target, success,
                http_status, latency_ms, ttfb_ms, error_code, error_message,
                checked_at, provider_name_at_probe, model_id_at_probe,
                provider_uuid_at_probe, model_uuid_at_probe
            FROM _probe_results_old
            """
        )
    )

    # Drop the old table.
    conn.execute(sa.text("DROP TABLE _probe_results_old;"))

    # Re-create the indexes that were on the original table.
    conn.execute(
        sa.text(
            "CREATE INDEX ix_probe_results_provider_model_time "
            "ON probe_results (provider_id, model_id, checked_at)"
        )
    )
    conn.execute(
        sa.text("CREATE INDEX ix_probe_results_checked_at ON probe_results (checked_at)")
    )
    conn.execute(
        sa.text(
            "CREATE INDEX ix_probe_results_provider_uuid_at_probe "
            "ON probe_results (provider_uuid_at_probe)"
        )
    )
    conn.execute(
        sa.text(
            "CREATE INDEX ix_probe_results_model_uuid_at_probe "
            "ON probe_results (model_uuid_at_probe)"
        )
    )

    # Re-enable FK enforcement. SQLite validates the table at this
    # point; if any existing rows reference a missing provider/model,
    # this will fail. That's intentional — we don't want to silently
    # accept orphan rows.
    conn.execute(sa.text("PRAGMA foreign_keys = ON;"))


def downgrade() -> None:
    """Restore NOT NULL + CASCADE. Will fail if any row has NULL provider_id."""
    conn = op.get_bind()
    null_count = conn.execute(
        sa.text("SELECT COUNT(*) FROM probe_results WHERE provider_id IS NULL")
    ).scalar()
    if null_count:
        raise RuntimeError(
            f"Cannot downgrade: {null_count} probe_results rows have NULL provider_id. "
            "Reassign or delete them first."
        )

    conn.execute(sa.text("PRAGMA foreign_keys = OFF;"))
    conn.execute(sa.text("ALTER TABLE probe_results RENAME TO _probe_results_old;"))
    conn.execute(
        sa.text(
            """
            CREATE TABLE probe_results (
                id INTEGER NOT NULL,
                uuid_id CHAR(32) NOT NULL,
                provider_id INTEGER NOT NULL,
                model_id INTEGER,
                target VARCHAR(20) NOT NULL,
                success BOOLEAN NOT NULL,
                http_status INTEGER,
                latency_ms INTEGER,
                ttfb_ms INTEGER,
                error_code VARCHAR(10),
                error_message TEXT,
                checked_at DATETIME NOT NULL,
                provider_name_at_probe VARCHAR(120) NOT NULL,
                model_id_at_probe VARCHAR(300),
                provider_uuid_at_probe CHAR(32) NOT NULL,
                model_uuid_at_probe CHAR(32),
                PRIMARY KEY (id),
                CONSTRAINT ix_probe_results_uuid_id UNIQUE (uuid_id),
                FOREIGN KEY (model_id) REFERENCES models (id) ON DELETE SET NULL,
                FOREIGN KEY (provider_id) REFERENCES providers (id) ON DELETE CASCADE
            )
            """
        )
    )
    conn.execute(
        sa.text(
            """
            INSERT INTO probe_results (
                id, uuid_id, provider_id, model_id, target, success,
                http_status, latency_ms, ttfb_ms, error_code, error_message,
                checked_at, provider_name_at_probe, model_id_at_probe,
                provider_uuid_at_probe, model_uuid_at_probe
            )
            SELECT
                id, uuid_id, provider_id, model_id, target, success,
                http_status, latency_ms, ttfb_ms, error_code, error_message,
                checked_at, provider_name_at_probe, model_id_at_probe,
                provider_uuid_at_probe, model_uuid_at_probe
            FROM _probe_results_old
            """
        )
    )
    conn.execute(sa.text("DROP TABLE _probe_results_old;"))
    conn.execute(
        sa.text(
            "CREATE INDEX ix_probe_results_provider_model_time "
            "ON probe_results (provider_id, model_id, checked_at)"
        )
    )
    conn.execute(sa.text("CREATE INDEX ix_probe_results_checked_at ON probe_results (checked_at)"))
    conn.execute(
        sa.text(
            "CREATE INDEX ix_probe_results_provider_uuid_at_probe "
            "ON probe_results (provider_uuid_at_probe)"
        )
    )
    conn.execute(
        sa.text(
            "CREATE INDEX ix_probe_results_model_uuid_at_probe "
            "ON probe_results (model_uuid_at_probe)"
        )
    )
    conn.execute(sa.text("PRAGMA foreign_keys = ON;"))
