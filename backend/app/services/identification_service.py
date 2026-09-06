import datetime as _dt
import hashlib
import json
import re
from dataclasses import dataclass

from app.core.config import get_settings
from app.providers.ai.anthropic_client import AIIdentificationError, AnthropicIdentificationClient
from app.providers.epub.parser import EpubEvidence
from app.providers.filename.parser import FilenameGuess, parse_book_filename
from app.providers.metadata.types import MetadataCandidate
from app.services.confidence_service import score
from app.services.metadata_sanity import (
    clamp_series_number,
    looks_like_placeholder_author,
    looks_like_placeholder_title,
)
from app.services.text_match import (
    normalize,
    normalize_words,
    texts_match,
    title_similarity,
    titles_match,
)

# prompts/15 Stage F. Below this character-level agreement between the EPUB
# title and an ISBN-matched provider title, the ISBN is not trusted for a
# deterministic result — the AI path decides instead.
_ISBN_TITLE_SIMILARITY_MIN = 0.80


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
        filename_guess = parse_book_filename(filename)

        # prompts/15 Stage E: a placeholder title/author in the EPUB must never
        # ride the deterministic fast path, even on an ISBN match — force the AI
        # path so the real title/author can be recovered (the candidate is still
        # passed to it).
        fast_match = _find_isbn_match(evidence, candidates)
        if fast_match is not None and _has_placeholder_epub_metadata(evidence):
            fast_match = None
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
                filename_corroborates=_filename_corroborates(filename_guess, title, author),
                resolved_title=title,
                resolved_author=author,
            )
            raw_response["confidence_breakdown"] = breakdown.as_dict()
            if filename_guess.usable:
                raw_response["filename_guess"] = filename_guess.as_prompt_line()
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
        prompt = _build_prompt(filename, evidence, candidates, corrections, filename_guess)
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
                filename_corroborates=_filename_corroborates(
                    filename_guess, evidence.title, _first_author_evidence(evidence)
                ),
                resolved_title=evidence.title or filename,
                resolved_author=_first_author_evidence(evidence),
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
            filename_corroborates=_filename_corroborates(
                filename_guess, ai_result.title, ai_result.author
            ),
            resolved_title=ai_result.title,
            resolved_author=ai_result.author,
        )

        merged_raw: dict = {**raw_response, "confidence_breakdown": breakdown.as_dict()}
        if filename_guess.usable:
            merged_raw["filename_guess"] = filename_guess.as_prompt_line()
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


def should_ground(
    *,
    filename: str,
    evidence: EpubEvidence,
    candidates: list[MetadataCandidate],
) -> bool:
    """prompts/15 Stage A. Whether the AI-path identify call may web-search
    before answering.

    Web search is billed per call, so it is reserved for the *one* case where
    the model's own bibliographic memory is genuinely unreliable: a book new
    enough to be at or past its training cutoff. That is exactly the documented
    failure — "Scion" (a 2026 standalone) auto-filed as an invented series #2.
    For anything older the model already knows the answer and a search is money
    spent for nothing, so thin/conflicting provider evidence on its own does
    *not* trigger grounding — it just means a normal (unGROUNDED) AI call and,
    if the score is low, the review queue.
    """
    if not get_settings().ai_web_search_enabled:
        return False
    return _recent_year_present(filename, candidates)


def _has_placeholder_epub_metadata(evidence: EpubEvidence) -> bool:
    # An ISBN in the EPUB is enough corroboration to trust a genuinely short
    # title ("It", "V.") — the fast path only runs on an ISBN match anyway.
    return looks_like_placeholder_title(
        evidence.title, corroborated=bool(evidence.isbn13 or evidence.isbn10)
    ) or looks_like_placeholder_author(_first_author_evidence(evidence))


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
        # prompts/15 Stage F — an ISBN in an EPUB is often wrong (a print ISBN
        # on an ebook, an OCR'd digit, the wrong edition). titles_match alone
        # is too weak a check to then trust it deterministically: it strips
        # everything after a ':' so "Mistborn: The Final Empire" and "Mistborn:
        # The Well of Ascension" pass it. Require real character-level title
        # agreement as well, else fall through to the AI path (the candidate is
        # still passed along).
        if not titles_match(candidate.title, evidence.title):
            continue
        if title_similarity(candidate.title, evidence.title) < _ISBN_TITLE_SIMILARITY_MIN:
            continue
        if not texts_match(_first_author(candidate), _first_author_evidence(evidence)):
            continue
        return candidate
    return None


def _filename_corroborates(
    guess: FilenameGuess, resolved_title: str | None, resolved_author: str | None
) -> bool:
    """prompts/15 Stage C. The structured filename parse agrees with the
    identification's resolved title (and author, if the filename carried one).
    Replaces the old weak "resolved-or-EPUB title is a substring of the
    filename" test — `"It"` was a substring of almost any filename."""
    if not guess.usable or not guess.title or not resolved_title:
        return False
    if not titles_match(guess.title, resolved_title):
        return False
    if guess.author and resolved_author:
        gw, rw = normalize_words(guess.author), normalize_words(resolved_author)
        if gw and rw and not (gw <= rw or rw <= gw):
            return False
    return True


_MAX_CORRECTION_EXAMPLES = 5
_MAX_CORRECTION_FIELD_CHARS = 120


def _build_prompt(
    filename: str,
    evidence: EpubEvidence,
    candidates: list[MetadataCandidate],
    corrections: list[dict] | None = None,
    filename_guess: "FilenameGuess | None" = None,
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
    if len(evidence.all_isbns) > 1:
        lines.append(f"All ISBNs found in the EPUB: {', '.join(evidence.all_isbns)}")
    if evidence.description:
        lines.append(f"EPUB description: {evidence.description[:600]}")
    if evidence.publisher:
        lines.append(f"EPUB publisher: {evidence.publisher}")
    if evidence.pub_date:
        lines.append(f"EPUB publication date: {evidence.pub_date}")
    if evidence.subjects:
        lines.append(f"EPUB subjects/genre: {', '.join(evidence.subjects[:12])}")
    if evidence.text_snippet:
        lines.append(f"EPUB text (front matter + a body sample): {evidence.text_snippet[:2600]}")

    if candidates:
        lines.append("")
        lines.append("Candidate matches from metadata providers:")
        for candidate in candidates:
            series_note = candidate.series
            if series_note and candidate.series_number is not None:
                series_note = f"{candidate.series} #{_fmt_number(candidate.series_number)}"
            lines.append(
                f"- [{candidate.source}] title={candidate.title!r} "
                f"authors={candidate.authors!r} isbn13={candidate.isbn13!r} "
                f"series={series_note!r} genre={candidate.genre!r} "
                f"published={candidate.first_published!r}"
            )
    else:
        lines.append("")
        lines.append("No metadata provider candidates were found.")

    if filename_guess is not None and (filename_guess.title or filename_guess.series):
        lines.append("")
        lines.append(
            "Structured parse of the filename (a heuristic — trust it only as far "
            f"as its confidence): {filename_guess.as_prompt_line()}"
        )

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


def _fmt_number(number: float) -> str:
    return str(int(number) if isinstance(number, float) and number.is_integer() else number)


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
