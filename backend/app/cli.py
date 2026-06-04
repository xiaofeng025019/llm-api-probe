"""Command-line helpers for managing local data.

Usage:
  uv run python -m app.cli list-providers
  uv run python -m app.cli cleanup-test-data
  uv run python -m app.cli cleanup-test-data --yes
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Iterable

from sqlalchemy import select

from app.db import init_db
from app.db.models import Provider
from app.db.session import get_session_maker

# Provider name / api_key patterns that suggest leftover from a manual
# smoke test or a one-off curl while developing. Match on exact short
# names ("t", "test") and well-known fake keys.
_NAME_PATTERNS = {"t", "test", "demo", "smoke", "example", "tmp", "temp", "x"}
_KEY_PATTERNS = {"sk-test", "sk-fake", "k", "test", "demo", "xxx"}


def _looks_like_test_data(name: str, api_key: str) -> bool:
    n = (name or "").strip().lower()
    k = (api_key or "").strip().lower()
    if n in _NAME_PATTERNS:
        return True
    if k in _KEY_PATTERNS:
        return True
    return n.startswith("test-") or n.startswith("tmp-") or k.startswith("sk-test") or k.startswith("sk-fake")


async def _ensure_db() -> None:
    """CLI commands are run from anywhere and may pre-date migrations.
    Run alembic upgrade head so list/cleanup don't 500 on missing tables."""
    await init_db()


async def list_providers() -> list[Provider]:
    await _ensure_db()
    sm = get_session_maker()
    async with sm() as s:
        res = await s.execute(select(Provider).order_by(Provider.id))
        return list(res.scalars().all())


async def cleanup_test_data(yes: bool) -> int:
    await _ensure_db()
    providers = await list_providers()
    suspects = [p for p in providers if _looks_like_test_data(p.name, p.api_key)]
    if not suspects:
        print("No test-data providers found. Done.")
        return 0

    print(f"Found {len(suspects)} provider(s) matching test-data patterns:")
    for p in suspects:
        key_preview = p.api_key[:6] + "..." if p.api_key else "(empty)"
        print(f"  id={p.id}  name={p.name!r}  key={key_preview}")

    if not yes:
        print("\nRe-run with --yes to delete.")
        return len(suspects)

    sm = get_session_maker()
    async with sm() as s:
        for p in suspects:
            fresh = await s.get(Provider, p.id)
            if fresh is not None:
                await s.delete(fresh)
        await s.commit()
    print(f"Deleted {len(suspects)} provider(s).")
    return len(suspects)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list-providers", help="List all configured providers")

    p_clean = sub.add_parser(
        "cleanup-test-data",
        help="Delete providers whose name/key matches common test patterns",
    )
    p_clean.add_argument("--yes", action="store_true", help="Actually delete (default: list only)")

    args = parser.parse_args(argv)

    if args.cmd == "list-providers":
        ps = asyncio.run(list_providers())
        for p in ps:
            key_preview = p.api_key[:6] + "..." if p.api_key else "(empty)"
            print(
                f"id={p.id:>3}  enabled={p.enabled!s:<5}  "
                f"name={p.name!r:<24}  kind={p.kind.value:<14}  "
                f"key={key_preview}"
            )
        print(f"\nTotal: {len(ps)} provider(s)")
        return 0

    if args.cmd == "cleanup-test-data":
        n = asyncio.run(cleanup_test_data(args.yes))
        # Exit 0 when nothing to do, or when --yes deleted. Exit 1 when
        # suspects were listed and the user needs to confirm.
        return 0 if args.yes or n == 0 else 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
