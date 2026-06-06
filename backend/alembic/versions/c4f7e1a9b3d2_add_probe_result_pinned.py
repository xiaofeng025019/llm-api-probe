"""add probe_result pinned

Add a `pinned` boolean column to probe_results so the user can
keep specific errors visible past the retention window. Pinned
rows are excluded from the daily cleanup that runs over the
retention_days setting.

Revision ID: c4f7e1a9b3d2
Revises: e1f3a7b2c594
Create Date: 2026-06-05 21:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c4f7e1a9b3d2"
down_revision: Union[str, Sequence[str], None] = "e1f3a7b2c594"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("probe_results") as batch_op:
        batch_op.add_column(
            sa.Column(
                "pinned",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
    op.create_index(
        "ix_probe_results_pinned_checked_at",
        "probe_results",
        ["pinned", "checked_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_probe_results_pinned_checked_at", table_name="probe_results")
    with op.batch_alter_table("probe_results") as batch_op:
        batch_op.drop_column("pinned")
