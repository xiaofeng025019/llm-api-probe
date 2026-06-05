"""add_probe_result_uuid_snapshots

Add `provider_uuid_at_probe` and `model_uuid_at_probe` UUID snapshot
columns to `probe_results` so that historical records carry a stable
identifier that survives:

- `probe_results.provider_id` / `probe_results.model_id` int FKs being
  reassigned (model hard-delete + re-insert, cross-database restore)
- Provider or model renames
- Schema migrations that re-seed int PKs

Backfills existing rows from the live `providers.uuid_id` /
`models.uuid_id` joined on the current int FKs.

Revision ID: d4f8a2c6b1e3
Revises: c9a5e4d7b2f0
Create Date: 2026-06-05 00:55:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "d4f8a2c6b1e3"
down_revision: Union[str, Sequence[str], None] = "c9a5e4d7b2f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add UUID snapshot columns and backfill from existing FKs."""
    # 1. Add the two columns (nullable for the backfill step).
    with op.batch_alter_table("probe_results", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("provider_uuid_at_probe", sa.Uuid(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("model_uuid_at_probe", sa.Uuid(), nullable=True)
        )

    # 2. Backfill from live FKs.
    # Every probe_result has a provider_id, so provider_uuid_at_probe
    # can always be populated. model_uuid_at_probe is NULL for probes
    # that don't target a specific model (list_models with model_id NULL).
    conn = op.get_bind()

    # Backfill provider_uuid_at_probe from providers.uuid_id.
    conn.execute(
        sa.text(
            """
            UPDATE probe_results
            SET provider_uuid_at_probe = (
                SELECT uuid_id FROM providers
                WHERE providers.id = probe_results.provider_id
            )
            WHERE provider_uuid_at_probe IS NULL
            """
        )
    )

    # Backfill model_uuid_at_probe from models.uuid_id (only for rows
    # that have a model_id).
    conn.execute(
        sa.text(
            """
            UPDATE probe_results
            SET model_uuid_at_probe = (
                SELECT uuid_id FROM models
                WHERE models.id = probe_results.model_id
            )
            WHERE model_id IS NOT NULL AND model_uuid_at_probe IS NULL
            """
        )
    )

    # 3. Add indexes for the snapshot lookups.
    with op.batch_alter_table("probe_results", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_probe_results_provider_uuid_at_probe"),
            ["provider_uuid_at_probe"],
        )
        batch_op.create_index(
            batch_op.f("ix_probe_results_model_uuid_at_probe"),
            ["model_uuid_at_probe"],
        )

    # 4. Tighten nullability. provider_uuid_at_probe is NOT NULL because
    # every probe has a provider. model_uuid_at_probe stays nullable —
    # list_models probes without a model id are valid.
    with op.batch_alter_table("probe_results", schema=None) as batch_op:
        batch_op.alter_column("provider_uuid_at_probe", nullable=False)


def downgrade() -> None:
    """Drop the UUID snapshot columns and their indexes."""
    with op.batch_alter_table("probe_results", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_probe_results_model_uuid_at_probe"))
        batch_op.drop_index(batch_op.f("ix_probe_results_provider_uuid_at_probe"))
        batch_op.drop_column("model_uuid_at_probe")
        batch_op.drop_column("provider_uuid_at_probe")
