import pytest

from app.data.models import (
    AIDecision,
    Author,
    Book,
    BookCandidate,
    File,
    FileStatus,
    Identifier,
    IdentifierType,
    Review,
    ReviewStatus,
    Series,
)
from app.providers.epub.parser import EpubEvidence
from app.providers.metadata.types import MetadataCandidate
from app.services import reident_audit_service as svc
from app.services.identification_service import hash_evidence
from app.services.scan_service import _evidence_to_metadata_sources
from app.schemas.reident_audit import ReidentSignal


@pytest.fixture(autouse=True)
def _route_db_to_test_session(db_session, monkeypatch):
    class _CM:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(svc, "async_session_factory", lambda: _CM())


class _FakeProvider:
    def __init__(self, name, isbn_results=None, title_results=None):
        self.name = name
        self._isbn = isbn_results or []
        self._title = title_results or []
        self.calls = []

    async def search_by_isbn(self, isbn):
        self.calls.append(("isbn", isbn))
        return list(self._isbn)

    async def search_by_title_author(self, title, author):
        self.calls.append(("title", title, author))
        return list(self._title)


def _fake_candidate_service(**providers_kw):
    from app.services.candidate_service import CandidateService

    return CandidateService(providers=list(providers_kw.values()))


async def _make_book(
    db_session,
    *,
    title,
    author=None,
    series=None,
    series_number=None,
    evidence: EpubEvidence,
    candidates: list[MetadataCandidate] | None = None,
    computed_confidence=97,
    model="claude-opus-5",
    isbn13=None,
    corrected=False,
):
    author_row = Author(name=author) if author else None
    series_row = Series(name=series) if series else None
    for r in (author_row, series_row):
        if r is not None:
            db_session.add(r)
    await db_session.flush()

    book = Book(
        canonical_title=title,
        author_id=author_row.id if author_row else None,
        series_id=series_row.id if series_row else None,
        series_number=series_number,
    )
    db_session.add(book)
    await db_session.flush()

    if isbn13:
        db_session.add(
            Identifier(book_id=book.id, type=IdentifierType.isbn13, value=isbn13, source="test")
        )

    f = File(
        drive_file_id=f"drive-{title}",
        filename=f"{title}.epub",
        sha256=("a" * 64),
        size_bytes=1000,
        status=FileStatus.organised,
        book_id=book.id,
        quality_score=80,
    )
    db_session.add(f)
    await db_session.flush()

    for src in _evidence_to_metadata_sources(f.filename, evidence):
        src.file_id = f.id
        db_session.add(src)

    for c in candidates or []:
        db_session.add(
            BookCandidate(
                file_id=f.id,
                title=c.title,
                author=", ".join(c.authors) if c.authors else None,
                series=c.series,
                series_number=c.series_number,
                source=c.source,
                confidence_component_json={},
            )
        )

    db_session.add(
        AIDecision(
            file_id=f.id,
            model=model,
            prompt_hash="p",
            evidence_hash=hash_evidence(f.filename, evidence, candidates or []),
            raw_response_json={},
            computed_confidence=computed_confidence,
            needs_human_review=False,
        )
    )

    if corrected:
        db_session.add(
            Review(
                file_id=f.id,
                status=ReviewStatus.corrected,
                proposed_json={},
                correction_json={"title": title, "author": author, "series": series},
            )
        )

    await db_session.commit()
    return book, f


# --------------------------------------------------------------------------


