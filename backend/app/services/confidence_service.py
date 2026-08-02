from dataclasses import dataclass, field

from app.providers.epub.parser import EpubEvidence
from app.providers.metadata.types import MetadataCandidate
from app.services.text_match import normalize, normalize_title

ISBN_PRESENT = 40
PROVIDER_MATCHES_EPUB = 20
PROVIDERS_AGREE = 15
EPUB_METADATA_COMPLETE = 15
FILENAME_MATCHES_TITLE = 5
AI_CORROBORATES = 5

PROVIDER_DISAGREEMENT_PENALTY = -25
EPUB_PROVIDER_DISAGREEMENT_PENALTY = -15
SERIES_DISAGREEMENT_PENALTY = -10


@dataclass
class ConfidenceBreakdown:
    total: int
    components: dict[str, int] = field(default_factory=dict)
    conflicts: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"total": self.total, "components": self.components, "conflicts": self.conflicts}


def score(
    *,
    evidence: EpubEvidence,
    candidates: list[MetadataCandidate],
    filename: str,
    ai_corroborates: bool = False,
) -> ConfidenceBreakdown:
    """SPEC.md §13's point table, filled in as part of this build — the
    original numbered spec's literal breakdown was never available to this
    project, only that it sums to 100 across six components (40+20+15+15+5+5).
    This is a from-scratch design matching that shape, plus the conflict
    penalties resolved in §1. A pure deterministic match (no AI consulted)
    tops out at 95 — it never earns the ai_corroborates point — which lines
    up neatly with the ≥95 auto-organize threshold requiring either full
    provider agreement including a second corroborating provider, or an AI
    check.
    """
    components: dict[str, int] = {}
    conflicts: dict[str, int] = {}

    has_isbn = bool(evidence.isbn13 or evidence.isbn10)
    components["isbn_present"] = ISBN_PRESENT if has_isbn else 0

    # Titles and series names use normalize_title (drops a leading "the"/
    # "a"/"an") — library catalogs routinely strip leading articles while an
    # EPUB's own metadata usually doesn't, and that's not a real
    # disagreement. Authors use plain normalize(); stripping articles from
    # a name isn't meaningful and could mangle initials.
    candidate_titles = {normalize_title(c.title) for c in candidates if c.title}
    candidate_authors = {normalize(c.authors[0]) for c in candidates if c.authors}

    epub_provider_conflict = (
        bool(evidence.title)
        and bool(candidate_titles)
        and normalize_title(evidence.title) not in candidate_titles
    )
    provider_matches_epub = bool(candidates) and bool(evidence.title) and not epub_provider_conflict
    components["provider_matches_epub"] = PROVIDER_MATCHES_EPUB if provider_matches_epub else 0

    provider_conflict = len(candidate_titles) > 1 or len(candidate_authors) > 1
    providers_agree = len(candidates) >= 2 and not provider_conflict
    components["providers_agree"] = PROVIDERS_AGREE if providers_agree else 0

    epub_complete = bool(evidence.title) and bool(evidence.authors) and bool(evidence.language)
    components["epub_metadata_complete"] = EPUB_METADATA_COMPLETE if epub_complete else 0

    filename_matches = bool(evidence.title) and normalize_title(evidence.title) in normalize(filename)
    components["filename_matches_title"] = FILENAME_MATCHES_TITLE if filename_matches else 0

    components["ai_corroborates"] = AI_CORROBORATES if ai_corroborates else 0

    if provider_conflict:
        conflicts["provider_disagreement"] = PROVIDER_DISAGREEMENT_PENALTY
    if epub_provider_conflict:
        conflicts["epub_provider_disagreement"] = EPUB_PROVIDER_DISAGREEMENT_PENALTY

    candidate_series = {normalize_title(c.series) for c in candidates if c.series}
    if (
        evidence.series
        and candidate_series
        and normalize_title(evidence.series) not in candidate_series
    ):
        conflicts["series_disagreement"] = SERIES_DISAGREEMENT_PENALTY

    total = max(0, min(100, sum(components.values()) + sum(conflicts.values())))
    return ConfidenceBreakdown(total=total, components=components, conflicts=conflicts)
