import hashlib
import json
from dataclasses import dataclass

from app.providers.ai.anthropic_client import AIIdentificationError, AnthropicIdentificationClient
from app.providers.epub.parser import EpubEvidence
from app.providers.metadata.types import MetadataCandidate
from app.services.confidence_service import score
from app.services.text_match import texts_match, titles_match


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
        self, *, filename: str, evidence: EpubEvidence, candidates: list[MetadataCandidate]
    ) -> IdentificationResult:
        evidence_hash = hash_evidence(filename, evidence, candidates)

        fast_match = _find_isbn_match(evidence, candidates)
        if fast_match is not None:
            breakdown = score(evidence=evidence, candidates=candidates, filename=filename)
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
            raw_response: dict = {"confidence_breakdown": breakdown.as_dict()}

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

        prompt = _build_prompt(filename, evidence, candidates)
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()

        try:
            ai_result, raw_response = await self._ai_client.identify(prompt)
        except AIIdentificationError as exc:
            breakdown = score(evidence=evidence, candidates=candidates, filename=filename)
            return IdentificationResult(
                title=evidence.title or filename,
                author=_first_author_evidence(evidence),
                series=evidence.series,
                series_number=evidence.series_number,
                computed_confidence=breakdown.total,
                ai_reported_confidence=None,
                needs_human_review=True,
                reasoning_summary=f"AI identification unavailable: {exc}",
                model="unavailable",
                prompt_hash=prompt_hash,
                evidence_hash=evidence_hash,
                raw_response={"error": str(exc), "confidence_breakdown": breakdown.as_dict()},
            )

        ai_corroborates = titles_match(ai_result.title, evidence.title) or any(
            titles_match(ai_result.title, c.title) for c in candidates
        )
        breakdown = score(
            evidence=evidence,
            candidates=candidates,
            filename=filename,
            ai_corroborates=ai_corroborates,
        )

        return IdentificationResult(
            title=ai_result.title,
            author=ai_result.author,
            series=ai_result.series,
            series_number=ai_result.series_number,
            computed_confidence=breakdown.total,
            ai_reported_confidence=ai_result.ai_confidence,
            needs_human_review=breakdown.total < 85,
            reasoning_summary=ai_result.reasoning_summary,
            model=self._ai_client.model_name,
            prompt_hash=prompt_hash,
            evidence_hash=evidence_hash,
            raw_response={**raw_response, "confidence_breakdown": breakdown.as_dict()},
        )


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


def _build_prompt(filename: str, evidence: EpubEvidence, candidates: list[MetadataCandidate]) -> str:
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

    return "\n".join(lines)


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
