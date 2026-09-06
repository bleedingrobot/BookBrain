"""Merge Author rows that prompts/15 Stage J's canonicaliser would now unify.

    python scripts/repair_forked_authors.py            # dry run — show the merges
    python scripts/repair_forked_authors.py --write    # apply

Before Stage J, `_find_or_create_author` matched on a plain word set, so
"J.R.R. Tolkien" / "J. R. R. Tolkien" / "Tolkien, J.R.R." each got their own
row. New scans no longer fork them; this repairs the ones already forked.

Conservative, per the Stage J gotcha ("two different authors can share
initials"): a group is only merged when every name in it shares the same
`normalize_person_name` key AND the group's books share at least one ISBN or a
normalised title — evidence they're really one person, not "J. Smith" vs
"John Smith". Books are repointed to the row with the most complete display
name; the emptied rows are deleted. Mirrors title_merge_repair_service.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from app.data.db import async_session_factory  # noqa: E402
from app.data.models import Author, Book, Identifier  # noqa: E402
from app.services.book_repository import get_book_write_lock  # noqa: E402
from app.services.text_match import (  # noqa: E402
    normalize_person_name,
    normalize_title_strict,
    person_sort_name,
)


def _book_keys(books: list[Book], isbns: dict[int, set[str]]) -> set[str]:
    keys: set[str] = set()
    for b in books:
        keys |= {f"isbn:{v}" for v in isbns.get(b.id, set())}
        keys.add(f"title:{normalize_title_strict(b.canonical_title)}")
    return keys


async def main(write: bool) -> None:
    async with async_session_factory() as session:
        authors = (
            (await session.execute(select(Author).options(selectinload(Author.books))))
            .scalars()
            .all()
        )
        ident_rows = (await session.execute(select(Identifier))).scalars().all()
        isbns: dict[int, set[str]] = defaultdict(set)
        for i in ident_rows:
            isbns[i.book_id].add(i.value)

        by_key: dict[str, list[Author]] = defaultdict(list)
        for a in authors:
            key = normalize_person_name(a.name)
            if key:
                by_key[key].append(a)

        merges = 0
        for key, group in by_key.items():
            if len(group) < 2:
                continue
            # require book-level corroboration: at least two rows in the group
            # share a book (same ISBN or same strict-normalised title). Without
            # that, "J. Smith" and "John Smith" could be two different people.
            all_keys = [_book_keys(a.books, isbns) for a in group]
            shared = any(
                all_keys[i] & all_keys[j]
                for i in range(len(group))
                for j in range(i + 1, len(group))
            )
            if not shared:
                print(f"  SKIP {key!r}: {[a.name for a in group]} — no shared book/ISBN")
                continue

            canonical = max(group, key=lambda a: (len(a.name), a.id))
            others = [a for a in group if a.id != canonical.id]
            print(f"  MERGE -> {canonical.name!r}  <=  {[a.name for a in others]}")
            merges += 1
            if write:
                for a in others:
                    for b in list(a.books):
                        b.author = canonical
                    await session.flush()
                    await session.delete(a)
                if canonical.sort_name is None:
                    canonical.sort_name = person_sort_name(canonical.name) or None

        print(f"\n{merges} author group(s) {'merged' if write else 'would merge'}")
        if write:
            async with get_book_write_lock():
                await session.commit()
            print("committed.")
        else:
            print("dry run — pass --write to apply.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true")
    asyncio.run(main(ap.parse_args().write))
