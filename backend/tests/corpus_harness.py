"""Ground-truth eval harness for first-pass book identification.

prompts/15 Stage 0. "Close to 100%" is meaningless without a number — this is
the number. A corpus of real, hand-verified books lives in
``tests/identification_corpus/*.json``; this module replays each one through
``IdentificationService.identify`` (providers + AI mocked from the recording)
plus the real ``resolve_book`` normalisation, and scores per-field precision
against the known-correct answer.

Two entry points share :func:`score_corpus`:

* ``tests/test_identification_corpus.py`` — the ``-m corpus`` gate. Fails if any
  field regresses below the baseline recorded in ``IDENTIFICATION-EVAL.md``.
* ``scripts/eval_identification.py`` — prints the score table + a confusion
  list; ``--live`` swaps the recordings for real providers + a real AI call.

Offline fidelity notes
----------------------
* The recorded ``identify_book`` response is frozen. Stages that change the
  *prompt* or add *web grounding* (A, H) can only be measured with
  ``eval_identification.py --live`` on the recent-books slice — the offline
  number holds the model's answer fixed and measures everything deterministic
  around it (candidates, confidence, clamps, resolve_book).
* A fixture with ``recorded_ai: null`` has no replayable model answer; offline
  it is scored only if identification never reaches the AI path (fast path /
  rule / parse-failure), otherwise it is reported as ``skipped_offline``.
* ``resolve_book`` against an empty DB is an identity op on the strings — it is
  still run here so Stage J (author/series canonicalisation) can extend the
  harness to pre-seed canonical rows without changing this call site.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.data.db import Base
from app.data import models  # noqa: F401  (registers models on Base.metadata)
from app.providers.ai.anthropic_client import AIIdentificationError
from app.providers.ai.types import AIIdentificationResult, AISeriesResult
from app.providers.epub.parser import EpubEvidence
from app.providers.metadata.types import MetadataCandidate
from app.services.book_repository import resolve_book
from app.services.identification_service import IdentificationService
from app.services.text_match import normalize_title_strict, normalize_words

CORPUS_DIR = Path(__file__).parent / "identification_corpus"
FIELDS = ("title", "author", "series", "series_number")


# --------------------------------------------------------------------------
# Fixture model
# --------------------------------------------------------------------------


@dataclass
class AnswerKey:
    title: str | None = None
    author: str | None = None
    series: str | None = None
    series_number: float | None = None
    verified: bool = False  # triangulated or hand-verified (vs auto-filled)
    source: str = ""        # "triangulated" | "human_correction" | "stored_identification" | "manual"
    # per-field: "consensus" | "weak" | "unresolved" (from scripts/build_truth.py).
    # Absent ⇒ treat every field as "consensus" (pre-triangulation fixtures).
    provenance: dict = field(default_factory=dict)

    def scorable(self, fieldname: str, *, include_weak: bool = True) -> bool:
        p = self.provenance.get(fieldname, "consensus")
        return p == "consensus" or (include_weak and p == "weak")


@dataclass
class CorpusEntry:
    id: str
    case_tags: list[str]
    notes: str
    source: dict[str, Any]
    filename: str
    evidence: EpubEvidence
    candidates: list[MetadataCandidate]
    recorded_ai: dict | None
    recorded_series_ai: dict | None
    candidate_fidelity: str  # "fresh" | "db-reconstructed"
    answer: AnswerKey

    @classmethod
    def from_json(cls, path: Path) -> "CorpusEntry":
        raw = json.loads(path.read_text(encoding="utf-8"))
        ev = raw["evidence"]
        return cls(
            id=raw["id"],
            case_tags=list(raw.get("case_tags", [])),
            notes=raw.get("notes", ""),
            source=raw.get("source", {}),
            filename=raw["filename"],
            evidence=EpubEvidence(
                title=ev.get("title"),
                authors=list(ev.get("authors", [])),
                language=ev.get("language"),
                description=ev.get("description"),
                isbn10=ev.get("isbn10"),
                isbn13=ev.get("isbn13"),
                series=ev.get("series"),
                series_number=ev.get("series_number"),
                text_snippet=ev.get("text_snippet", ""),
            ),
            candidates=[
                MetadataCandidate(
                    title=c.get("title"),
                    authors=list(c.get("authors", [])),
                    series=c.get("series"),
                    series_number=c.get("series_number"),
                    description=c.get("description"),
                    language=c.get("language"),
                    first_published=c.get("first_published"),
                    isbn13=c.get("isbn13"),
                    isbn10=c.get("isbn10"),
                    source=c.get("source", ""),
                )
                for c in raw.get("candidates", [])
            ],
            recorded_ai=raw.get("recorded_ai"),
            recorded_series_ai=raw.get("recorded_series_ai"),
            candidate_fidelity=raw.get("candidate_fidelity", "db-reconstructed"),
            answer=AnswerKey(**raw["answer"]),
        )


def load_corpus() -> list[CorpusEntry]:
    if not CORPUS_DIR.is_dir():
        return []
    out: list[CorpusEntry] = []
    for p in sorted(CORPUS_DIR.glob("*.json")):
        if p.name.startswith("_"):
            continue
        raw = json.loads(p.read_text(encoding="utf-8"))
        if "answer" not in raw or "evidence" not in raw:
            continue  # not a fixture (e.g. a spec or index file living alongside)
        out.append(CorpusEntry.from_json(p))
    return out


# --------------------------------------------------------------------------
# Frozen AI client — replays the recording, refuses anything unrecorded
# --------------------------------------------------------------------------


class FrozenAIClient:
    """Stands in for ``AnthropicIdentificationClient`` in offline scoring.
    Replays the fixture's recorded tool responses; never touches the network."""

    model_name = "frozen"

    def __init__(self, entry: CorpusEntry) -> None:
        self._entry = entry
        self.identify_calls = 0
        self.series_calls = 0

    async def identify(
        self, prompt: str, *, ground: bool = False
    ) -> tuple[AIIdentificationResult, dict]:
        self.identify_calls += 1
        rec = self._entry.recorded_ai
        if rec is None:
            raise _NoRecording("identify_book")
        return AIIdentificationResult.from_tool_input(rec), {"stop_reason": "tool_use", "frozen": True}

    async def identify_series(self, title: str, author: str | None) -> tuple[AISeriesResult, dict]:
        self.series_calls += 1
        rec = self._entry.recorded_series_ai
        if rec is None:
            # identify_series' own contract: null when unsure. Neutral default
            # for a fixture captured before the series lookup existed.
            return AISeriesResult(series=None, series_number=None), {"frozen": True, "unrecorded": True}
        return AISeriesResult.from_tool_input(rec), {"frozen": True}


