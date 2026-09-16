import os
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from app.data.models import (
    AcquisitionCandidate,
    AcquisitionStatus,
    LibrarrRequest,
    LibrarrRequestStatus,
    LocalFile,
    LocalFileStatus,
)
from app.services import acquisition_service, local_scan_service
from app.services import torrent_service as svc
from sqlalchemy import select

LIBRARR_URL = "http://librarr.test"


@pytest.fixture(autouse=True)
def _route_db(db_session, monkeypatch):
    class _CM:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(svc, "async_session_factory", lambda: _CM())
    monkeypatch.setattr(acquisition_service, "async_session_factory", lambda: _CM())


@pytest.fixture(autouse=True)
def _reset_budget(monkeypatch):
    # Cycle budgets are process-wide dicts in production — reset between
    # tests so one test's submissions don't spend another's budget.
    monkeypatch.setattr(acquisition_service, "_recent_search_times", {})


@pytest.fixture(autouse=True)
def _fast_quick_poll(monkeypatch):
    # Real values (5s interval, 45s max wait) would make any test that
    # reaches _quick_resolve take up to 45 real seconds — shrink both by
    # 1000x so the same number of poll attempts still happens, just fast.
    monkeypatch.setattr(svc, "_QUICK_POLL_INTERVAL_SECONDS", 0.005)
    monkeypatch.setattr(svc, "_QUICK_POLL_MAX_WAIT_SECONDS", 0.045)


def _target(title="Departure", author="A G Riddle", request_id="wl-dep", source="wishlist"):
    return {"request_id": request_id, "source": source, "title": title, "author": author, "isbn13": None}


@pytest.fixture
def _torrent_idle(monkeypatch):
    """All the 'is it idle / configured?' gates pass — mirrors
    test_acquisition_service.py's _autoget_idle fixture, scoped to
    torrent_service's own module-level imports."""

    class _Cfg:
        torrent_enabled = True
        librarr_url = LIBRARR_URL
        librarr_api_key = "test-key"

    import app.core.config as cfg

    monkeypatch.setattr(cfg, "get_settings", lambda: _Cfg())
    monkeypatch.setattr(svc, "get_settings", lambda: _Cfg())

    from app.core.settings_keys import TORRENT_AUTOGET_ENABLED
    from app.data.repositories.settings_repository import SettingsRepository

    async def _repo_get(self, key):
        return "true" if key == TORRENT_AUTOGET_ENABLED else None

    monkeypatch.setattr(SettingsRepository, "get", _repo_get)
    monkeypatch.setattr(acquisition_service, "has_active_refresh_job", lambda: False)

    import app.services.scan_service as scan_svc

    class _ScanSvc:
        def has_running_job(self):
            return False

    monkeypatch.setattr(scan_svc, "get_scan_service", lambda: _ScanSvc())

    import app.services.auth_service as auth_svc

    class _Auth:
        async def get_credentials(self, repo):
            return object()

    monkeypatch.setattr(auth_svc, "get_auth_service", lambda: _Auth())

    import app.services.drive_service as ds

    class _Folder:
        folder_id = "lib-folder"

    async def _library(repo):
        return _Folder()

    monkeypatch.setattr(ds.DriveService, "get_library_folder_config", staticmethod(_library))

    import app.providers.drive.client as dc

    monkeypatch.setattr(dc, "build_drive_service", lambda creds: None)
    monkeypatch.setattr(svc, "DriveProvider", lambda svc_obj: object())

    targets = {"items": [_target()]}

    async def _fake_gather(provider, folder):
        return list(targets["items"])

    monkeypatch.setattr(acquisition_service, "gather_acquisition_targets", _fake_gather)

    return targets


# --- _submit_to_librarr / _poll_librarr (HTTP-level) ----------------------
# Response shapes below are confirmed live against a real Librarr instance
# 2026-09-16, not guessed: every request-lifecycle response nests the
# record under "request", and a fresh request starts "pending" — Librarr
# requires an explicit approve call before it does anything, with no
# auto-approve setting, which is why _submit_to_librarr always chains one.


