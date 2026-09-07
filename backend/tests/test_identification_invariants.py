"""Structural invariants for first-pass identification (prompts/15 Stage 0).

These need no ground truth — they are properties every identification must have
regardless of which book it is. A violation is a bug. Run with `pytest -m corpus`.

They run over the real corpus predictions *and* a few synthetic evidence
shapes, so they keep working as the corpus grows and catch the "confidently
wrong" failures the accuracy push exists to prevent.
"""

from __future__ import annotations

import pytest

from app.providers.epub.parser import EpubEvidence
from app.providers.metadata.types import MetadataCandidate
from app.services.identification_service import IdentificationService
from app.services.metadata_sanity import MAX_SERIES_NUMBER
from app.services.text_match import normalize_words
from tests.corpus_harness import FrozenAIClient, load_corpus, run_entry

pytestmark = pytest.mark.corpus

_AUTO_ORGANIZE = 85  # settings.confidence_auto_flagged


async def _predict_all():
    return [(e, await run_entry(e)) for e in load_corpus()]


# --------------------------------------------------------------------------
# Invariants over the real corpus
# --------------------------------------------------------------------------


async def test_standalone_never_carries_a_series_number():
    bad = []
    for entry, pred in await _predict_all():
        if pred.skipped_offline:
            continue
        if pred.series in (None, "") and pred.series_number is not None:
            bad.append(f"{entry.id}: series=None but series_number={pred.series_number}")
    assert not bad, "\n".join(bad)


async def test_series_number_is_always_plausible():
    bad = []
    for entry, pred in await _predict_all():
        if pred.skipped_offline or pred.series_number is None:
            continue
        n = pred.series_number
        if n <= 0 or n > MAX_SERIES_NUMBER:
            bad.append(f"{entry.id}: implausible series_number {n}")
    assert not bad, "\n".join(bad)


async def test_resolved_title_is_grounded_in_some_input():
    """The pipeline must not emit a title that appears in *no* input — not the
    EPUB, not a candidate, not the filename, not the recorded AI answer. A
    confident, wholly-invented title is the worst failure mode."""
    bad = []
    for entry, pred in await _predict_all():
        if pred.skipped_offline or not pred.title:
            continue
        haystacks = [
            entry.evidence.title or "",
            entry.filename,
            *(c.title or "" for c in entry.candidates),
        ]
        if entry.recorded_ai:
            haystacks.append(entry.recorded_ai.get("title") or "")
        pool = normalize_words(" ".join(haystacks))
        want = normalize_words(pred.title)
        if not want:
            continue
        overlap = len(want & pool) / len(want)
        if overlap < 0.5:
            bad.append(f"{entry.id}: title {pred.title!r} not grounded (overlap {overlap:.0%})")
    assert not bad, "\n".join(bad)


async def test_identification_is_deterministic():
    """Same evidence in ⇒ same identification out. Guards against nondeterminism
    (dict ordering, set iteration) creeping into resolve/score."""
    bad = []
    for entry in load_corpus():
        a = await run_entry(entry)
        b = await run_entry(entry)
        if (a.title, a.author, a.series, a.series_number) != (b.title, b.author, b.series, b.series_number):
            bad.append(entry.id)
    assert not bad, f"non-deterministic: {bad}"


async def test_no_confident_identification_from_pure_junk():
    """Placeholder title + no author + no ISBN + no candidates + no AI answer
    must never clear the auto-organize bar."""
    svc = IdentificationService(ai_client=_StubAI())
    for junk_title in ("input", "Unknown", "Calibre", "book1", "Untitled"):
        ev = EpubEvidence(title=junk_title, authors=[], language=None)
        result = await svc.identify(filename="x.epub", evidence=ev, candidates=[])
        assert result.computed_confidence < _AUTO_ORGANIZE, (
            f"{junk_title!r} reached confidence {result.computed_confidence}"
        )


# --------------------------------------------------------------------------
# Synthetic safety properties
# --------------------------------------------------------------------------


class _StubAI:
    model_name = "stub"

    async def identify(self, prompt: str, *, ground: bool = False):
        from app.providers.ai.types import AIIdentificationResult

        return (
            AIIdentificationResult(
                title="Some Book", author="Some Author", series=None, series_number=None,
                ai_confidence=50, reasoning_summary="stub", needs_human_review=True,
            ),
            {"stop_reason": "tool_use"},
        )

    async def identify_series(self, title, author):
        from app.providers.ai.types import AISeriesResult

        return AISeriesResult(series=None, series_number=None), {}


async def test_isbn_fast_path_requires_title_agreement():
    """A wrong-but-valid ISBN whose provider record is a *different* book must
    not produce a deterministic identification."""
    svc = IdentificationService(ai_client=_StubAI())
    ev = EpubEvidence(title="The Final Empire", authors=["Brandon Sanderson"], isbn13="9780765311788")
    wrong = MetadataCandidate(
        title="Neuromancer", authors=["William Gibson"], isbn13="9780765311788", source="x"
    )
    result = await svc.identify(filename="x.epub", evidence=ev, candidates=[wrong])
    assert result.model != "deterministic", "fast-pathed on an ISBN pointing at a different title"


async def test_stray_series_index_without_a_series_is_dropped():
    svc = IdentificationService(ai_client=_StubAI())
    ev = EpubEvidence(
        title="A Standalone Novel", authors=["A Writer"], isbn13=None,
        series=None, series_number=7.0,  # calibre leftover, no series name
    )
    result = await svc.identify(filename="x.epub", evidence=ev, candidates=[])
    assert result.series is None
    assert result.series_number is None