class _NoRecording(Exception):
    """Raised by FrozenAIClient when offline scoring reaches an un-recorded
    model call. Deliberately NOT an AIIdentificationError — that class is
    caught inside IdentificationService.identify and turned into a low-confidence
    fallback; this must propagate out so score_corpus() can mark the entry
    skipped_offline instead of scoring a filename-stem guess."""


# --------------------------------------------------------------------------
# Running one entry
# --------------------------------------------------------------------------


@dataclass
class Prediction:
    title: str | None
    author: str | None
    series: str | None
    series_number: float | None
    computed_confidence: int
    model: str
    needs_review: bool
    fast_path: bool
    skipped_offline: bool = False
    error: str | None = None


_scoring_engine = None
_scoring_sessionmaker = None


async def _resolve_via_db(pred_title, pred_author, pred_series, pred_series_number,
                          isbn13, isbn10) -> tuple[str, str | None, str | None, float | None]:
    """Round-trip the identification through the real resolve_book against a
    throw-away empty in-memory DB, and read back the canonical row. On an
    empty DB this returns the input strings unchanged; the call is here so
    Stage J can pre-seed rows without touching the harness."""
    global _scoring_engine, _scoring_sessionmaker
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sm() as session:
            book = await resolve_book(
                session,
                title=pred_title or "",
                author=pred_author,
                series=pred_series,
                series_number=pred_series_number,
                isbn13=isbn13,
                isbn10=isbn10,
            )
            await session.flush()
            author_name = (
                (await session.get(models.Author, book.author_id)).name if book.author_id else None
            )
            series_name = (
                (await session.get(models.Series, book.series_id)).name if book.series_id else None
            )
            return book.canonical_title, author_name, series_name, book.series_number
    finally:
        await engine.dispose()


async def run_entry(
    entry: CorpusEntry,
    *,
    identification_service: IdentificationService | None = None,
    live: bool = False,
) -> Prediction:
    if live:
        svc = identification_service or IdentificationService()
        candidates = entry.candidates  # eval_identification --live refreshes these itself
    else:
        client = FrozenAIClient(entry)
        svc = identification_service or IdentificationService(ai_client=client)
        candidates = entry.candidates

    try:
        result = await svc.identify(
            filename=entry.filename,
            evidence=entry.evidence,
            candidates=candidates,
            corrections=[],
        )
    except _NoRecording as exc:
        return Prediction(
            title=None, author=None, series=None, series_number=None,
            computed_confidence=0, model="skipped_offline", needs_review=True,
            fast_path=False, skipped_offline=True, error=str(exc),
        )

    r_title, r_author, r_series, r_series_number = await _resolve_via_db(
        result.title, result.author, result.series, result.series_number,
        entry.evidence.isbn13, entry.evidence.isbn10,
    )
    return Prediction(
        title=r_title,
        author=r_author,
        series=r_series,
        series_number=r_series_number,
        computed_confidence=result.computed_confidence,
        model=result.model,
        needs_review=result.needs_human_review,
        fast_path=(result.model == "deterministic"),
    )


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# words that are noise when comparing series names across sources — leading
# articles and the generic "kind of series" nouns that some catalogs append.
_SERIES_NOISE = frozenset(
    {"the", "a", "an", "series", "trilogy", "duology", "quartet", "sequence", "saga", "novels", "books"}
)


def series_key(name: Any) -> frozenset:
    return normalize_words(name) - _SERIES_NOISE