@respx.mock
async def test_submit_to_librarr_creates_then_approves_and_returns_the_id():
    respx.post(f"{LIBRARR_URL}/api/requests").mock(
        return_value=httpx.Response(
            201, json={"request": {"id": "req-1", "status": "pending"}, "success": True}
        )
    )
    approve_route = respx.put(f"{LIBRARR_URL}/api/requests/req-1/approve").mock(
        return_value=httpx.Response(200, json={"request": {"id": "req-1", "status": "approved"}, "success": True})
    )
    async with httpx.AsyncClient(base_url=LIBRARR_URL) as client:
        result = await svc._submit_to_librarr(client, "Departure", "A G Riddle")
    assert result == "req-1"
    assert approve_route.called


@respx.mock
async def test_submit_to_librarr_returns_none_on_http_error():
    respx.post(f"{LIBRARR_URL}/api/requests").mock(return_value=httpx.Response(500))
    async with httpx.AsyncClient(base_url=LIBRARR_URL) as client:
        result = await svc._submit_to_librarr(client, "Departure", "A G Riddle")
    assert result is None


@respx.mock
async def test_submit_to_librarr_returns_none_on_missing_id():
    respx.post(f"{LIBRARR_URL}/api/requests").mock(
        return_value=httpx.Response(201, json={"request": {"status": "pending"}, "success": True})
    )
    async with httpx.AsyncClient(base_url=LIBRARR_URL) as client:
        result = await svc._submit_to_librarr(client, "Departure", "A G Riddle")
    assert result is None


@respx.mock
async def test_submit_to_librarr_returns_none_when_approve_fails():
    respx.post(f"{LIBRARR_URL}/api/requests").mock(
        return_value=httpx.Response(201, json={"request": {"id": "req-1", "status": "pending"}, "success": True})
    )
    respx.put(f"{LIBRARR_URL}/api/requests/req-1/approve").mock(return_value=httpx.Response(500))
    async with httpx.AsyncClient(base_url=LIBRARR_URL) as client:
        result = await svc._submit_to_librarr(client, "Departure", "A G Riddle")
    assert result is None


@respx.mock
async def test_poll_librarr_unwraps_the_request_envelope():
    respx.get(f"{LIBRARR_URL}/api/requests/req-1").mock(
        return_value=httpx.Response(200, json={"request": {"status": "downloading"}, "success": True})
    )
    async with httpx.AsyncClient(base_url=LIBRARR_URL) as client:
        result = await svc._poll_librarr(client, "req-1")
    assert result == {"status": "downloading"}


@respx.mock
async def test_poll_librarr_returns_none_on_http_error():
    respx.get(f"{LIBRARR_URL}/api/requests/req-1").mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient(base_url=LIBRARR_URL) as client:
        result = await svc._poll_librarr(client, "req-1")
    assert result is None


# --- submit_tick -----------------------------------------------------------


async def test_submit_tick_skipped_when_torrent_disabled(db_session, monkeypatch, _torrent_idle):
    class _Cfg:
        torrent_enabled = False

    monkeypatch.setattr(svc, "get_settings", lambda: _Cfg())
    out = await svc.submit_tick()
    assert out == {"skipped": "torrent disabled"}


async def test_submit_tick_skipped_when_autoget_off(db_session, monkeypatch, _torrent_idle):
    from app.data.repositories.settings_repository import SettingsRepository

    async def _off(self, key):
        return "false"

    monkeypatch.setattr(SettingsRepository, "get", _off)
    out = await svc.submit_tick()
    assert out == {"skipped": "auto-get off"}


async def test_submit_tick_skipped_when_busy(db_session, monkeypatch, _torrent_idle):
    monkeypatch.setattr(acquisition_service, "has_active_refresh_job", lambda: True)
    out = await svc.submit_tick()
    assert out == {"skipped": "busy"}


async def test_submit_tick_skipped_when_no_targets(db_session, monkeypatch, _torrent_idle):
    _torrent_idle["items"] = []
    out = await svc.submit_tick()
    assert out == {"skipped": "no targets"}


