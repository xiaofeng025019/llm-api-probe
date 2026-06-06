"""add probe_results retry_after_seconds

Revision ID: c1331e866b15
Revises: f6be65f28074
Create Date: 2026-06-06 21:21:18.855173

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1331e866b15'
down_revision: Union[str, Sequence[str], None] = 'f6be65f28074'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # NOTE: the autogenerate pass also detected an apscheduler_jobs
    # table and its index as "dropped", but that's a runtime artifact
    # of the APScheduler SQLAlchemyJobStore that the scheduler
    # recreates on every startup — not a real schema change. We drop
    # those operations to avoid destroying the in-progress job queue.
    with op.batch_alter_table('probe_results', schema=None) as batch_op:
        batch_op.add_column(sa.Column('retry_after_seconds', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('probe_results', schema=None) as batch_op:
        batch_op.drop_column('retry_after_seconds')
