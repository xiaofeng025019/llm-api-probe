"""UUID query helpers for SQLite legacy rows."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import String, cast, func


def uuid_equals(column: Any, value: uuid.UUID):
    """Match UUID columns stored as either 32-char hex or 36-char text.

    Early migrations populated SQLite UUID columns with ``str(uuid4())``
    while SQLAlchemy's Uuid bind processor compares against hex form. Normalize
    both shapes so existing local databases and freshly inserted rows work.
    """
    return func.lower(func.replace(cast(column, String), "-", "")) == value.hex
