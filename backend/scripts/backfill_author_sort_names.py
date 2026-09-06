"""Populate Author.sort_name for rows that predate prompts/15 Stage J.

    python scripts/backfill_author_sort_names.py            # dry run — show what would change
    python scripts/backfill_author_sort_names.py --write    # apply

`Author.sort_name` has been in the schema since v1 and was never filled. This
derives it ("Brandon Sanderson" -> "Sanderson, Brandon", surname particles
handled) from the existing display name. It never touches `Author.name`.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from sqlalchemy import select  # noqa: E402

from app.data.db import async_session_factory  # noqa: E402
from app.data.models import Author  # noqa: E402
from app.services.text_match import person_sort_name  # noqa: E402


async def main(write: bool) -> None:
    async with async_session_factory() as session:
        authors = (await session.execute(select(Author))).scalars().all()
        changes: list[tuple[str, str | None, str]] = []
        for a in authors:
            new = person_sort_name(a.name) or None
            if new and new != a.sort_name:
                changes.append((a.name, a.sort_name, new))
                if write:
                    a.sort_name = new
        for name, old, new in changes:
            print(f"  {name!r}: {old!r} -> {new!r}")
        print(f"\n{len(changes)} of {len(authors)} authors {'updated' if write else 'would change'}")
        if write:
            await session.commit()
            print("committed.")
        else:
            print("dry run — pass --write to apply.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help="apply the changes")
    asyncio.run(main(ap.parse_args().write))
