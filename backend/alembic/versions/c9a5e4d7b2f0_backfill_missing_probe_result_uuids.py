"""backfill_missing_probe_result_uuids

Revision ID: c9a5e4d7b2f0
Revises: b6f1c2d4a9e1
Create Date: 2026-06-05 00:42:00.000000

"""

from collections.abc import Sequence
import uuid

from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = "c9a5e4d7b2f0"
down_revision: str | Sequence[str] | None = "b6f1c2d4a9e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Populate UUIDs for legacy probe rows that were inserted without one."""
    conn = op.get_bind()
    rows = conn.execute(text("SELECT id FROM probe_results WHERE uuid_id IS NULL")).fetchall()
    for (row_id,) in rows:
        conn.execute(
            text("UPDATE probe_results SET uuid_id = :uuid_id WHERE id = :id"),
            {"uuid_id": str(uuid.uuid4()), "id": row_id},
        )


def downgrade() -> None:
    """No-op: generated UUIDs should remain stable once exposed by the API."""
