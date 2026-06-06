"""add probe_results model_id checked_at index

Revision ID: f6be65f28074
Revises: c4f7e1a9b3d2
Create Date: 2026-06-06 18:45:00.107841

Adds a (model_id, checked_at) composite index so the dashboard's
model-only latest-probe lookup doesn't fall through to a full table
scan. Mirrors the existing (provider_id, model_id, checked_at) index
for queries that filter on model_id alone.

Note: the autogenerate pass at migration time also picked up a
handful of pre-existing drift (apscheduler_jobs table, probetarget
enum cast, index name changes) that are unrelated to this fix and
left out of this migration to keep the diff minimal.
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "f6be65f28074"
down_revision: Union[str, Sequence[str], None] = "c4f7e1a9b3d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_probe_results_model_time",
        "probe_results",
        ["model_id", "checked_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_probe_results_model_time", table_name="probe_results")