async def test_free_pass_makes_zero_anthropic_calls(db_session, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("the free pass must not construct an AI client")

    monkeypatch.setattr(
        "app.providers.ai.anthropic_client.AnthropicIdentificationClient.__init__", _boom
    )

    ev = EpubEvidence(title="Dune", authors=["Frank Herbert"], language="en")
    await _make_book(db_session, title="Dune", author="Frank Herbert", evidence=ev)

    report = await svc.build_reident_report(candidate_service=_fake_candidate_service())

    assert report.checked == 1
    assert report.generated_at is not None


async def test_flags_bogus_series_but_not_a_clean_book(db_session):
    clean_ev = EpubEvidence(
        title="Mistborn: The Final Empire",
        authors=["Brandon Sanderson"],
        language="en",
        series="Mistborn",
    )
    await _make_book(
        db_session,
        title="Mistborn: The Final Empire",
        author="Brandon Sanderson",
        series="Mistborn",
        series_number=1,
        evidence=clean_ev,
    )

    # Series set by the AI, nothing backs it up.
    bogus_ev = EpubEvidence(title="Elantris", authors=["Brandon Sanderson"], language="en")
    await _make_book(
        db_session,
        title="Elantris",
        author="Brandon Sanderson",
        series="The Cosmere Chronicles",
        series_number=2,
        evidence=bogus_ev,
    )

    prov = _FakeProvider("g", title_results=[])
    report = await svc.build_reident_report(candidate_service=_fake_candidate_service(g=prov))

    flagged = {d.stored_title: d for d in report.divergences}
    assert "Elantris" in flagged
    assert ReidentSignal.series_unverified in flagged["Elantris"].signals
    assert "Mistborn: The Final Empire" not in flagged


async def test_flags_wrong_title_when_providers_agree_otherwise(db_session):
    ev = EpubEvidence(title="The Way of Kigns", authors=["Brandon Sanderson"], language="en")
    await _make_book(
        db_session,
        title="The Way of Kigns",  # typo'd stored title
        author="Brandon Sanderson",
        evidence=ev,
    )

    right = [
        MetadataCandidate(title="The Way of Kings", authors=["Brandon Sanderson"], source="g"),
        MetadataCandidate(title="The Way of Kings", authors=["Brandon Sanderson"], source="o"),
    ]
    prov_g = _FakeProvider("g", title_results=right[:1])
    prov_o = _FakeProvider("o", title_results=right[1:])
    report = await svc.build_reident_report(
        candidate_service=_fake_candidate_service(g=prov_g, o=prov_o)
    )

    [d] = report.divergences
    assert ReidentSignal.title_disagrees in d.signals


async def test_human_corrected_book_is_not_flagged_for_provider_disagreement(db_session):
    ev = EpubEvidence(title="My Preferred Title", authors=["A. Writer"], language="en")
    await _make_book(
        db_session,
        title="My Preferred Title",
        author="A. Writer",
        series="A Human-Chosen Series",
        evidence=ev,
        corrected=True,
    )

    other = [
        MetadataCandidate(title="Some Other Title", authors=["A. Writer"], source="g"),
        MetadataCandidate(title="Some Other Title", authors=["A. Writer"], source="o"),
    ]
    report = await svc.build_reident_report(
        candidate_service=_fake_candidate_service(
            g=_FakeProvider("g", title_results=other[:1]),
            o=_FakeProvider("o", title_results=other[1:]),
        )
    )
    assert report.divergences == []


async def test_no_provider_data_is_inconclusive_not_a_divergence(db_session):
    ev = EpubEvidence(title="Obscure Book", authors=["Nobody"], language="en")
    await _make_book(db_session, title="Obscure Book", author="Nobody", evidence=ev)

    report = await svc.build_reident_report(
        candidate_service=_fake_candidate_service(g=_FakeProvider("g"))
    )
    assert report.divergences == []
    assert report.providers_unavailable == 0


async def test_missed_duplicate_flags_both(db_session):
    for did in ("1", "2"):
        ev = EpubEvidence(title="Dune", authors=["Frank Herbert"], language="en")
        b, f = await _make_book(
            db_session, title="Dune", author="Frank Herbert", evidence=ev
        )
        f.drive_file_id = f"dune-{did}"
        f.filename = f"dune-{did}.epub"
        await db_session.commit()

    report = await svc.build_reident_report(
        candidate_service=_fake_candidate_service(g=_FakeProvider("g"))
    )
    dup = [d for d in report.divergences if ReidentSignal.possible_duplicate in d.signals]
    assert len(dup) == 2


async def test_reconstructed_evidence_hash_matches_what_identification_stored(db_session):
    # The gotcha in prompts/05: reconstruct evidence *exactly* as the
    # pipeline assembled it, or every ai_decisions cache row misses.
    ev = EpubEvidence(
        title="Dune",
        authors=["Frank Herbert", "Kevin J. Anderson"],
        language="en",
        isbn13="9780441172719",
        series="Dune",
        series_number=1.0,
        text_snippet="It was a warm night at Castle Caladan.",
    )
    _, f = await _make_book(
        db_session, title="Dune", author="Frank Herbert", evidence=ev, candidates=[]
    )

    from app.data.models import MetadataSource
    from sqlalchemy import select

    rows = (
        (await db_session.execute(select(MetadataSource).where(MetadataSource.file_id == f.id)))
        .scalars()
        .all()
    )
    reconstructed = svc.evidence_from_sources(rows)

    assert hash_evidence(f.filename, reconstructed, []) == hash_evidence(f.filename, ev, [])


async def test_dismiss_filters_the_report_without_a_rebuild(db_session):
    ev = EpubEvidence(title="Elantris", authors=["Brandon Sanderson"], language="en")
    book, _ = await _make_book(
        db_session,
        title="Elantris",
        author="Brandon Sanderson",
        series="Invented Series",
        evidence=ev,
    )
    report = await svc.build_reident_report(
        candidate_service=_fake_candidate_service(g=_FakeProvider("g"))
    )
    await svc.save_report(db_session, report)
    assert len(report.divergences) == 1

    await svc.dismiss_book(db_session, book.id)

    filtered = await svc.get_report_filtered(db_session)
    assert filtered.divergences == []

    await svc.undismiss_book(db_session, book.id)
    assert len(( await svc.get_report_filtered(db_session)).divergences) == 1


async def test_deep_check_is_capped_and_only_touches_flagged_rows(db_session, monkeypatch):
    ev = EpubEvidence(title="Elantris", authors=["Brandon Sanderson"], language="en")
    book, _ = await _make_book(
        db_session,
        title="Elantris",
        author="Brandon Sanderson",
        series="Invented Series",
        evidence=ev,
    )
    report = await svc.build_reident_report(
        candidate_service=_fake_candidate_service(g=_FakeProvider("g"))
    )
    await svc.save_report(db_session, report)

    from app.providers.ai.types import AIAuditResult

    calls = []

    class _FakeAI:
        async def audit_book_identity(self, prompt):
            calls.append(prompt)
            return AIAuditResult(
                verdict="stored_is_wrong",
                series_is_real=False,
                corrected_title="Elantris",
                corrected_author="Brandon Sanderson",
                corrected_series=None,
                corrected_series_number=None,
                explanation="No such series.",
            )

    # A book id that isn't in the report must be ignored.
    result = await svc.run_deep_check(db_session, [book.id, 99999], ai_client=_FakeAI())

    assert result.rechecked == 1
    assert result.stored_is_wrong == 1
    assert len(calls) == 1

    refreshed = await svc.get_cached_report(db_session)
    row = next(d for d in refreshed.divergences if d.book_id == book.id)
    assert row.deep_check_verdict == "stored_is_wrong"
    assert row.deep_check_suggested_series is None


async def test_deep_check_estimate_caps_and_prices(db_session):
    est = await svc.estimate_deep_check(db_session, list(range(1, 200)))
    assert est.cap == svc.DEEP_CHECK_CAP
    assert est.will_check <= svc.DEEP_CHECK_CAP
    assert est.estimated_cost_usd == round(est.will_check * 0.02, 2)