@respx.mock
async def test_submit_tick_submits_a_never_tried_book_and_flips_the_candidate(
    db_session, monkeypatch, _torrent_idle
):
    # Real shape confirmed live 2026-09-16: the record is nested under
    # "request", and a fresh request starts life "pending" until approved.
    respx.post(f"{LIBRARR_URL}/api/requests").mock(
        return_value=httpx.Response(201, json={"request": {"id": "req-1", "status": "pending"}, "success": True})
    )
    approve_route = respx.put(f"{LIBRARR_URL}/api/requests/req-1/approve").mock(
        return_value=httpx.Response(200, json={"request": {"id": "req-1", "status": "approved"}, "success": True})
    )
    # _quick_resolve polls a few times before giving up — still genuinely
    # in flight the whole (fast-shrunk) window, so it doesn't chain further.
    respx.get(f"{LIBRARR_URL}/api/requests/req-1").mock(
        return_value=httpx.Response(200, json={"request": {"id": "req-1", "status": "downloading"}, "success": True})
    )

    out = await svc.submit_tick()

    assert approve_route.called
    assert out == {"submitted": "Departure", "librarr_request_id": "req-1", "quick_result": "still_in_flight"}
    row = (
        await db_session.execute(
            select(AcquisitionCandidate).where(AcquisitionCandidate.request_id == "wl-dep")
        )
    ).scalar_one()
    assert row.status == AcquisitionStatus.fetching
    assert row.candidate_provider == "torrent"

    tracked = (
        await db_session.execute(
            select(LibrarrRequest).where(LibrarrRequest.request_id == "wl-dep")
        )
    ).scalar_one()
    assert tracked.librarr_request_id == "req-1"
    # _quick_resolve's polling already advanced this past "approved" to
    # whatever Librarr reports mid-flight — a nice side effect of the fast
    # check: the tracked row is kept fresher than waiting for poll_tick.
    assert tracked.status == LibrarrRequestStatus.downloading


@respx.mock
async def test_submit_tick_chains_to_the_next_book_on_a_fast_miss(db_session, monkeypatch, _torrent_idle):
    """James's ask 2026-09-16: don't wait out the rest of the interval when
    a book fails fast — immediately try the next one. First book resolves
    as a fast miss; the tick should immediately pick and submit a second
    book in the same call, then stop there since that one is genuinely
    still in flight."""
    _torrent_idle["items"] = [
        _target("Departure", "A G Riddle", request_id="wl-dep"),
        _target("Sourcery", "Terry Pratchett", request_id="wl-src"),
    ]

    respx.post(f"{LIBRARR_URL}/api/requests").mock(
        side_effect=[
            httpx.Response(201, json={"request": {"id": "req-a", "status": "pending"}, "success": True}),
            httpx.Response(201, json={"request": {"id": "req-b", "status": "pending"}, "success": True}),
        ]
    )
    respx.put(f"{LIBRARR_URL}/api/requests/req-a/approve").mock(
        return_value=httpx.Response(200, json={"request": {"id": "req-a", "status": "approved"}, "success": True})
    )
    respx.put(f"{LIBRARR_URL}/api/requests/req-b/approve").mock(
        return_value=httpx.Response(200, json={"request": {"id": "req-b", "status": "approved"}, "success": True})
    )
    respx.get(f"{LIBRARR_URL}/api/requests/req-a").mock(
        return_value=httpx.Response(
            200,
            json={
                "request": {"id": "req-a", "status": "failed", "attention_note": "No search results found"},
                "success": True,
            },
        )
    )
    respx.get(f"{LIBRARR_URL}/api/requests/req-b").mock(
        return_value=httpx.Response(200, json={"request": {"id": "req-b", "status": "downloading"}, "success": True})
    )

    out = await svc.submit_tick()

    assert out["chained_attempts"] == 2
    assert out["attempts"][0]["submitted"] == "Departure"
    assert out["attempts"][0]["quick_result"] == "failed"
    assert out["attempts"][1]["submitted"] == "Sourcery"
    assert out["attempts"][1]["quick_result"] == "still_in_flight"

    dep = (
        await db_session.execute(
            select(AcquisitionCandidate).where(AcquisitionCandidate.request_id == "wl-dep")
        )
    ).scalar_one()
    assert dep.status == AcquisitionStatus.failed  # released back to the backlog, not left fetching

    src = (
        await db_session.execute(
            select(AcquisitionCandidate).where(AcquisitionCandidate.request_id == "wl-src")
        )
    ).scalar_one()
    assert src.status == AcquisitionStatus.fetching  # genuinely in flight — left alone


