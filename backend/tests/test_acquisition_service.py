import pytest
from sqlalchemy import select

from app.data.models import AcquisitionCandidate, AcquisitionStatus
from app.services import acquisition_service as svc
from app.services.acquisition_service import _Wishlist, score_candidate
from app.services.openbooks_service import BookResult, SearchOutcome


@pytest.fixture(autouse=True)
def _route_db(db_session, monkeypatch):
    class _CM:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(svc, "async_session_factory", lambda: _CM())
    monkeypatch.setattr(svc, "_SEARCH_SPACING_SECONDS", 0)


def _book(title, author, fmt="epub", full=None, server="Bsk", size="1.2MB"):
    return BookResult(
        server=server,
        author=author,
        title=title,
        format=fmt,
        size=size,
        full=full or f"!{server} {author} - {title}.{fmt}",
    )


# --- scoring -------------------------------------------------------------


def test_score_exact_and_contained_title():
    b = _book("The Left Hand of Darkness", "Ursula K Le Guin")
    assert score_candidate("The Left Hand of Darkness", "Ursula K Le Guin", b) >= 0.9


def test_score_handles_openbooks_swapping_author_and_title():
    swapped = _book(title="Ursula Le Guin", author="The Dispossessed")
    assert score_candidate("The Dispossessed", "Ursula K Le Guin", swapped) >= 0.85


def test_score_rejects_a_different_book():
    b = _book("A Wizard of Earthsea", "Ursula K Le Guin")
    assert score_candidate("The Dispossessed", "Ursula K Le Guin", b) == 0.0


# --- refresh ------------------------------------------------------------


def _wishlist(items):
    return _Wishlist(file_id="wl1", raw={"version": 2, "items": items}, items=items)


async def test_refresh_creates_pending_and_no_match_rows(db_session, monkeypatch):
    items = [
        {"id": "r1", "title": "The Dispossessed", "author": "Ursula K Le Guin", "status": "wanted"},
        {"id": "r2", "title": "Some Obscure Thing", "author": "Nobody", "status": "wanted"},
        {"id": "r3", "title": "Already Handled", "author": "X", "status": "sourced"},
    ]
    monkeypatch.setattr(svc, "_read_wishlist", lambda p, f: _wishlist(items))

    async def fake_search(query):
        if "Dispossessed" in query:
            return SearchOutcome(
                results=[
                    _book("The Dispossessed", "Ursula K Le Guin"),
                    _book("The Dispossessed", "Ursula K Le Guin", fmt="mobi"),  # dropped: not epub
                    _book("The Dispossessed (retail)", "Ursula K Le Guin", server="Ook"),
                ]
            )
        return SearchOutcome(results=[_book("Totally Different Book", "Someone Else")])

    monkeypatch.setattr(svc.openbooks_service, "search", fake_search)

    result = await svc.refresh_candidates(object(), "libfolder")
    assert result == {"requests": 2, "searched": 2, "withCandidates": 1}

    rows = {r.request_id: r for r in (await db_session.execute(select(AcquisitionCandidate))).scalars()}
    assert rows["r1"].status == AcquisitionStatus.pending
    assert rows["r1"].candidate_format == "epub"
    assert len(rows["r1"].alternatives_json) == 1  # the retail one; mobi filtered out
    assert rows["r2"].status == AcquisitionStatus.no_match
    assert "r3" not in rows  # not "wanted"


async def test_refresh_skips_already_approved_unchanged_request(db_session, monkeypatch):
    db_session.add(
        AcquisitionCandidate(
            request_id="r1",
            request_title="The Dispossessed",
            request_author="Ursula K Le Guin",
            status=AcquisitionStatus.approved,
        )
    )
    await db_session.commit()

    items = [{"id": "r1", "title": "The Dispossessed", "author": "Ursula K Le Guin", "status": "wanted"}]
    monkeypatch.setattr(svc, "_read_wishlist", lambda p, f: _wishlist(items))

    called = False

    async def fake_search(query):
        nonlocal called
        called = True
        return SearchOutcome(results=[])

    monkeypatch.setattr(svc.openbooks_service, "search", fake_search)

    result = await svc.refresh_candidates(object(), "libfolder")
    assert result["searched"] == 0
    assert called is False


