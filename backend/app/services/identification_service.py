import datetime as _dt
import hashlib
import json
import re
from dataclasses import dataclass

from app.core.config import get_settings
from app.providers.ai.anthropic_client import AIIdentificationError, AnthropicIdentificationClient
from app.providers.epub.parser import EpubEvidence
from app.providers.metadata.types import MetadataCandidate
from app.services.confidence_service import score
from app.services.metadata_sanity import clamp_series_number
from app.services.text_match import normalize, texts_match, titles_match


@dataclass
class IdentificationResult:
    title: str
    author: str | None
    series: str | None
    series_number: float | None
    computed_confidence: int
    ai_reported_confidence: float | None
    needs_human_review: bool
    reasoning_summary: str | None
    model: str
    prompt_hash: str
    evidence_hash: str
    raw_response: dict


class IdentificationService:
    """SPEC.md §5 steps 5-8: skip the full identification AI call when ISBN +
    a metadata provider + the EPUB's own metadata all already agree;
    otherwise ask the AI. Either way, the *computed* confidence (§13) — not
    the AI's self-reported one — drives the needs_human_review routing
    decision, per the hard rule in §1.

    The fast path still makes one lightweight AI call when series info is
    missing from both the EPUB and every provider candidate — title/author
    confidence doesn't need it, but series is worth asking about separately
    since neither deterministic source may know it.
    """

    def __init__(self, ai_client: AnthropicIdentificationClient | None = None) -> None:
        self._ai_client = ai_client or AnthropicIdentificationClient()

    async def identify(
        self,
        *,
        filename: str,
        evidence: EpubEvidence,
        candidates: list[MetadataCandidate],
        corrections: list[dict] | None = None,
    ) -> IdentificationResult:
        evidence_hash = hash_evidence(filename, evidence, candidates)

        fast_match = _find_isbn_match(evidence, candidates)
        if fast_match is not None:
            title = fast_match.title or evidence.title or ""
            author = _first_author(fast_match) or _first_author_evidence(evidence)
            series = evidence.series
            series_number = evidence.series_number
            if series is None:
                candidate_with_series = next((c for c in candidates if c.series), None)
                if candidate_with_series is not None:
                    series = candidate_with_series.series
                    series_number = candidate_with_series.series_number
            reasoning_summary = (
                "Deterministic match: ISBN, a metadata provider, and the EPUB's "
                "own metadata all agree on title and author."
            )
            raw_response: dict = {}

            # ISBN/provider/EPUB agreement is enough to trust title+author
            # without asking the AI. Series is a different question — neither
            # the EPUB nor the provider necessarily know it, so when both are
            # silent, make one lightweight AI call just for that (skipped
            # entirely, no extra cost, when either source already has it).
            if series is None:
                try:
                    series_result, series_raw = await self._ai_client.identify_series(title, author)
                    series = series_result.series
                    series_number = series_result.series_number
                    raw_response["series_lookup"] = series_raw
                    if series:
                        reasoning_summary += (
                            f" Series ({series}) supplied from general bibliographic "
                            "knowledge — not present in the EPUB or provider metadata."
                        )
                except AIIdentificationError as exc:
                    raw_response["series_lookup_error"] = str(exc)

            # Score *after* the series lookup, not before: a fast-path book that
            # only has a series because the model guessed one must take the
            # uncorroborated-series penalty and drop toward the review bar.
            breakdown = score(
                evidence=evidence,
                candidates=candidates,
                filename=filename,
                resolved_series=series,
            )
            raw_response["confidence_breakdown"] = breakdown.as_dict()
            series_number = clamp_series_number(series, series_number, raw_response)

            return IdentificationResult(
                title=title,
                author=author,
                series=series,
                series_number=series_number,
                computed_confidence=breakdown.total,
                ai_reported_confidence=None,
                needs_human_review=breakdown.total < 85,
                reasoning_summary=reasoning_summary,
                model="deterministic",
                prompt_hash=hashlib.sha256(f"deterministic:{evidence_hash}".encode()).hexdigest(),
                evidence_hash=evidence_hash,
                raw_response=raw_response,
            )

        # corrections feed the full identify_book prompt only. The fast path
        # and the identify_series lookup above don't call the model with a
        # free-text prompt, so there's nothing to teach there.
        prompt = _build_prompt(filename, evidence, candidates, corrections)
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()

        ground = should_ground(filename=filename, evidence=evidence, candidates=candidates)
        try:
            ai_result, raw_response = await self._ai_client.identify(prompt, ground=ground)
        except AIIdentificationError as exc:
            breakdown = score(
                evidence=evidence,
                candidates=candidates,
                filename=filename,
                resolved_series=evidence.series,
            )
            fallback_raw: dict = {"error": str(exc), "confidence_breakdown": breakdown.as_dict()}
            series_number = clamp_series_number(
                evidence.series, evidence.series_number, fallback_raw
            )
            return IdentificationResult(
                title=evidence.title or filename,
                author=_first_author_evidence(evidence),
                series=evidence.series,
                series_number=series_number,
                computed_confidence=breakdown.total,
                ai_reported_confidence=None,
                needs_human_review=True,
                reasoning_summary=f"AI identification unavailable: {exc}",
                model="unavailable",
                prompt_hash=prompt_hash,
                evidence_hash=evidence_hash,
                raw_response=fallback_raw,
            )

        ai_corroborates = titles_match(ai_result.title, evidence.title) or any(
            titles_match(ai_result.title, c.title) for c in candidates
        )
        breakdown = score(
            evidence=evidence,
            candidates=candidates,
            filename=filename,
            ai_corroborates=ai_corroborates,
            resolved_series=ai_result.series,
        )

        merged_raw: dict = {**raw_response, "confidence_breakdown": breakdown.as_dict()}
        series_number = clamp_series_number(
            ai_result.series, ai_result.series_number, merged_raw
        )

        return IdentificationResult(
            title=ai_result.title,
            author=ai_result.author,
            series=ai_result.series,
            series_number=series_number,
            computed_confidence=breakdown.total,
            ai_reported_confidence=ai_result.ai_confidence,
            needs_human_review=breakdown.total < 85,
            reasoning_summary=ai_result.reasoning_summary,
            model=self._ai_client.model_name,
            prompt_hash=prompt_hash,
            evidence_hash=evidence_hash,
            raw_response=merged_raw,
        )


