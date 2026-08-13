from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.data.models import Author, Book, Series
from app.schemas.library_audit import LibraryAuditResult, SimilarNameCluster, SimilarNameMember
from app.services.text_match import normalize, normalize_words

# Anything reaching this check already failed the exact word-set match that
# resolve_book/_find_or_create_series use to dedupe at identification time
# (SPEC.md — same words, different order/punctuation, reuse the row) — so a
# name pair only gets here when the AI/provider phrased the same series or
# author differently enough to fork a second DB row (and, since
# organize_service names folders after Series.name, a second Drive folder
# too). This is a heuristic "worth a look" signal, not a certainty — the
# report is read-only and left for human review.
_SIMILARITY_THRESHOLD = 0.5
_MIN_LENGTH = 3


def _is_similar(a: str, b: str) -> bool:
    na, nb = normalize(a), normalize(b)
    if len(na) < _MIN_LENGTH or len(nb) < _MIN_LENGTH:
        return False
    if SequenceMatcher(None, na, nb).ratio() >= _SIMILARITY_THRESHOLD:
        return True
    # Catches abbreviation-style splits ("Dune" vs "Dune Chronicles") that
    # SequenceMatcher's ratio undercounts once the length gap gets large.
    wa, wb = normalize_words(a), normalize_words(b)
    return bool(wa) and bool(wb) and (wa <= wb or wb <= wa)


_Row = tuple[int, str, int, int]  # id, name, book_count, file_count


def _cluster(rows: list[_Row]) -> list[SimilarNameCluster]:
    parent = {row[0]: row[0] for row in rows}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            if _is_similar(rows[i][1], rows[j][1]):
                union(rows[i][0], rows[j][0])

    groups: dict[int, list[_Row]] = {}
    for row in rows:
        groups.setdefault(find(row[0]), []).append(row)

    return [
        SimilarNameCluster(
            members=[
                SimilarNameMember(id=r[0], name=r[1], book_count=r[2], file_count=r[3])
                for r in sorted(members, key=lambda r: r[1])
            ]
        )
        for members in groups.values()
        if len(members) > 1
    ]


async def audit_library(session: AsyncSession) -> LibraryAuditResult:
    """Read-only: flags Series/Author rows whose names look like they might
    be the same thing split across two records (and, by construction of
    organize_service.build_target_path, two Drive folders). Fixing is
    manual today — move the files in Drive, then Rebuild library to
    re-derive Author/Series/Book from wherever they actually ended up."""
    series_rows = (
        (await session.execute(select(Series).options(selectinload(Series.books).selectinload(Book.files))))
        .scalars()
        .all()
    )
    author_rows = (
        (await session.execute(select(Author).options(selectinload(Author.books).selectinload(Book.files))))
        .scalars()
        .all()
    )

    series_tuples: list[_Row] = [
        (s.id, s.name, len(s.books), sum(len(b.files) for b in s.books)) for s in series_rows
    ]
    author_tuples: list[_Row] = [
        (a.id, a.name, len(a.books), sum(len(b.files) for b in a.books)) for a in author_rows
    ]

    return LibraryAuditResult(
        similar_series=_cluster(series_tuples),
        similar_authors=_cluster(author_tuples),
    )