def series_matches(a: Any, b: Any) -> bool:
    """Series names are phrased loosely across sources — "Nevernight Chronicle"
    / "The Nevernight Chronicle", "Culture" / "Culture series", "Dragonlance:
    The War of Souls" / "The War of Souls". Match if, after dropping articles
    and generic series-nouns, one name's words are contained in the other's."""
    ka, kb = series_key(a), series_key(b)
    if not ka or not kb:
        return not ka and not kb
    return ka <= kb or kb <= ka


def field_matches(fieldname: str, predicted: Any, expected: Any) -> bool:
    if fieldname == "title":
        return normalize_title_strict(predicted or "") == normalize_title_strict(expected or "")
    if fieldname == "series":
        return series_matches(predicted, expected)
    if fieldname == "author":
        return normalize_words(predicted) == normalize_words(expected)
    if fieldname == "series_number":
        return _num(predicted) == _num(expected)
    raise ValueError(fieldname)


@dataclass
class EntryResult:
    entry: CorpusEntry
    prediction: Prediction
    field_ok: dict[str, bool]      # only fields with a scorable answer key
    exact: bool                    # all scorable fields correct (needs >=1 scorable)

    @property
    def wrong_fields(self) -> list[str]:
        return [f for f, ok in self.field_ok.items() if not ok]


@dataclass
class CorpusReport:
    results: list[EntryResult] = field(default_factory=list)
    scored: int = 0               # entries with >=1 scorable field
    skipped_offline: int = 0      # AI path with no recorded answer
    skipped_unresolved: int = 0   # triangulation reached no scorable field at all
    include_weak: bool = True
    precision: dict[str, float] = field(default_factory=dict)   # hits / coverage
    coverage: dict[str, int] = field(default_factory=dict)      # entries with a scorable answer
    exact_match: float = 0.0
    fast_path_rate: float = 0.0
    wrong_auto_organized: int = 0  # confidence >=85 but a scorable field was wrong

    def as_baseline_dict(self, corpus_size: int, generated: str) -> dict:
        return {
            "corpus_size": corpus_size,
            "scored": self.scored,
            "generated": generated,
            "include_weak": self.include_weak,
            "coverage": dict(self.coverage),
            "precision": {k: round(v, 4) for k, v in self.precision.items()},
            "exact_match": round(self.exact_match, 4),
        }


async def score_corpus(
    entries: list[CorpusEntry] | None = None,
    *,
    identification_service: IdentificationService | None = None,
    live: bool = False,
    include_weak: bool = True,
) -> CorpusReport:
    entries = entries if entries is not None else load_corpus()
    report = CorpusReport(include_weak=include_weak)
    field_hits = {f: 0 for f in FIELDS}
    field_cov = {f: 0 for f in FIELDS}
    exact_hits = 0
    fast_paths = 0

    for entry in entries:
        pred = await run_entry(entry, identification_service=identification_service, live=live)
        if pred.skipped_offline:
            report.skipped_offline += 1
            report.results.append(
                EntryResult(entry=entry, prediction=pred, field_ok={}, exact=False)
            )
            continue

        scorable = [f for f in FIELDS if entry.answer.scorable(f, include_weak=include_weak)]
        if not scorable:
            report.skipped_unresolved += 1
            report.results.append(
                EntryResult(entry=entry, prediction=pred, field_ok={}, exact=False)
            )
            continue

        field_ok = {
            f: field_matches(f, getattr(pred, f), getattr(entry.answer, f)) for f in scorable
        }
        exact = all(field_ok.values())
        for f, ok in field_ok.items():
            field_hits[f] += int(ok)
            field_cov[f] += 1
        exact_hits += int(exact)
        fast_paths += int(pred.fast_path)
        if pred.computed_confidence >= 85 and not exact:
            report.wrong_auto_organized += 1
        report.results.append(EntryResult(entry=entry, prediction=pred, field_ok=field_ok, exact=exact))

    report.scored = sum(1 for r in report.results if r.field_ok)
    n = report.scored or 1
    report.coverage = field_cov
    report.precision = {f: (field_hits[f] / field_cov[f] if field_cov[f] else 1.0) for f in FIELDS}
    report.exact_match = exact_hits / n
    report.fast_path_rate = fast_paths / n
    return report


# --------------------------------------------------------------------------
# Baseline block in IDENTIFICATION-EVAL.md
# --------------------------------------------------------------------------

_EVAL_MD = Path(__file__).resolve().parents[2] / "IDENTIFICATION-EVAL.md"
_BASELINE_BEGIN = "<!-- eval-baseline:begin -->"
_BASELINE_END = "<!-- eval-baseline:end -->"


def load_baseline(path: Path | None = None) -> dict | None:
    path = path or _EVAL_MD
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    if _BASELINE_BEGIN not in text or _BASELINE_END not in text:
        return None
    block = text.split(_BASELINE_BEGIN, 1)[1].split(_BASELINE_END, 1)[0]
    start, end = block.find("{"), block.rfind("}")
    if start < 0 or end < 0:
        return None
    return json.loads(block[start : end + 1])


def render_baseline_block(payload: dict) -> str:
    return f"{_BASELINE_BEGIN}\n```json\n{json.dumps(payload, indent=2)}\n```\n{_BASELINE_END}"
