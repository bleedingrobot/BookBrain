from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.data.models import AuditClusterKind, Author, Book, DismissedAuditCluster, Series
from app.schemas.library_audit import (
    DismissedClusterInfo,
    LibraryAuditResult,
    SimilarNameCluster,
    SimilarNameMember,
)
from app.services.text_match import normalize, normalize_words

# Anything reaching this check already failed the exact word-set match that
# resolve_book/_find_or_create_series use to dedupe at identification time
# (SPEC.md — same words, different order/punctuation, reuse the row) — so a
# name pair only gets here when the AI/provider phrased the same series or
# author differently enough to fork a second DB row (and, since
# organize_service names folders after Series.name, a second Drive folder
# too). This is a heuristic "worth a look" signal, not a certainty — the
# report is read-only and left for human review.
_SIMILARITY_THRESHOLD = 0.72
_MIN_LENGTH = 3

# Tuned against a real ~100-series library, not just synthetic examples:
# comparing raw normalized strings let a long shared *generic* word decide
# the match while the actually-distinguishing word differed completely —
# "The Farseer Trilogy" vs "The Coldfire Trilogy" scored 0.74 purely off
# "The"/"Trilogy", nothing to do with Farseer vs Coldfire. Stripping this
# vocabulary before comparing fixes that at the source, rather than trying
# to out-tune a threshold against it.
_STRUCTURAL_WORDS = frozenset(
    {
        "the", "a", "an", "and", "of", "in",
        "saga", "series", "trilogy", "duology", "quartet", "quintet",
        "cycle",
        "book", "books", "vol", "volume", "part", "world",
    }
)


def _significant_words(text: str) -> frozenset[str]:
    return normalize_words(text) - _STRUCTURAL_WORDS


def _is_similar(a: str, b: str) -> bool:
    na, nb = normalize(a), normalize(b)
    if len(na) < _MIN_LENGTH or len(nb) < _MIN_LENGTH:
        return False

    sa, sb = _significant_words(a), _significant_words(b)
    if not sa or not sb:
        # Nothing distinctive left to compare (e.g. a name that's entirely
        # structural words) — no basis to call it similar to anything.
        return False
    if sa <= sb or sb <= sa:
        return True

    # Ratio on the significant words only, not the raw names — otherwise a
    # long shared generic word (see above) can carry a match on its own.
    ratio = SequenceMatcher(None, "".join(sorted(sa)), "".join(sorted(sb))).ratio()
    return ratio >= _SIMILARITY_THRESHOLD


_Row = tuple[int, str, int, int]  # id, name, book_count, file_count


def _cluster(rows: list[_Row]) -> list[SimilarNameCluster]:
    """Groups by pairwise similarity, but never transitively — connecting
    A-B and B-C must not imply A-C. Real data showed this go badly wrong:
    one library-wide component grew to 60+ unrelated series through a
    chain of individually-plausible-looking but unrelated pairs. A
    component is only reported as one cluster if it's a genuine clique
    (every pair in it is similar); otherwise it's broken down into its
    individual similar pairs, each its own cluster — a name can end up in
    more than one, which is correct (e.g. "Dune" pairing separately with
    both "Dune Chronicles" and "Dune Saga" even if those two aren't
    themselves similar to each other)."""
    by_id = {row[0]: row for row in rows}
    edges: dict[int, set[int]] = {row[0]: set() for row in rows}
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            if _is_similar(rows[i][1], rows[j][1]):
                edges[rows[i][0]].add(rows[j][0])
                edges[rows[j][0]].add(rows[i][0])

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

    for a, neighbors in edges.items():
        for b in neighbors:
            union(a, b)

    components: dict[int, list[int]] = {}
    for row in rows:
        components.setdefault(find(row[0]), []).append(row[0])

    def to_cluster(ids: list[int]) -> SimilarNameCluster:
        return SimilarNameCluster(
            members=[
                SimilarNameMember(id=r[0], name=r[1], book_count=r[2], file_count=r[3])
                for r in sorted((by_id[i] for i in ids), key=lambda r: r[1])
            ]
        )

    clusters: list[SimilarNameCluster] = []
    seen_pairs: set[frozenset[int]] = set()
    for ids in components.values():
        if len(ids) < 2:
            continue
        is_clique = all(b in edges[a] for a in ids for b in ids if a != b)
        if is_clique:
            clusters.append(to_cluster(ids))
            continue
        for a in ids:
            for b in edges[a]:
                pair = frozenset((a, b))
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    clusters.append(to_cluster(sorted(pair)))

    return clusters


def _cluster_key(member_ids: list[int]) -> str:
    return ",".join(str(i) for i in sorted(set(member_ids)))


async def _dismissed_keys(session: AsyncSession, kind: AuditClusterKind) -> set[str]:
    rows = (
        (
            await session.execute(
                select(DismissedAuditCluster.member_ids_key).where(DismissedAuditCluster.kind == kind)
            )
        )
        .scalars()
        .all()
    )
    return set(rows)


async def audit_library(session: AsyncSession) -> LibraryAuditResult:
    """Read-only: flags Series/Author rows whose names look like they might
    be the same thing split across two records (and, by construction of
    organize_service.build_target_path, two Drive folders). Fixing series
    clusters can be done in place via propose_series_merge/apply_series_merge;
    author clusters (and any cluster the user has decided not to act on,
    dismissed below) still need the manual Drive-move + Rebuild workflow."""
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

    dismissed_series = await _dismissed_keys(session, AuditClusterKind.series)
    dismissed_authors = await _dismissed_keys(session, AuditClusterKind.author)

    def _not_dismissed(cluster: SimilarNameCluster, dismissed: set[str]) -> bool:
        return _cluster_key([m.id for m in cluster.members]) not in dismissed

    return LibraryAuditResult(
        similar_series=[c for c in _cluster(series_tuples) if _not_dismissed(c, dismissed_series)],
        similar_authors=[c for c in _cluster(author_tuples) if _not_dismissed(c, dismissed_authors)],
    )


async def dismiss_cluster(session: AsyncSession, kind: AuditClusterKind, member_ids: list[int]) -> None:
    """Idempotent: dismissing an already-dismissed cluster (same kind +
    exact member-id-set) is a no-op, not an error."""
    key = _cluster_key(member_ids)
    existing = (
        await session.execute(
            select(DismissedAuditCluster).where(
                DismissedAuditCluster.kind == kind, DismissedAuditCluster.member_ids_key == key
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return
    session.add(DismissedAuditCluster(kind=kind, member_ids_key=key))
    try:
        await session.commit()
    except IntegrityError:
        # Lost a race with a concurrent dismiss of the exact same cluster —
        # the outcome (dismissed) is the same either way.
        await session.rollback()


async def list_dismissed_clusters(session: AsyncSession) -> list[DismissedClusterInfo]:
    rows = (
        (await session.execute(select(DismissedAuditCluster).order_by(DismissedAuditCluster.created_at.desc())))
        .scalars()
        .all()
    )
    return [
        DismissedClusterInfo(
            id=r.id,
            kind=r.kind.value,
            member_ids=[int(x) for x in r.member_ids_key.split(",")],
            created_at=r.created_at,
        )
        for r in rows
    ]


async def undismiss_cluster(session: AsyncSession, dismissed_id: int) -> None:
    row = await session.get(DismissedAuditCluster, dismissed_id)
    if row is not None:
        await session.delete(row)
        await session.commit()
