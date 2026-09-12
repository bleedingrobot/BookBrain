"""Adversarial mutation tests (prompts/15 Stage 0).

Corrupt one piece of evidence and assert the pipeline degrades safely: it
either still reaches the right answer, or it stops being confident — it must
never turn a corrupted input into a confident wrong identification that would
auto-organize.

Offline these exercise the *deterministic* layer (ISBN fast path, confidence
scoring, the series-number clamp, resolve_book) — the recorded AI answer can't
be re-run against mutated evidence. That's the layer that decides whether a
book auto-files without a human, so it's the one that must be mutation-safe.
"""

from __future__ import annotations

import pytest

from app.providers.ai.types import AIIdentificationResult, AISeriesResult
from app.providers.epub.parser import EpubEvidence
from app.providers.metadata.types import MetadataCandidate
from app.services.identification_service import IdentificationService

pytestmark = pytest.mark.corpus

_AUTO_ORGANIZE = 85


class _EchoAI:
    """A naive model: repeats whatever the EPUB says. The dangerous case —
    if the deterministic layer also trusts corrupted evidence, a wrong answer
    sails through at high confidence."""

    model_name = "echo"

    def __init__(self, evidence: EpubEvidence) -> None:
        self._ev = evidence

    async def identify(self, prompt: str, *, ground: bool = False):
        return (
            AIIdentificationResult(
                title=self._ev.title or "Unknown",
                author=(self._ev.authors or ["Unknown"])[0],
                series=self._ev.series,
                series_number=self._ev.series_number,
                ai_confidence=95,
                reasoning_summary="echoed the EPUB",
                needs_human_review=False,
            ),
            {"stop_reason": "tool_use"},
        )

    async def identify_series(self, title, author):
        return AISeriesResult(series=None, series_number=None), {}


async def _run(evidence: EpubEvidence, candidates: list[MetadataCandidate], filename="x.epub"):
    svc = IdentificationService(ai_client=_EchoAI(evidence))
    return await svc.identify(filename=filename, evidence=evidence, candidates=candidates)


async def test_wrong_isbn_pointing_at_another_book_does_not_fast_path():
    # The EPUB carries a valid ISBN, but that ISBN's provider record is a
    # different book. Must not produce a deterministic (fast-path) result.
    ev = EpubEvidence(title="The Left Hand of Darkness", authors=["Ursula K. Le Guin"],
                      isbn13="9780547572475", language="en")
    wrong = MetadataCandidate(title="A Wizard of Earthsea", authors=["Ursula K. Le Guin"],
                              isbn13="9780547572475", source="google_books")
    result = await _run(ev, [wrong])
    assert result.model != "deterministic"
    assert result.computed_confidence < 95


async def test_appended_volume_marker_does_not_fabricate_a_series_number():
    # A tracker/Calibre artefact in the title. With no series name anywhere,
    # the number must not survive.
    ev = EpubEvidence(title="Stand-Alone Story (Book 7)", authors=["A. Writer"],
                      series=None, series_number=None, language="en")
    result = await _run(ev, [])
    assert result.series is None
    assert result.series_number is None


async def test_junk_series_index_is_clamped_even_on_the_fast_path():
    ev = EpubEvidence(title="City of Torment", authors=["Bruce R. Cordell"],
                      isbn13="9780786956142", series="Abolethic Sovereignty", series_number=301.0,
                      language="en")
    cand = MetadataCandidate(title="City of Torment", authors=["Bruce R. Cordell"],
                             isbn13="9780786956142", source="open_library")
    result = await _run(ev, [cand])
    assert result.series == "Abolethic Sovereignty"
    assert result.series_number is None  # 301 is a scrape artefact


async def test_placeholder_author_does_not_reach_auto_organize_alone():
    ev = EpubEvidence(title="Some Real Title", authors=["Unknown"], isbn13=None, language="en")
    result = await _run(ev, [])
    assert result.computed_confidence < _AUTO_ORGANIZE


async def test_corpus_entries_survive_a_title_noise_mutation():
    """Every corpus book that auto-organizes today must still be safe when its
    EPUB title gains a junk parenthetical: same identification, or it drops
    below the auto bar."""
    from tests.corpus_harness import FrozenAIClient, field_matches, load_corpus, run_entry

    violations = []
    for entry in load_corpus():
        base = await run_entry(entry)
        if base.skipped_offline or base.computed_confidence < _AUTO_ORGANIZE:
            continue

        noisy = EpubEvidence(
            title=(entry.evidence.title or "Untitled") + " (Uncorrected Proof)",
            authors=list(entry.evidence.authors),
            language=entry.evidence.language,
            description=entry.evidence.description,
            isbn10=entry.evidence.isbn10,
            isbn13=entry.evidence.isbn13,
            series=entry.evidence.series,
            series_number=entry.evidence.series_number,
            text_snippet=entry.evidence.text_snippet,
        )
        svc = IdentificationService(ai_client=FrozenAIClient(entry))
        try:
            mutated = await svc.identify(
                filename=entry.filename, evidence=noisy, candidates=entry.candidates, corrections=[]
            )
        except Exception:  # noqa: BLE001 - a no-recording fixture, skip
            continue

        still_ok = all(
            field_matches(f, getattr(mutated, f), getattr(base, f))
            for f in ("title", "author", "series", "series_number")
        )
        if not still_ok and mutated.computed_confidence >= _AUTO_ORGANIZE:
            violations.append(
                f"{entry.id}: title noise changed the id and it still auto-organizes "
                f"({base.title!r}->{mutated.title!r}, conf {mutated.computed_confidence})"
            )
    assert not violations, "\n".join(violations)