async def test_submit_tick_stops_chaining_at_the_max_per_tick(db_session, monkeypatch, _torrent_idle):
    """The chain has a hard cap so a long stretch of fast misses can't turn
    one tick into an unbounded loop."""
    _torrent_idle["items"] = [
        _target(f"Book {i}", "Author", request_id=f"wl-{i}") for i in range(svc._MAX_SUBMISSIONS_PER_TICK + 2)
    ]

    counter = {"n": 0}

    async def always_miss(*a, **kw):
        counter["n"] += 1
        return f"req-{counter['n']}"  # unique per call — librarr_request_id is a unique column

    async def instantly_failed(rid, librarr_request_id):
        # Mirrors what the real _quick_resolve/_apply_librarr_status would
        # actually do on a fast miss — flips the candidate back to failed —
        # so this test exercises _MAX_SUBMISSIONS_PER_TICK specifically,
        # not _MAX_CONCURRENT_TORRENTS (which a no-op mock would trip
        # instead, since nothing would ever free up an in-flight slot).
        async with svc.async_session_factory() as session:
            candidate = (
                await session.execute(
                    select(AcquisitionCandidate).where(AcquisitionCandidate.request_id == rid)
                )
            ).scalar_one()
            candidate.status = AcquisitionStatus.failed
            candidate.resolved_at = datetime.now(UTC)
            await session.commit()
        return "failed"

    monkeypatch.setattr(svc, "_submit_to_librarr", always_miss)
    monkeypatch.setattr(svc, "_quick_resolve", instantly_failed)

    out = await svc.submit_tick()

    assert out["chained_attempts"] == svc._MAX_SUBMISSIONS_PER_TICK


async def test_submit_tick_respects_the_concurrency_cap(db_session, monkeypatch, _torrent_idle):
    for i in range(svc._MAX_CONCURRENT_TORRENTS):
        db_session.add(
            AcquisitionCandidate(
                request_id=f"in-flight-{i}", request_title=f"Book {i}",
                status=AcquisitionStatus.fetching, candidate_provider="torrent",
            )
        )
    await db_session.commit()

    async def fail_if_called(*a, **kw):
        raise AssertionError("must not submit past the concurrency cap")

    monkeypatch.setattr(svc, "_submit_to_librarr", fail_if_called)

    out = await svc.submit_tick()
    assert "already in flight" in out["skipped"]


async def test_submit_tick_respects_its_own_search_budget(db_session, monkeypatch, _torrent_idle):
    monkeypatch.setattr(
        acquisition_service, "_recent_search_times",
        {"torrent": [__import__("time").monotonic()] * svc._SEARCH_BUDGET_PER_HOUR},
    )

    async def fail_if_called(*a, **kw):
        raise AssertionError("budget spent — must not submit")

    monkeypatch.setattr(svc, "_submit_to_librarr", fail_if_called)

    out = await svc.submit_tick()
    assert out["skipped"] == "search budget spent this hour"


async def test_submit_tick_skips_a_book_already_fetching(db_session, monkeypatch, _torrent_idle):
    """Never-tried-first only applies to books with no row at all — a book
    already `fetching` (in flight via this same subsystem, or found by it on
    a prior tick) must not be resubmitted."""
    db_session.add(
        AcquisitionCandidate(
            request_id="wl-dep", request_title="Departure", request_author="A G Riddle",
            status=AcquisitionStatus.fetching, candidate_provider="torrent",
        )
    )
    await db_session.commit()

    async def fail_if_called(*a, **kw):
        raise AssertionError("book is already in flight — must not resubmit")

    monkeypatch.setattr(svc, "_submit_to_librarr", fail_if_called)

    out = await svc.submit_tick()
    assert out["skipped"] == "all caught up or cooling down"


async def test_submit_tick_leaves_row_untouched_when_librarr_unreachable(
    db_session, monkeypatch, _torrent_idle
):
    async def unreachable(*a, **kw):
        return None

    monkeypatch.setattr(svc, "_submit_to_librarr", unreachable)

    out = await svc.submit_tick()
    assert out == {"skipped": "librarr unavailable", "title": "Departure"}

    row = (
        await db_session.execute(
            select(AcquisitionCandidate).where(AcquisitionCandidate.request_id == "wl-dep")
        )
    ).scalar_one_or_none()
    assert row is None  # never-tried and still untouched — nothing was created


# --- poll_tick ---------------------------------------------------------