_YEAR_RE = re.compile(r"(?:19|20)\d{2}")


def _recent_year_present(filename: str, candidates: list[MetadataCandidate]) -> bool:
    """A publication year in the last two calendar years is a strong hint the
    book may be past the model's training cutoff — exactly when unaided recall
    invents a series. Reads the filename and any provider ``first_published``;
    the EPUB itself carries no parsed date until Stage D."""
    cutoff = _dt.date.today().year - 1
    for match in _YEAR_RE.finditer(filename or ""):
        if int(match.group(0)) >= cutoff:
            return True
    for candidate in candidates:
        published = getattr(candidate, "first_published", None)
        if published:
            match = _YEAR_RE.search(str(published))
            if match and int(match.group(0)) >= cutoff:
                return True
    return False


def _candidates_corroborate(candidates: list[MetadataCandidate]) -> bool:
    """>=2 provider candidates that agree with each other on the title — the
    case where the AI isn't really guessing and web search adds little."""
    titled = [c for c in candidates if c.title]
    if len(titled) < 2:
        return False
    return all(titles_match(titled[0].title, c.title) for c in titled[1:])


def should_ground(
    *,
    filename: str,
    evidence: EpubEvidence,
    candidates: list[MetadataCandidate],
) -> bool:
    """prompts/15 Stage A. Whether the AI-path identify call should be allowed
    to web-search before answering. Grounding is the biggest single lever
    against post-cutoff "invented series" errors, but it costs per search — so
    skip it only for the safe case: a recent-enough date is absent *and* two
    or more providers already corroborate each other *and* there is an ISBN.
    Everything thinner or newer grounds.
    """
    if not get_settings().ai_web_search_enabled:
        return False
    if _recent_year_present(filename, candidates):
        return True
    if not _candidates_corroborate(candidates):
        return True
    return not bool(evidence.isbn13 or evidence.isbn10)


def _first_author(candidate: MetadataCandidate) -> str | None:
    return candidate.authors[0] if candidate.authors else None


def _first_author_evidence(evidence: EpubEvidence) -> str | None:
    return evidence.authors[0] if evidence.authors else None


def _find_isbn_match(
    evidence: EpubEvidence, candidates: list[MetadataCandidate]
) -> MetadataCandidate | None:
    if not (evidence.isbn13 or evidence.isbn10) or not evidence.title:
        return None

    for candidate in candidates:
        isbn_matches = (evidence.isbn13 and candidate.isbn13 == evidence.isbn13) or (
            evidence.isbn10 and candidate.isbn10 == evidence.isbn10
        )
        if not isbn_matches:
            continue
        if not titles_match(candidate.title, evidence.title):
            continue
        if not texts_match(_first_author(candidate), _first_author_evidence(evidence)):
            continue
        return candidate
    return None


