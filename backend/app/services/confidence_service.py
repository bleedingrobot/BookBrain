from dataclasses import dataclass, field

from app.providers.epub.parser import EpubEvidence
from app.providers.metadata.types import MetadataCandidate
from app.services.metadata_sanity import (
    looks_like_placeholder_author,
    looks_like_placeholder_title,
)
from app.services.text_match import normalize, normalize_title, normalize_words

ISBN_PRESENT = 40
PROVIDER_MATCHES_EPUB = 20
PROVIDERS_AGREE = 15
EPUB_METADATA_COMPLETE = 15
FILENAME_MATCHES_TITLE = 5
AI_CORROBORATES = 5

PROVIDER_DISAGREEMENT_PENALTY = -25
EPUB_PROVIDER_DISAGREEMENT_PENALTY = -15
SERIES_DISAGREEMENT_PENALTY = -10
# Distinct from SERIES_DISAGREEMENT_PENALTY: that one needs a *conflicting*
# candidate series. This one fires on *silence* — the resolved book has a
# series that neither the EPUB nor any provider candidate mentions at all,
# i.e. the AI (or the fast-path series lookup) invented it. Structural gap #1
# from the 2026-09-06 review: a clean ISBN+provider match (~90) minus this
# lands an invented-series book in the review queue instead of the library.
UNCORROBORATED_SERIES_PENALTY = -15
# prompts/15 Stage E. The *resolved* title or author still looks like a stub
# ("Unknown", "Calibre", "book1", a publisher's name) — identification never
# actually landed on a real book. Large, because such a row must not
# auto-organise regardless of what else added up (a complete-looking EPUB with
# an ISBN can otherwise reach 55+ before the model is even consulted).
PLACEHOLDER_METADATA_PENALTY = -30
# The resolved title is just the filename stem and nothing (provider or AI)
# independently says so — we effectively failed to identify it and kept the
# filename as a guess.
TITLE_IS_FILENAME_ONLY_PENALTY = -10


@dataclass
class ConfidenceBreakdown:
    total: int
    components: dict[str, int] = field(default_factory=dict)
    conflicts: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"total": self.total, "components": self.components, "conflicts": self.conflicts}


def _filename_stem(filename: str) -> str:
    stem = filename.rsplit("/", 1)[-1]
    return stem.rsplit(".", 1)[0] if "." in stem else stem


def _series_in_a_source(
    series: str, evidence: EpubEvidence, candidates: list[MetadataCandidate]
) -> bool:
    """Same order-independent word-set match as
    ``reident_audit_service._series_corroborated`` — is this series backed by
    the EPUB's own metadata or any provider candidate?"""
    target = normalize_words(series)
    if not target:
        return True
    if evidence.series and normalize_words(evidence.series) == target:
        return True
    return any(c.series and normalize_words(c.series) == target for c in candidates)


def score(
    *,
    evidence: EpubEvidence,
    candidates: list[MetadataCandidate],
    filename: str,
    ai_corroborates: bool = False,
    resolved_series: str | None = None,
    filename_corroborates: bool | None = None,
    resolved_title: str | None = None,
    resolved_author: str | None = None,
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

    # prompts/15 Stage C: prefer the structured filename parse agreeing with the
    # *resolved* title+author (passed by identification_service). The old
    # substring test — resolved/EPUB title appears anywhere in the filename —
    # stays as the fallback for callers that don't parse the filename
    # (reident_audit_service._recompute_confidence); "It" was a substring of
    # almost every filename.
    if filename_corroborates is None:
        filename_matches = bool(evidence.title) and normalize_title(evidence.title) in normalize(
            filename
        )
    else:
        filename_matches = filename_corroborates
    components["filename_matches_title"] = FILENAME_MATCHES_TITLE if filename_matches else 0

    components["ai_corroborates"] = AI_CORROBORATES if ai_corroborates else 0

    if provider_conflict:
        conflicts["provider_disagreement"] = PROVIDER_DISAGREEMENT_PENALTY
    if epub_provider_conflict:
        conflicts["epub_provider_disagreement"] = EPUB_PROVIDER_DISAGREEMENT_PENALTY

    # prompts/15 Stage B: real provider series now flow in, and provider series
    # strings are messy and sometimes plain wrong. A single provider disagreeing
    # with the EPUB's own series is too weak to penalise — only fire when the
    # EPUB series matches no candidate AND at least two candidates agree on a
    # different series (a genuine provider consensus pointing elsewhere).
    epub_series_key = normalize_title(evidence.series) if evidence.series else ""
    candidate_series = {normalize_title(c.series) for c in candidates if c.series}
    disagreeing = [
        c for c in candidates if c.series and normalize_title(c.series) != epub_series_key
    ]
    if (
        epub_series_key
        and epub_series_key not in candidate_series
        and len(disagreeing) >= 2
        and len({normalize_title(c.series) for c in disagreeing}) == 1
    ):
        conflicts["series_disagreement"] = SERIES_DISAGREEMENT_PENALTY

    # An AI-supplied series that no source mentions. Callers opt in by passing
    # the resolved series; the default keeps historical/recompute callers
    # (reident_audit_service._recompute_confidence, existing tests) unchanged.
    if resolved_series and not _series_in_a_source(resolved_series, evidence, candidates):
        conflicts["uncorroborated_series"] = UNCORROBORATED_SERIES_PENALTY

    # prompts/15 Stage E — placeholder / filename-stub resolved metadata.
    # Opt-in via resolved_title/resolved_author; reident + old callers unchanged.
    corroborated = has_isbn or provider_matches_epub or ai_corroborates
    if resolved_title is not None or resolved_author is not None:
        if looks_like_placeholder_title(
            resolved_title, corroborated=corroborated
        ) or looks_like_placeholder_author(resolved_author):
            conflicts["placeholder_metadata"] = PLACEHOLDER_METADATA_PENALTY
        elif (
            resolved_title
            and normalize_title(resolved_title) == normalize_title(_filename_stem(filename))
            and not ai_corroborates
            and normalize_title(resolved_title) not in candidate_titles
        ):
            conflicts["title_is_filename_only"] = TITLE_IS_FILENAME_ONLY_PENALTY

    total = max(0, min(100, sum(components.values()) + sum(conflicts.values())))
    return ConfidenceBreakdown(total=total, components=components, conflicts=conflicts)