async def test_poll_tick_skipped_when_torrent_disabled(db_session, monkeypatch):
    class _Cfg:
        torrent_enabled = False

    monkeypatch.setattr(svc, "get_settings", lambda: _Cfg())
    out = await svc.poll_tick()
    assert out == {"skipped": "torrent disabled"}


async def test_poll_tick_no_op_with_nothing_in_flight(db_session, monkeypatch):
    class _Cfg:
        torrent_enabled = True

    monkeypatch.setattr(svc, "get_settings", lambda: _Cfg())
    out = await svc.poll_tick()
    assert out == {"polled": 0, "released": 0}


@respx.mock
async def test_poll_tick_updates_status_for_an_in_flight_request(db_session, monkeypatch):
    class _Cfg:
        torrent_enabled = True
        librarr_url = LIBRARR_URL
        librarr_api_key = ""

    monkeypatch.setattr(svc, "get_settings", lambda: _Cfg())
    db_session.add(LibrarrRequest(request_id="wl-dep", librarr_request_id="req-1"))
    await db_session.commit()

    respx.get(f"{LIBRARR_URL}/api/requests/req-1").mock(
        return_value=httpx.Response(200, json={"status": "downloading"})
    )

    out = await svc.poll_tick()
    assert out == {"polled": 1, "updated": 1, "failed": 0, "released": 0}

    row = (
        await db_session.execute(select(LibrarrRequest).where(LibrarrRequest.request_id == "wl-dep"))
    ).scalar_one()
    assert row.status == LibrarrRequestStatus.downloading
    assert row.resolved_at is None  # still in flight


@respx.mock
async def test_poll_tick_flips_the_candidate_back_to_failed(db_session, monkeypatch):
    class _Cfg:
        torrent_enabled = True
        librarr_url = LIBRARR_URL
        librarr_api_key = ""

    monkeypatch.setattr(svc, "get_settings", lambda: _Cfg())
    db_session.add(LibrarrRequest(request_id="wl-dep", librarr_request_id="req-1"))
    db_session.add(
        AcquisitionCandidate(
            request_id="wl-dep", request_title="Departure",
            status=AcquisitionStatus.fetching, candidate_provider="torrent",
        )
    )
    await db_session.commit()

    # attention_note confirmed live 2026-09-16 as the real failure-message
    # field name (e.g. "No search results found").
    respx.get(f"{LIBRARR_URL}/api/requests/req-1").mock(
        return_value=httpx.Response(200, json={"status": "failed", "attention_note": "No search results found"})
    )

    out = await svc.poll_tick()
    assert out == {"polled": 1, "updated": 1, "failed": 1, "released": 0}

    tracked = (
        await db_session.execute(select(LibrarrRequest).where(LibrarrRequest.request_id == "wl-dep"))
    ).scalar_one()
    assert tracked.status == LibrarrRequestStatus.failed
    assert tracked.resolved_at is not None

    candidate = (
        await db_session.execute(
            select(AcquisitionCandidate).where(AcquisitionCandidate.request_id == "wl-dep")
        )
    ).scalar_one()
    assert candidate.status == AcquisitionStatus.failed
    assert candidate.message == "No search results found"
    assert candidate.resolved_at is not None


@respx.mock
async def test_poll_tick_completed_does_not_touch_the_candidate(db_session, monkeypatch):
    """completed just means the file is in Librarr's EBOOK_DIR — the actual
    approve happens via local_scan_service once the handoff leg picks it up,
    not here."""
    class _Cfg:
        torrent_enabled = True
        librarr_url = LIBRARR_URL
        librarr_api_key = ""

    monkeypatch.setattr(svc, "get_settings", lambda: _Cfg())
    db_session.add(LibrarrRequest(request_id="wl-dep", librarr_request_id="req-1"))
    db_session.add(
        AcquisitionCandidate(
            request_id="wl-dep", request_title="Departure",
            status=AcquisitionStatus.fetching, candidate_provider="torrent",
        )
    )
    await db_session.commit()

    respx.get(f"{LIBRARR_URL}/api/requests/req-1").mock(
        return_value=httpx.Response(200, json={"status": "completed"})
    )

    await svc.poll_tick()

    candidate = (
        await db_session.execute(
            select(AcquisitionCandidate).where(AcquisitionCandidate.request_id == "wl-dep")
        )
    ).scalar_one()
    assert candidate.status == AcquisitionStatus.fetching  # unchanged


