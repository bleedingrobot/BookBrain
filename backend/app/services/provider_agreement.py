"""Per-field agreement across raw metadata candidates, surfaced as extra
context — for the AI prompt and the review-queue UI — rather than folded into
confidence_service's scoring. confidence_service already checks title/author
for a conflict; this reports the positive case (which values ≥2 candidates
actually agree on, and how many) at a per-field granularity it doesn't cover
(series, genre), without changing the score or the auto-organize threshold."""

from collections import Counter
from dataclasses import dataclass

from app.providers.metadata.types import MetadataCandidate
from app.services.text_match import normalize, normalize_title, normalize_words

_AGREEMENT_FIELDS = ("title", "authors", "series", "genre")


@dataclass(frozen=True)
class AgreementInfo:
    value: str
    provider_count: int
    sources: tuple[str, ...]


def _norm_genre(value: str) -> str:
    return " ".join(value.split()).strip().casefold()


def _normalized_value(field_name: str, candidate: MetadataCandidate) -> str | None:
    if field_name == "title":
        key = normalize_title(candidate.title)
    elif field_name == "authors":
        key = normalize(candidate.authors[0]) if candidate.authors else ""
    elif field_name == "series":
        words = normalize_words(candidate.series)
        key = " ".join(sorted(words)) if words else ""
    elif field_name == "genre":
        key = _norm_genre(candidate.genre) if candidate.genre else ""
    else:  # pragma: no cover - not reachable, _AGREEMENT_FIELDS is fixed
        return None
    return key or None


def _display_value(field_name: str, candidate: MetadataCandidate) -> str | None:
    if field_name == "authors":
        return candidate.authors[0] if candidate.authors else None
    return getattr(candidate, field_name)


def compute_agreement(candidates: list[MetadataCandidate]) -> dict[str, AgreementInfo]:
    """For each of title/authors/series/genre, report the value ≥2 candidates
    agree on after normalizing (first-seen provider order breaks ties among
    equally-supported values). Fields with no such agreement are omitted."""
    agreement: dict[str, AgreementInfo] = {}
    for field_name in _AGREEMENT_FIELDS:
        counts: Counter[str] = Counter()
        first_seen: dict[str, tuple[str, list[str]]] = {}
        for candidate in candidates:
            key = _normalized_value(field_name, candidate)
            if key is None:
                continue
            counts[key] += 1
            display = first_seen.setdefault(key, (_display_value(field_name, candidate) or "", []))
            display[1].append(candidate.source)

        if not counts:
            continue
        top_key, top_count = counts.most_common(1)[0]
        if top_count < 2:
            continue
        value, sources = first_seen[top_key]
        agreement[field_name] = AgreementInfo(
            value=value, provider_count=top_count, sources=tuple(sources)
        )
    return agreement
