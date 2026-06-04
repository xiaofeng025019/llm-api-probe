"""add_partial_unique_index_on_provider_name

Revision ID: 7ad1aec504b8
Revises: 0158e57e3489
Create Date: 2026-06-04 23:51:29.877720

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7ad1aec504b8'
down_revision: Union[str, Sequence[str], None] = '0158e57e3489'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Replace the unique index on providers.name with a partial unique index
    that only enforces uniqueness among active (non-soft-deleted) providers.
    This allows creating a new provider with the same name after the old one
    is soft-deleted."""
    with op.batch_alter_table('providers', schema=None) as batch_op:
        # Drop old unique index on name
        batch_op.drop_index(batch_op.f('ix_providers_name'))
        # Re-create as non-unique index (for query performance)
        batch_op.create_index(batch_op.f('ix_providers_name'), ['name'], unique=False)
        # Add partial unique index: name is unique only among active providers
        batch_op.create_index(
            'ix_providers_name_active', ['name'], unique=True,
            sqlite_where=sa.text('deleted_at IS NULL'),
        )


def downgrade() -> None:
    """Revert to the original unique index on providers.name."""
    with op.batch_alter_table('providers', schema=None) as batch_op:
        batch_op.drop_index('ix_providers_name_active', sqlite_where=sa.text('deleted_at IS NULL'))
        batch_op.drop_index(batch_op.f('ix_providers_name'))
        batch_op.create_index(batch_op.f('ix_providers_name'), ['name'], unique=True)