@respx.mock
async def test_poll_tick_ignores_an_unrecognized_status(db_session, monkeypatch):
    class _Cfg:
        torrent_enabled = True
        librarr_url = LIBRARR_URL
        librarr_api_key = ""

    monkeypatch.setattr(svc, "get_settings", lambda: _Cfg())
    db_session.add(LibrarrRequest(request_id="wl-dep", librarr_request_id="req-1"))
    await db_session.commit()

    respx.get(f"{LIBRARR_URL}/api/requests/req-1").mock(
        return_value=httpx.Response(200, json={"status": "some-new-status-we-dont-know"})
    )

    out = await svc.poll_tick()
    assert out == {"polled": 1, "updated": 0, "failed": 0, "released": 0}


async def test_poll_tick_releases_a_candidate_stuck_fetching_past_the_timeout(db_session, monkeypatch):
    """Regression guard for a real live finding 2026-09-16: Librarr reported
    a request "completed" for a torrent stalled at 0 seeders that will never
    deliver a file — with nothing left to poll (the tracked request already
    resolved), the candidate would otherwise stay `fetching` forever. Uses
    raw SQL to backdate updated_at, since AcquisitionCandidate.updated_at
    has onupdate=func.now() and would otherwise reset itself the moment the
    ORM flushes any other change to the row."""
    from sqlalchemy import text

    class _Cfg:
        torrent_enabled = True
        librarr_url = LIBRARR_URL
        librarr_api_key = ""

    monkeypatch.setattr(svc, "get_settings", lambda: _Cfg())

    # The tracked request already resolved — nothing left in `in_flight`.
    db_session.add(
        LibrarrRequest(
            request_id="wl-dep", librarr_request_id="req-1",
            status=LibrarrRequestStatus.completed,
        )
    )
    db_session.add(
        AcquisitionCandidate(
            request_id="wl-dep", request_title="Departure",
            status=AcquisitionStatus.fetching, candidate_provider="torrent",
        )
    )
    await db_session.commit()

    stale = datetime.now(UTC) - svc._FETCHING_STUCK_AFTER - timedelta(minutes=1)
    await db_session.execute(
        text("UPDATE acquisition_candidates SET updated_at = :ts WHERE request_id = 'wl-dep'"),
        {"ts": stale},
    )
    await db_session.commit()

    out = await svc.poll_tick()
    assert out == {"polled": 0, "released": 1}

    candidate = (
        await db_session.execute(
            select(AcquisitionCandidate).where(AcquisitionCandidate.request_id == "wl-dep")
        )
    ).scalar_one()
    assert candidate.status == AcquisitionStatus.failed
    assert "no usable file arrived in time" in candidate.message


async def test_poll_tick_leaves_a_recently_fetching_candidate_alone(db_session, monkeypatch):
    class _Cfg:
        torrent_enabled = True
        librarr_url = LIBRARR_URL
        librarr_api_key = ""

    monkeypatch.setattr(svc, "get_settings", lambda: _Cfg())
    db_session.add(
        LibrarrRequest(
            request_id="wl-dep", librarr_request_id="req-1",
            status=LibrarrRequestStatus.completed,
        )
    )
    db_session.add(
        AcquisitionCandidate(
            request_id="wl-dep", request_title="Departure",
            status=AcquisitionStatus.fetching, candidate_provider="torrent",
        )
    )
    await db_session.commit()

    out = await svc.poll_tick()
    assert out == {"polled": 0, "released": 0}

    candidate = (
        await db_session.execute(
            select(AcquisitionCandidate).where(AcquisitionCandidate.request_id == "wl-dep")
        )
    ).scalar_one()
    assert candidate.status == AcquisitionStatus.fetching  # left alone, well within the window


# --- local_scan_tick -----------------------------------------------------


async def test_local_scan_tick_skipped_when_torrent_disabled(db_session, monkeypatch):
    class _Cfg:
        torrent_enabled = False

    monkeypatch.setattr(svc, "get_settings", lambda: _Cfg())
    out = await svc.local_scan_tick()
    assert out == {"skipped": "torrent disabled"}


