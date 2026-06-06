"""models_add_partial_unique_index_provider_model_active

Add a partial unique index on models(provider_id, model_id) WHERE
deleted_at IS NULL, so the DB itself rejects duplicate *active* rows
under the same (provider, model_id). Without this, two concurrent
manual `POST /providers/{id}/models` calls with the same model_id
both pass the application-level check-then-insert and create
duplicate rows, whose stats get double-counted on the dashboard.

Soft-deleted rows are exempt — they may share (provider_id, model_id)
with a later-recreated active row.

If duplicate active rows already exist (from earlier races), we
delete the older ones first (keeping the row with the highest id —
the most recent insert) so the unique index can be created without
an IntegrityError on existing data.

Revision ID: a5b7c2e83f10
Revises: c1331e866b15
Create Date: 2026-06-07 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a5b7c2e83f10"
down_revision: Union[str, Sequence[str], None] = "c1331e866b15"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the partial unique index. First clean up any existing duplicates
    by keeping only the highest-id active row per (provider_id, model_id)."""
    conn = op.get_bind()

    # Find and delete duplicate active rows (keep the one with the highest id).
    # This is a defensive step in case the data table already has races
    # from before this migration; if there are no duplicates the DELETE is a no-op.
    conn.execute(
        sa.text(
            """
            DELETE FROM models
            WHERE deleted_at IS NULL
              AND id NOT IN (
                  SELECT MAX(id) FROM models
                  WHERE deleted_at IS NULL
                  GROUP BY provider_id, model_id
              )
            """
        )
    )

    # SQLite supports partial unique indexes (WHERE clause) natively;
    # both Postgres and MySQL do too with the same syntax.
    conn.execute(
        sa.text(
            "CREATE UNIQUE INDEX uq_models_provider_model_active "
            "ON models (provider_id, model_id) "
            "WHERE deleted_at IS NULL"
        )
    )


def downgrade() -> None:
    """Drop the partial unique index. The non-unique covering index
    `ix_models_provider_model` stays in place."""
    conn = op.get_bind()
    conn.execute(sa.text("DROP INDEX IF EXISTS uq_models_provider_model_active"))
