"""Merge Author rows that `_find_or_create_author` would now unify.

    python scripts/repair_forked_authors.py            # dry run — show the merges
    python scripts/repair_forked_authors.py --write    # apply

Before prompts/15 Stage J, `_find_or_create_author` matched on a plain word
set, so "J.R.R. Tolkien" / "J. R. R. Tolkien" / "Tolkien, J.R.R." each got
their own row, and a collaboration credit ("Dean Koontz; Queenie Chan") got
its own row instead of resolving to the primary author. New scans no longer
fork either; this repairs the ones already forked.

Pass 1 — within a `normalize_person_name` key group:

  * **Solo-name variants** ("J.R.R. Tolkien" / "Tolkien, J.R.R." /
    "Dean R. Koontz" vs "Dean Koontz") merge only with book-level
    corroboration — a shared ISBN or a shared strict-normalised title —
    because "J. Smith" and "John Smith" also share a key but may be two
    people. The cleanest solo name (shortest) is canonical.
  * **Collaboration credits** whose primary author is the canonical
    ("Dean Koontz; Queenie Chan" → "Dean Koontz") always fold in —
    REVIEW-2026-09-08 policy: a co-authored book is filed under the primary
    author.

Pass 2 (prompts/28) — `Author.hardcover_person_id` groups. Hardcover asserts
these rows are one person (canonical + alias walk, resolved by
`hardcover_new_releases_service`), which stands in for the shared-book
requirement:

  * Same `normalize_person_name` key across the group → auto-merge
    ("Iain M. Banks" / "Iain Banks", which pass 1 skips for want of a shared
    book).
  * Different keys (a pen name ↔ legal name) → only printed as SUGGEST;
    that's a bigger claim, merge it by hand.

Books are repointed to the canonical row; the emptied rows are deleted.
Mirrors title_merge_repair_service. Dry-run first; `--write` to apply.
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
    is_collaboration,
    looks_solo,
    normalize_person_name,
    normalize_title_strict,
    person_sort_name,
    primary_author_name,
)


async def _apply_merge(session, canonical: Author, merge_in: list[Author]) -> None:
    for a in merge_in:
        for b in list(a.books):
            b.author = canonical
        await session.flush()
        await session.delete(a)
    if canonical.sort_name is None:
        canonical.sort_name = person_sort_name(canonical.name) or None


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

            solos = [a for a in group if looks_solo(a.name)]
            collabs = [a for a in group if is_collaboration(a.name)]

            # Canonical = the cleanest solo name in the group (shortest wins —
            # "Dean Koontz" over "Dean Koontz and Kevin J. Anderson"); if the
            # group is all collaboration credits, synthesise the primary.
            if solos:
                canonical = min(solos, key=lambda a: (len(a.name), a.id))
                canonical_solos = solos
            else:
                canonical = min(group, key=lambda a: a.id)
                canonical.name = primary_author_name(canonical.name).strip() or canonical.name
                canonical_solos = [canonical]

            merge_in: list[Author] = []

            # Solo-name variants ("J.R.R. Tolkien" / "Tolkien, J.R.R." /
            # "Dean Koontz" / "Dean R. Koontz") only merge with book-level
            # corroboration — "J. Smith" and "John Smith" share a key but may be
            # two people.
            other_solos = [a for a in canonical_solos if a.id != canonical.id]
            if other_solos:
                keys_by_id = {a.id: _book_keys(a.books, isbns) for a in canonical_solos}
                for a in other_solos:
                    if keys_by_id[a.id] & keys_by_id[canonical.id]:
                        merge_in.append(a)
                    else:
                        print(
                            f"  SKIP {a.name!r} -> {canonical.name!r}: no shared book/ISBN "
                            "(could be a different person)"
                        )

            # Collaboration credits ("Dean Koontz; Queenie Chan") whose primary
            # author IS this canonical always fold in — REVIEW-2026-09-08 policy:
            # a co-authored book is filed under the primary author.
            for a in collabs:
                if a.id != canonical.id and normalize_person_name(a.name) == key:
                    merge_in.append(a)

            if not merge_in:
                continue

            print(f"  MERGE -> {canonical.name!r}  <=  {[a.name for a in merge_in]}")
            merges += 1
            if write:
                await _apply_merge(session, canonical, merge_in)

        # -- Pass 2: prompts/28 Hardcover person-id groups ------------------
        # Re-load — pass 1 may have deleted rows (flushed, not yet committed).
        authors = (
            (await session.execute(select(Author).options(selectinload(Author.books))))
            .scalars()
            .all()
        )
        by_person: dict[int, list[Author]] = defaultdict(list)
        for a in authors:
            if a.hardcover_person_id is not None:
                by_person[a.hardcover_person_id].append(a)

        for pid, group in by_person.items():
            if len(group) < 2:
                continue
            # Shortest name is the clean form ("Iain Banks" over "Iain M.
            # Banks") — same rule pass 1 uses for solo variants.
            canonical = min(group, key=lambda a: (len(a.name), a.id))
            others = [a for a in group if a.id != canonical.id]
            if len({normalize_person_name(a.name) for a in group}) == 1:
                print(
                    f"  MERGE (Hardcover person {pid}) -> {canonical.name!r}  "
                    f"<=  {[a.name for a in others]}"
                )
                merges += 1
                if write:
                    await _apply_merge(session, canonical, others)
            else:
                print(
                    f"  SUGGEST (Hardcover person {pid}, pen name?): "
                    f"{sorted(a.name for a in group)} — merge by hand if right"
                )

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
