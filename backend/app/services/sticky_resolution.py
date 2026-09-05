from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import File, LibraryRule, Review, ReviewStatus, RuleType
from app.providers.epub.parser import EpubEvidence
from app.services.book_repository import resolve_book
from app.services.identification_service import IdentificationResult, hash_evidence
from app.services.text_match import normalize

# NOTE: a sha256-keyed correction lookup (find a corrected identification by
# content hash alone) is deliberately NOT exposed as a standalone step in
# the main identification pipeline. By construction, a correction can only
# exist for a sha256 that's already attached to some File — so any file
# whose content matches one is always caught by scan_service's duplicate
# check first. The one thing that check needs from a correction is the
# book id, which is what resolve_corrected_book_id below provides.


async def _latest_correction(session: AsyncSession, sha256: str) -> Review | None:
    # Match a file's current hash OR the hash it had before BookBrain
    # rewrote its embedded metadata — a correction made against the
    # pristine original must still stick after a writeback changes the bytes.
    result = await session.execute(
        select(Review)
        .join(File, Review.file_id == File.id)
        .where(
            Review.status == ReviewStatus.corrected,
            or_(File.sha256 == sha256, File.original_sha256 == sha256),
        )
        .order_by(Review.resolved_at.desc())
    )
    review = result.scalars().first()
    return review if review is not None and review.correction_json else None


async def resolve_corrected_book_id(session: AsyncSession, sha256: str) -> int | None:
    """Used by duplicate detection (SPEC.md §6): a new file whose content
    exactly matches an existing one normally just copies the primary's
    book_id, but if that content has since been corrected by a human, the
    correction is authoritative — inherit that instead, not a stale or
    never-reviewed guess."""
    review = await _latest_correction(session, sha256)
    if review is None:
        return None

    data = review.correction_json
    book = await resolve_book(
        session,
        title=data["title"],
        author=data.get("author"),
        series=data.get("series"),
        series_number=data.get("series_number"),
        isbn13=None,
        isbn10=None,
    )
    return book.id


async def find_rule_match(
    session: AsyncSession, filename: str, evidence: EpubEvidence
) -> IdentificationResult | None:
    """SPEC.md §2: a matching library_rules entry short-circuits the
    pipeline exactly like a sticky correction — consulted before candidate
    generation and before the AI call."""
    if not evidence.authors and not evidence.series:
        return None

    result = await session.execute(
        select(LibraryRule).where(
            LibraryRule.rule_type.in_([RuleType.author_alias, RuleType.series_alias])
        )
    )
    rules = result.scalars().all()

    author = evidence.authors[0] if evidence.authors else None
    matched_author = author
    matched_series = evidence.series
    matched = False

    for rule in rules:
        if rule.rule_type == RuleType.author_alias and author and normalize(rule.pattern) == normalize(author):
            matched_author = rule.resolution_json.get("author", author)
            matched = True
        elif (
            rule.rule_type == RuleType.series_alias
            and evidence.series
            and normalize(rule.pattern) == normalize(evidence.series)
        ):
            matched_series = rule.resolution_json.get("series", evidence.series)
            matched = True

    if not matched:
        return None

    return IdentificationResult(
        title=evidence.title or filename,
        author=matched_author,
        series=matched_series,
        series_number=evidence.series_number,
        computed_confidence=100,
        ai_reported_confidence=None,
        needs_human_review=False,
        reasoning_summary="Matched a saved library rule (author/series alias).",
        model="library_rule",
        prompt_hash="",
        evidence_hash=hash_evidence(filename, evidence, []),
        raw_response={},
    )