async def test_local_scan_tick_skipped_when_not_configured(db_session, monkeypatch, tmp_path):
    class _Cfg:
        torrent_enabled = True
        torrent_incoming_folder = str(tmp_path)

    monkeypatch.setattr(svc, "get_settings", lambda: _Cfg())

    import app.services.auth_service as auth_svc

    class _Auth:
        async def get_credentials(self, repo):
            return None

    monkeypatch.setattr(auth_svc, "get_auth_service", lambda: _Auth())

    out = await svc.local_scan_tick()
    assert out == {"skipped": "not configured", "junk_removed": 0}


async def test_local_scan_tick_runs_the_existing_handoff_pipeline(db_session, monkeypatch, tmp_path):
    class _Cfg:
        torrent_enabled = True
        torrent_incoming_folder = str(tmp_path)

    monkeypatch.setattr(svc, "get_settings", lambda: _Cfg())

    import app.services.auth_service as auth_svc
    import app.services.drive_service as ds
    import app.providers.drive.client as dc

    class _Auth:
        async def get_credentials(self, repo):
            return object()

    class _Folder:
        folder_id = "f"

    async def _folder(repo):
        return _Folder()

    monkeypatch.setattr(auth_svc, "get_auth_service", lambda: _Auth())
    monkeypatch.setattr(ds.DriveService, "get_inbox_folder_config", staticmethod(_folder))
    monkeypatch.setattr(ds.DriveService, "get_library_folder_config", staticmethod(_folder))
    monkeypatch.setattr(dc, "build_drive_service", lambda creds: None)
    monkeypatch.setattr(svc, "DriveProvider", lambda svc_obj: object())

    calls = {"scan": 0, "match": 0, "copy": 0}

    async def fake_scan(session, root):
        calls["scan"] += 1
        return [LocalFile(id=1, path="/x/y.epub", filename="y.epub", size_bytes=100, status=LocalFileStatus.pending)]

    async def fake_match(session, rows, provider, inbox_id, library_id, *, auto_resolve=False):
        calls["match"] += 1

    async def fake_copy(session, file_ids, provider, inbox_id):
        calls["copy"] += 1
        return {"copied": 1, "failed": 0}

    monkeypatch.setattr(local_scan_service, "scan_local_folder", fake_scan)
    monkeypatch.setattr(local_scan_service, "match_against_wishlist", fake_match)
    monkeypatch.setattr(local_scan_service, "copy_to_drive", fake_copy)

    out = await svc.local_scan_tick()

    assert calls == {"scan": 1, "match": 1, "copy": 1}
    assert out == {"pending": 1, "copied": 1, "failed": 0, "junk_removed": 0}


# --- _cleanup_incoming_junk -----------------------------------------------
# torrent_incoming_folder is owned exclusively by this subsystem — unlike
# the shared torrents_watch_folder (James's own pre-existing manual
# audiobook collection, which local_scan_service.scan_local_folder already
# correctly leaves untouched forever by design), so cleanup here is safe.


async def test_cleanup_incoming_junk_removes_an_old_non_ebook_file(tmp_path):
    junk = tmp_path / "Some Audiobook.mp3"
    junk.write_bytes(b"x" * 100)
    old = time.time() - svc._JUNK_FILE_GRACE_PERIOD.total_seconds() - 60
    os.utime(junk, (old, old))

    removed = await svc._cleanup_incoming_junk(str(tmp_path))

    assert removed == 1
    assert not junk.exists()


async def test_cleanup_incoming_junk_leaves_a_recent_file_alone(tmp_path):
    """Grace period protects a file Librarr might still be mid-move into
    the folder from being raced and deleted out from under it."""
    junk = tmp_path / "Some Audiobook.mp3"
    junk.write_bytes(b"x" * 100)

    removed = await svc._cleanup_incoming_junk(str(tmp_path))

    assert removed == 0
    assert junk.exists()


async def test_cleanup_incoming_junk_never_touches_ebook_files(tmp_path):
    book = tmp_path / "A Real Book.epub"
    book.write_bytes(b"x" * 100)
    old = time.time() - svc._JUNK_FILE_GRACE_PERIOD.total_seconds() - 60
    os.utime(book, (old, old))

    removed = await svc._cleanup_incoming_junk(str(tmp_path))

    assert removed == 0
    assert book.exists()


async def test_cleanup_incoming_junk_handles_a_missing_folder(tmp_path):
    removed = await svc._cleanup_incoming_junk(str(tmp_path / "does-not-exist"))
    assert removed == 0
