"""add_model_status_confirmation_fields

Revision ID: b6f1c2d4a9e1
Revises: 7ad1aec504b8
Create Date: 2026-06-04 22:35:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b6f1c2d4a9e1"
down_revision: Union[str, Sequence[str], None] = "7ad1aec504b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("models", schema=None) as batch_op:
        batch_op.add_column(sa.Column("status", sa.String(length=40), nullable=False, server_default="unknown"))
        batch_op.add_column(sa.Column("status_reason", sa.String(length=300), nullable=True))
        batch_op.add_column(sa.Column("status_checked_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("status_confirmed_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"))

    with op.batch_alter_table("models", schema=None) as batch_op:
        batch_op.alter_column("status", server_default=None)
        batch_op.alter_column("consecutive_failures", server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("models", schema=None) as batch_op:
        batch_op.drop_column("consecutive_failures")
        batch_op.drop_column("last_success_at")
        batch_op.drop_column("status_confirmed_at")
        batch_op.drop_column("status_checked_at")
        batch_op.drop_column("status_reason")
        batch_op.drop_column("status")