_MAX_CORRECTION_EXAMPLES = 5
_MAX_CORRECTION_FIELD_CHARS = 120


def _build_prompt(
    filename: str,
    evidence: EpubEvidence,
    candidates: list[MetadataCandidate],
    corrections: list[dict] | None = None,
) -> str:
    lines = [
        "Identify the book described by this evidence. Evidence comes from an "
        "EPUB file and third-party metadata lookups; sources may disagree.",
        "",
        f"Filename: {filename}",
        f"EPUB title: {evidence.title or '(none)'}",
        f"EPUB author(s): {', '.join(evidence.authors) or '(none)'}",
        f"EPUB language: {evidence.language or '(none)'}",
        f"EPUB ISBN-13: {evidence.isbn13 or '(none)'}",
        f"EPUB ISBN-10: {evidence.isbn10 or '(none)'}",
        f"EPUB series: {evidence.series or '(none)'}",
    ]
    if evidence.text_snippet:
        lines.append(f"First-chapter/copyright-page text: {evidence.text_snippet[:2000]}")

    if candidates:
        lines.append("")
        lines.append("Candidate matches from metadata providers:")
        for candidate in candidates:
            lines.append(
                f"- [{candidate.source}] title={candidate.title!r} "
                f"authors={candidate.authors!r} isbn13={candidate.isbn13!r} "
                f"series={candidate.series!r}"
            )
    else:
        lines.append("")
        lines.append("No metadata provider candidates were found.")

    # Appended last, and only when there's something to show, so a call with
    # no corrections produces the byte-identical prompt (and prompt_hash) as
    # before this section existed.
    if corrections:
        lines.extend(_render_corrections(corrections))

    return "\n".join(lines)


def _render_corrections(corrections: list[dict]) -> list[str]:
    """A short 'here's what a human fixed before' block. Titles and author/
    series names only — never reasoning text or confidence numbers. Capped at
    _MAX_CORRECTION_EXAMPLES rows and each field clipped, to keep the section
    well under ~400 tokens even with long titles."""
    lines = [
        "",
        "Corrections a human has previously made to identifications like this "
        "one. Learn from them — in particular, do NOT invent a series for a "
        "standalone book:",
    ]
    for pair in corrections[:_MAX_CORRECTION_EXAMPLES]:
        said, fixed = pair["proposed"], pair["corrected"]
        lines.append(f"- You said: {_describe_book(said)}")
        lines.append(f"  Corrected to: {_describe_correction(said, fixed)}")
    return lines


def _describe_book(fields: dict) -> str:
    text = f'"{_clip(fields.get("title")) or "(unknown title)"}"'
    if fields.get("author"):
        text += f" by {_clip(fields['author'])}"
    if fields.get("series"):
        text += ", " + _series_phrase(fields["series"], fields.get("series_number"))
    return text


def _describe_correction(said: dict, fixed: dict) -> str:
    """Only the fields the human actually changed. A correction that nulled
    the series is the whole point of this feature — spell it out."""
    changed: list[str] = []
    if normalize(said.get("title")) != normalize(fixed.get("title")):
        changed.append(f'title "{_clip(fixed.get("title")) or "(unknown)"}"')
    if normalize(said.get("author")) != normalize(fixed.get("author")):
        changed.append(f"author {_clip(fixed.get('author')) or '(unknown)'}")
    if normalize(said.get("series")) != normalize(fixed.get("series")) or said.get(
        "series_number"
    ) != fixed.get("series_number"):
        if fixed.get("series"):
            changed.append(_series_phrase(fixed["series"], fixed.get("series_number")))
        else:
            changed.append("standalone, no series")
    return ", ".join(changed) if changed else "(no change)"


def _series_phrase(series: str, number: object) -> str:
    phrase = f'series "{_clip(series)}"'
    if number is not None:
        phrase += f" #{int(number) if isinstance(number, float) and number.is_integer() else number}"
    return phrase


def _clip(text: str | None) -> str:
    if not text:
        return ""
    text = text.strip()
    if len(text) <= _MAX_CORRECTION_FIELD_CHARS:
        return text
    return text[: _MAX_CORRECTION_FIELD_CHARS - 1] + "…"


def hash_evidence(
    filename: str, evidence: EpubEvidence, candidates: list[MetadataCandidate]
) -> str:
    payload = {
        "filename": filename,
        "title": evidence.title,
        "authors": evidence.authors,
        "isbn13": evidence.isbn13,
        "isbn10": evidence.isbn10,
        "series": evidence.series,
        "candidates": [
            {"source": c.source, "title": c.title, "authors": c.authors, "isbn13": c.isbn13}
            for c in candidates
        ],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