# --- approve / skip / reset -------------------------------------------


async def test_approve_downloads_and_marks_sourced(db_session, monkeypatch):
    db_session.add(
        AcquisitionCandidate(
            request_id="r1",
            request_title="The Dispossessed",
            request_author="Ursula K Le Guin",
            status=AcquisitionStatus.pending,
            candidate_full="!Bsk Ursula K Le Guin - The Dispossessed.epub",
            candidate_title="The Dispossessed",
            candidate_author="Ursula K Le Guin",
            candidate_format="epub",
        )
    )
    await db_session.commit()

    seen = {}

    async def fake_acquire(full, filename, provider, inbox):
        seen["full"] = full
        seen["filename"] = filename
        return {"filename": filename, "drive_file_id": "d1", "size_bytes": 123}

    monkeypatch.setattr(svc.acquire_service, "acquire_to_inbox", fake_acquire)
    marked = {}
    monkeypatch.setattr(
        svc, "_mark_wishlist_sourced", lambda p, f, rid: marked.setdefault("rid", rid) or True
    )

    result = await svc.approve_request("r1", None, object(), "inbox", "lib")
    assert result["drive_file_id"] == "d1"
    assert seen["full"] == "!Bsk Ursula K Le Guin - The Dispossessed.epub"
    assert seen["filename"] == "Ursula K Le Guin - The Dispossessed.epub"
    assert marked["rid"] == "r1"

    row = (await db_session.execute(select(AcquisitionCandidate))).scalar_one()
    assert row.status == AcquisitionStatus.approved
    assert row.resolved_at is not None


async def test_approve_with_alternative_full(db_session, monkeypatch):
    db_session.add(
        AcquisitionCandidate(
            request_id="r1",
            request_title="T",
            status=AcquisitionStatus.pending,
            candidate_full="!Bsk best.epub",
            candidate_title="best",
            alternatives_json=[{"full": "!Ook alt.epub", "title": "alt", "author": "A"}],
        )
    )
    await db_session.commit()

    seen = {}

    async def fake_acquire(full, filename, provider, inbox):
        seen["full"] = full
        return {"filename": filename, "drive_file_id": "d1", "size_bytes": 1}

    monkeypatch.setattr(svc.acquire_service, "acquire_to_inbox", fake_acquire)
    monkeypatch.setattr(svc, "_mark_wishlist_sourced", lambda *a: True)

    await svc.approve_request("r1", "!Ook alt.epub", object(), "inbox", "lib")
    assert seen["full"] == "!Ook alt.epub"


async def test_skip_and_reset(db_session, monkeypatch):
    db_session.add(
        AcquisitionCandidate(request_id="r1", request_title="T", status=AcquisitionStatus.pending)
    )
    await db_session.commit()

    await svc.skip_request("r1")
    row = (await db_session.execute(select(AcquisitionCandidate))).scalar_one()
    assert row.status == AcquisitionStatus.skipped

    await svc.reset_request("r1")
    rows = (await db_session.execute(select(AcquisitionCandidate))).scalars().all()
    assert rows == []


async def test_list_requests_excludes_deleted_and_sorts(db_session, monkeypatch):
    for rid, status, score in [
        ("r1", AcquisitionStatus.no_match, None),
        ("r2", AcquisitionStatus.pending, 0.95),
        ("gone", AcquisitionStatus.pending, 0.9),
    ]:
        db_session.add(
            AcquisitionCandidate(
                request_id=rid,
                request_title=rid,
                status=status,
                score=score,
                candidate_full=("!x.epub" if score else None),
            )
        )
    await db_session.commit()

    items = [
        {"id": "r1", "title": "One", "author": None, "status": "wanted", "requestedBy": "Jo"},
        {"id": "r2", "title": "Two", "author": "A", "status": "wanted", "requestedBy": "Sam"},
    ]
    monkeypatch.setattr(svc, "_read_wishlist", lambda p, f: _wishlist(items))

    views = await svc.list_requests(object(), "lib")
    assert [v.request_id for v in views] == ["r2", "r1"]  # pending before no_match; "gone" dropped
    assert views[0].requested_by == "Sam"
