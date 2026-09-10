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


def test_score_prefers_a_reliable_server_with_a_real_size():
    good = _book("The Dispossessed", "Ursula K Le Guin", server="Bsk", size="1.2MB")
    flaky = _book("The Dispossessed", "Ursula K Le Guin", server="Dumbledore", size="N/A")
    sg = score_candidate("The Dispossessed", "Ursula K Le Guin", good)
    sf = score_candidate("The Dispossessed", "Ursula K Le Guin", flaky)
    assert sg > sf
    # a strong title+author match on a flaky server still stays a usable alternative
    assert sf >= 0.72

    ranked = svc._rank("The Dispossessed", "Ursula K Le Guin", [flaky, good])
    assert ranked[0][1].server == "Bsk"


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
    assert result == {"targets": 2, "outstanding": 0, "searched": 2, "withCandidates": 1}

    rows = {r.request_id: r for r in (await db_session.execute(select(AcquisitionCandidate))).scalars()}
    assert rows["r1"].status == AcquisitionStatus.pending
    assert rows["r1"].source == "wishlist"
    assert rows["r1"].candidate_format == "epub"
    assert len(rows["r1"].alternatives_json) == 1  # the retail one; mobi filtered out
    assert rows["r2"].status == AcquisitionStatus.no_match
    assert "r3" not in rows  # not "wanted"


async def test_refresh_pulls_from_all_three_sources_and_respects_limit(db_session, monkeypatch):
    targets = [
        {"request_id": "r1", "source": "wishlist", "title": "Book One", "author": "A", "isbn13": None},
        {"request_id": "wtr:x", "source": "want_to_read", "title": "Book Two", "author": "B", "isbn13": None},
        {"request_id": "list:y", "source": "list", "title": "Book Three", "author": "C", "isbn13": None},
    ]

    async def fake_gather(p, f):
        return targets

    monkeypatch.setattr(svc, "_gather_targets", fake_gather)

    async def fake_search(query):
        return SearchOutcome(results=[_book(query.rsplit(" ", 1)[0], "Whoever")])

    monkeypatch.setattr(svc.openbooks_service, "search", fake_search)

    result = await svc.refresh_candidates(object(), "f", limit=2)
    assert result == {"targets": 3, "outstanding": 1, "searched": 2, "withCandidates": 2}

    rows = {r.request_id: r.source for r in (await db_session.execute(select(AcquisitionCandidate))).scalars()}
    assert rows == {"r1": "wishlist", "wtr:x": "want_to_read"}  # 3rd left for next run

    result = await svc.refresh_candidates(object(), "f", limit=2)
    assert result["searched"] == 1 and result["outstanding"] == 0  # just the leftover


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


async def test_list_suggestions_reads_the_two_sidecars(monkeypatch):
    import app.services.library_index_service as lib

    files = {
        "bookbrain-reading.json": {
            "wantUnowned": [
                {"title": "The Blade Itself", "author": "Joe Abercrombie", "isbn13": "9780575079793"},
                {"title": "  ", "author": "x"},  # blank → dropped
                "junk",
            ]
        },
        "bookbrain-lists.json": {
            "candidates": [
                {"title": "Gideon the Ninth", "author": "Tamsyn Muir", "isbn13": None, "fromList": "Best of 2019"},
            ]
        },
    }
    monkeypatch.setattr(lib, "_read_json_file", lambda provider, folder, name: files.get(name, {}))

    out = await svc.list_suggestions(object(), "lib")
    assert [s["title"] for s in out["want_to_read"]] == ["The Blade Itself"]
    assert out["want_to_read"][0]["isbn13"] == "9780575079793"
    assert out["from_lists"] == [
        {"title": "Gideon the Ninth", "author": "Tamsyn Muir", "isbn13": None, "from_list": "Best of 2019"}
    ]


async def test_list_suggestions_tolerates_missing_sidecars(monkeypatch):
    import app.services.library_index_service as lib

    monkeypatch.setattr(lib, "_read_json_file", lambda *a: {})
    out = await svc.list_suggestions(object(), "lib")
    assert out == {"want_to_read": [], "from_lists": []}


async def test_list_requests_drops_a_sourced_book_now_in_the_library(db_session, monkeypatch):
    from app.data.models import Author, Book, File, FileStatus

    author = Author(name="Joe Abercrombie")
    db_session.add(author)
    await db_session.flush()
    book = Book(canonical_title="The Blade Itself", author_id=author.id)
    db_session.add(book)
    await db_session.flush()
    db_session.add(
        File(
            drive_file_id="d1",
            filename="x.epub",
            sha256="s",
            size_bytes=1,
            status=FileStatus.organised,
            book_id=book.id,
        )
    )
    db_session.add(
        AcquisitionCandidate(
            request_id="wtr:x",
            source="want_to_read",
            request_title="The Blade Itself",
            request_author="Joe Abercrombie",
            status=AcquisitionStatus.approved,
            candidate_full="!Bsk x.epub",
        )
    )
    db_session.add(
        AcquisitionCandidate(
            request_id="wtr:y",
            source="want_to_read",
            request_title="Still Missing",
            request_author="Someone",
            status=AcquisitionStatus.approved,
            candidate_full="!Bsk y.epub",
        )
    )
    await db_session.commit()

    monkeypatch.setattr(svc, "_read_wishlist", lambda p, f: _wishlist([]))
    views = await svc.list_requests(object(), "lib")

    assert [v.request_id for v in views] == ["wtr:y"]  # the in-library one is gone
    remaining = (await db_session.execute(select(AcquisitionCandidate))).scalars().all()
    assert {r.request_id for r in remaining} == {"wtr:y"}  # and its row was deleted


async def test_list_requests_shows_unsearched_wishlist_items(db_session, monkeypatch):
    db_session.add(
        AcquisitionCandidate(
            request_id="r1",
            source="wishlist",
            request_title="Searched One",
            request_author="A",
            status=AcquisitionStatus.pending,
            candidate_full="!Bsk x.epub",
            candidate_title="Searched One",
        )
    )
    await db_session.commit()

    items = [
        {"id": "r1", "title": "Searched One", "author": "A", "status": "wanted"},
        {"id": "r2", "title": "Not Yet Searched", "author": "B", "status": "wanted", "requestedBy": "Jo"},
        {"id": "r3", "title": "Declined One", "author": "C", "status": "declined"},
    ]
    monkeypatch.setattr(svc, "_read_wishlist", lambda p, f: _wishlist(items))

    views = {v.request_id: v for v in await svc.list_requests(object(), "lib")}
    assert set(views) == {"r1", "r2"}  # r3 declined → hidden
    assert views["r1"].status == "pending"
    assert views["r2"].status == "unsearched"
    assert views["r2"].requested_by == "Jo"
