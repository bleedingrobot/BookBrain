"""The torrent acquisition subsystem — a third, structurally different way
to fill the wishlist backlog, alongside OpenBooks (IRC) and Libgen (direct
HTTP scrape).

Unlike those two, this is **not** an `AcquisitionProvider` and is never
registered in `acquisition_providers.default_acquisition_providers()`: every
provider's `download()` is expected to return a completed local file near-
synchronously (`acquire_service.acquire_to_inbox` awaits it and immediately
uploads the result), and a torrent can take minutes to hours. Forcing that
shape here would mean either blocking a scheduler tick for hours or lying
about completion.

Instead this talks to Librarr (https://github.com/jcraney143/librarr), run
headless (no library-import step — it just drops completed downloads into a
plain folder) alongside its own Prowlarr + qBittorrent. BookBrain never talks
to Prowlarr or qBittorrent directly; Librarr owns indexer search, scoring,
and download submission internally, exposing a simple request lifecycle API
(`POST /api/requests` -> poll `GET /api/requests/{id}` through
pending/searching/downloading/completed/failed).

Two independent ticks:

- `submit_tick()` — reuses `acquisition_service`'s shared never-tried-first /
  oldest-due-retry target selection (`_pick_next_target`) and the shared
  `_autoget_lock`, so this cycle divides the backlog with OpenBooks/Libgen's
  cycles instead of duplicating their picks. On a pick, POSTs to Librarr and
  flips the matching `AcquisitionCandidate` to `status=fetching` — excluded
  from every cycle's due-retry consideration until it resolves one way or
  the other. Gated on its own `TORRENT_AUTOGET_ENABLED` toggle (separate
  from OpenBooks/Libgen's shared switch: a torrent commits real bandwidth
  and disk per book, unlike a quick search) plus a concurrency cap, since
  full automation with no per-book approval removes the natural throttle a
  human clicking "Get this" provides elsewhere.
- `poll_tick()` — follows up on already-submitted requests regardless of the
  auto-get toggle (only gated on `settings.torrent_enabled`), so turning
  auto-get off doesn't strand an in-flight torrent with nothing polling it
  to completion. On `completed`, no direct action is needed here: the file
  already landed in Librarr's `EBOOK_DIR`, which is `local_scan_tick()`'s
  own dedicated `torrent_incoming_folder` — a deliberately *separate* folder
  from `torrents_watch_folder` (James's existing, pre-populated manual
  workflow, fed by a torrent client on another machine — left untouched,
  still only scanned by the nightly job as before this subsystem existed).
- `local_scan_tick()` — runs the existing torrents-watch-folder machinery
  (`local_scan_service.scan_local_folder` + `match_against_wishlist`) against
  `torrent_incoming_folder` specifically, on a short interval instead of only
  nightly, so a file Librarr just organized reaches the Drive inbox within a
  couple of minutes and does the actual approve.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.data.db import async_session_factory
from app.data.models import (
    AcquisitionCandidate,
    AcquisitionStatus,
    LibrarrRequest,
    LibrarrRequestStatus,
    LocalFileStatus,
)
from app.providers.drive.provider import DriveProvider
from app.services import acquisition_service, local_scan_service
from app.services.drive_service import DriveService

logger = logging.getLogger(__name__)

_BUDGET_KEY = "torrent"
# Prowlarr/qBittorrent already rate-limit themselves; Librarr sits in front
# of both, so this is mostly an outer safety bound like Libgen's own budget,
# not a hard external constraint. Deliberately looser than OpenBooks' 8/hour
# (a shared, rate-limited IRC bot) but tighter than Libgen's 40/hour (a
# cheap HTTP search) since each hit here is a real download commitment.
_SEARCH_BUDGET_PER_HOUR = 20
# Full automation with no per-book human approval removes the natural
# throttle a manual "Get this" click provides elsewhere — this cap is the
# replacement brake, not optional polish.
_MAX_CONCURRENT_TORRENTS = 3
_LIBRARR_TIMEOUT = 15.0
# Live-confirmed 2026-09-16: Librarr can report a request "completed" for a
# torrent that's actually stalled at 0 seeders and will never deliver a
# file, and separately can match the wrong media type (an audiobook for an
# ebook request) that our own format check would correctly reject once it
# arrives — either way, nothing upstream ever un-sticks that book. Without
# this, a `fetching` row is permanently excluded from every cycle's
# due-retry consideration (by design, so nothing double-attempts a book
# genuinely in flight) with no path back if Librarr's own "done" signal
# turns out not to mean a usable file ever lands. Generous on purpose — long
# enough that a slow but genuinely-progressing torrent isn't punished for
# taking its time.
_FETCHING_STUCK_AFTER = timedelta(hours=4)
# Grace window before a non-ebook file sitting in torrent_incoming_folder is
# considered actual junk rather than something Librarr might still be
# mid-move into the folder.
_JUNK_FILE_GRACE_PERIOD = timedelta(minutes=30)


def _librarr_client() -> httpx.AsyncClient:
    settings = get_settings()
    headers = {"X-Api-Key": settings.librarr_api_key} if settings.librarr_api_key else {}
    return httpx.AsyncClient(base_url=settings.librarr_url, headers=headers, timeout=_LIBRARR_TIMEOUT)


def _unwrap(data: dict) -> dict:
    """Every Librarr request-lifecycle response nests the actual record
    under "request" (confirmed live 2026-09-16: {"request": {...},
    "success": true}) — falls back to the raw dict if that key's ever
    missing rather than betting the whole parse on one exact shape."""
    inner = data.get("request")
    return inner if isinstance(inner, dict) else data


async def _submit_to_librarr(client: httpx.AsyncClient, title: str, author: str | None) -> str | None:
    """POST a request to Librarr, then immediately approve it — confirmed
    live 2026-09-16: Librarr requires an explicit PUT .../approve before it
    starts searching at all ("users request books, admins approve" is its
    own design, with no auto-approve setting), so without this every
    submission would sit in `pending` forever. Returns the request id once
    approved, or None on any failure — degrades quietly, same philosophy as
    every other acquisition source's search()."""
    try:
        response = await client.post("/api/requests", json={"title": title, "author": author or ""})
        response.raise_for_status()
        data = _unwrap(response.json())
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("torrent: submitting %r to Librarr failed: %s", title, exc)
        return None
    request_id = data.get("id")
    if not request_id:
        logger.warning("torrent: Librarr accepted %r but returned no request id: %s", title, data)
        return None
    request_id = str(request_id)

    if not await _approve_librarr_request(client, request_id):
        # Left approved=False in Librarr's own DB — a human can approve it
        # manually from Librarr's UI if this keeps happening, but we treat
        # this submission as failed and let the normal backlog retry logic
        # pick the book again later (which will submit a fresh request,
        # leaving this one orphaned in Librarr rather than resuming it —
        # an acceptable v1 trade-off over the complexity of resuming a
        # half-submitted request).
        return None
    return request_id


async def _approve_librarr_request(client: httpx.AsyncClient, librarr_request_id: str) -> bool:
    try:
        response = await client.put(f"/api/requests/{librarr_request_id}/approve")
        response.raise_for_status()
        return True
    except httpx.HTTPError as exc:
        logger.warning("torrent: approving Librarr request %s failed: %s", librarr_request_id, exc)
        return False


async def _poll_librarr(client: httpx.AsyncClient, librarr_request_id: str) -> dict | None:
    try:
        response = await client.get(f"/api/requests/{librarr_request_id}")
        response.raise_for_status()
        return _unwrap(response.json())
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("torrent: polling Librarr request %s failed: %s", librarr_request_id, exc)
        return None


async def _torrent_autoget_enabled() -> bool:
    from app.core.settings_keys import TORRENT_AUTOGET_ENABLED
    from app.data.repositories.settings_repository import SettingsRepository

    async with async_session_factory() as session:
        return (await SettingsRepository(session).get(TORRENT_AUTOGET_ENABLED)) == "true"


async def submit_tick(trigger: str = "scheduler") -> dict:
    """One iteration of the torrent-submission cycle. Never raises."""
    from app.providers.drive.client import build_drive_service
    from app.services.auth_service import get_auth_service
    from app.services.scan_service import get_scan_service
    from app.data.repositories.settings_repository import SettingsRepository

    settings = get_settings()
    if not settings.torrent_enabled:
        return {"skipped": "torrent disabled"}
    if not await _torrent_autoget_enabled():
        return {"skipped": "auto-get off"}
    if acquisition_service.has_active_refresh_job() or get_scan_service().has_running_job():
        return {"skipped": "busy"}

    async with async_session_factory() as session:
        repo = SettingsRepository(session)
        try:
            creds = await get_auth_service().get_credentials(repo)
        except Exception:  # noqa: BLE001 — token refresh failure, etc.
            return {"skipped": "no drive credentials"}
        library = await DriveService.get_library_folder_config(repo)
    if creds is None or library is None:
        return {"skipped": "not configured"}

    provider = DriveProvider(build_drive_service(creds))
    targets = await acquisition_service.gather_acquisition_targets(provider, library.folder_id)
    if not targets:
        return {"skipped": "no targets"}

    async with acquisition_service._autoget_lock:
        async with async_session_factory() as session:
            await acquisition_service._dedupe_candidates(session)
            rows = list((await session.execute(select(AcquisitionCandidate))).scalars())

        now = datetime.now(UTC)
        picked = acquisition_service._pick_next_target(targets, rows, now)
        if picked is None:
            return {"skipped": "all caught up or cooling down", "targets": len(targets)}
        item, existing = picked
        rid, title, author = item["request_id"], item["title"], item.get("author")

        in_flight = sum(1 for r in rows if r.status == AcquisitionStatus.fetching)
        if in_flight >= _MAX_CONCURRENT_TORRENTS:
            return {"skipped": f"{in_flight} torrents already in flight", "title": title}

        if not acquisition_service._search_budget_left(_BUDGET_KEY, _SEARCH_BUDGET_PER_HOUR):
            return {"skipped": "search budget spent this hour", "title": title}

        async with _librarr_client() as client:
            librarr_request_id = await _submit_to_librarr(client, title, author)
        acquisition_service._note_search(_BUDGET_KEY)

        if librarr_request_id is None:
            # Leave the candidate row untouched — the normal backoff/dedupe
            # machinery naturally retries this book on a future tick, no
            # need to mark it failed just because Librarr itself was
            # unreachable this one time.
            return {"skipped": "librarr unavailable", "title": title}

        async with async_session_factory() as session:
            # _submit_to_librarr only returns non-None once the approve call
            # succeeded, so this row starts life already past `pending`.
            session.add(
                LibrarrRequest(
                    request_id=rid, librarr_request_id=librarr_request_id,
                    status=LibrarrRequestStatus.approved,
                )
            )
            if existing is not None:
                row = await session.get(AcquisitionCandidate, existing.id)
                if row is not None and row.request_id != rid:
                    # Re-key: this book was found under a different id (e.g.
                    # want-to-read/list vs the wishlist id gather_acquisition_targets
                    # prefers) — same dance _run_autoget_cycle does.
                    row.request_id = rid
                    row.source = item.get("source") or "wishlist"
            else:
                row = None
            if row is None:
                row = AcquisitionCandidate(
                    request_id=rid, request_title=title, request_author=author,
                    source=item.get("source") or "wishlist",
                )
                session.add(row)
            row.status = AcquisitionStatus.fetching
            row.candidate_provider = "torrent"
            row.candidate_title = title
            row.candidate_author = author
            row.request_title = title
            row.request_author = author
            row.message = None
            await session.commit()

    logger.info("torrent: submitted %r to Librarr (request %s)", title, librarr_request_id)
    return {"submitted": title, "librarr_request_id": librarr_request_id}


async def poll_tick(trigger: str = "scheduler") -> dict:
    """Follows up on already-submitted Librarr requests. Runs regardless of
    the auto-get toggle so an in-flight torrent never gets stranded
    unpolled. Never raises."""
    settings = get_settings()
    if not settings.torrent_enabled:
        return {"skipped": "torrent disabled"}

    async with async_session_factory() as session:
        in_flight = list(
            (
                await session.execute(
                    select(LibrarrRequest).where(
                        LibrarrRequest.status.notin_(
                            (LibrarrRequestStatus.completed, LibrarrRequestStatus.failed)
                        )
                    )
                )
            ).scalars()
        )
    if not in_flight:
        # Must still run — this is precisely the case where every tracked
        # request has already resolved (e.g. Librarr said "completed") but
        # the matching AcquisitionCandidate never got picked up by
        # local_scan_tick and is stuck `fetching` with nothing left to poll.
        return {"polled": 0, "released": await _release_stale_fetching()}

    updated = 0
    newly_failed = 0
    async with _librarr_client() as client:
        for tracked in in_flight:
            data = await _poll_librarr(client, tracked.librarr_request_id)
            if data is None:
                continue
            raw_status = str(data.get("status") or "").lower()
            try:
                new_status = LibrarrRequestStatus(raw_status)
            except ValueError:
                continue  # unrecognized status string — leave as-is, retry next tick

            if new_status == LibrarrRequestStatus.pending:
                # Shouldn't normally happen (submit_tick only creates a row
                # after approving), but if it does — a transient failure on
                # the original approve call, or Librarr resetting it for its
                # own reasons — retry approving rather than leaving it
                # stuck forever with nothing progressing it.
                await _approve_librarr_request(client, tracked.librarr_request_id)
                continue

            failure_message: str | None = None
            if new_status == LibrarrRequestStatus.failed:
                # "attention_note" confirmed live 2026-09-16 (e.g. "No search
                # results found") — "error"/"message" kept as fallbacks in
                # case a different failure path uses a different field.
                failure_message = str(
                    data.get("attention_note") or data.get("error") or data.get("message")
                    or "Librarr reported failure"
                )

            async with async_session_factory() as session:
                row = await session.get(LibrarrRequest, tracked.id)
                if row is None:
                    continue
                row.status = new_status
                if new_status in (LibrarrRequestStatus.completed, LibrarrRequestStatus.failed):
                    row.resolved_at = datetime.now(UTC)
                if failure_message is not None:
                    row.message = failure_message
                await session.commit()
            updated += 1

            if new_status == LibrarrRequestStatus.failed:
                async with async_session_factory() as session:
                    candidate = (
                        await session.execute(
                            select(AcquisitionCandidate).where(
                                AcquisitionCandidate.request_id == tracked.request_id,
                                AcquisitionCandidate.status == AcquisitionStatus.fetching,
                            )
                        )
                    ).scalar_one_or_none()
                    if candidate is not None:
                        candidate.status = AcquisitionStatus.failed
                        candidate.message = failure_message
                        candidate.resolved_at = datetime.now(UTC)
                        await session.commit()
                newly_failed += 1
                logger.warning(
                    "torrent: Librarr request %s for %r failed: %s",
                    tracked.librarr_request_id, tracked.request_id, failure_message,
                )

    released = await _release_stale_fetching()
    return {"polled": len(in_flight), "updated": updated, "failed": newly_failed, "released": released}


async def _release_stale_fetching() -> int:
    """Free any book that's been `fetching` for too long, regardless of what
    its LibrarrRequest row says — Librarr reporting `completed` is not
    proof a usable file will ever actually reach the handoff folder (see
    `_FETCHING_STUCK_AFTER`'s comment). Returns the number released.

    Filters in Python, not SQL — `_MAX_CONCURRENT_TORRENTS` caps how many
    `fetching` rows can exist at once (a handful at most), so fetching them
    all and comparing here avoids relying on SQLite comparing a timezone-
    aware cutoff against its naturally-naive stored datetimes correctly
    (the same naive/aware mismatch `acquisition_service._aware` exists to
    paper over for every other backoff calculation in this codebase)."""
    now = datetime.now(UTC)
    async with async_session_factory() as session:
        candidates = list(
            (
                await session.execute(
                    select(AcquisitionCandidate).where(AcquisitionCandidate.status == AcquisitionStatus.fetching)
                )
            ).scalars()
        )
        stuck = [
            row for row in candidates
            if (updated := acquisition_service._aware(row.updated_at)) is not None
            and now - updated >= _FETCHING_STUCK_AFTER
        ]
        for row in stuck:
            logger.warning(
                "torrent: %r stuck fetching for over %s with no file ever arriving — releasing",
                row.request_title, _FETCHING_STUCK_AFTER,
            )
            row.status = AcquisitionStatus.failed
            row.message = "torrent subsystem: no usable file arrived in time"
            row.resolved_at = datetime.now(UTC)
        if stuck:
            await session.commit()
    return len(stuck)


async def local_scan_tick(trigger: str = "scheduler") -> dict:
    """Runs the existing torrents-watch-folder handoff machinery (scan ->
    wishlist fuzzy-match -> upload) against `settings.torrent_incoming_folder`
    specifically — NOT `torrents_watch_folder` (James's existing, separate,
    pre-populated manual workflow, untouched by this subsystem; still scanned
    only by the nightly job's `_pull_local_folder` as before this subsystem
    existed) — on a short interval instead of only nightly, so a file Librarr
    just finished organizing reaches the Drive inbox within a couple of
    minutes, not up to a day later. Mirrors `_pull_local_folder`'s own
    creds/inbox/library resolution. Gated only on `settings.torrent_enabled`
    (same reasoning as poll_tick — this leg matters for anything already
    sitting in the folder regardless of the auto-get toggle). Never raises."""
    from app.data.repositories.settings_repository import SettingsRepository
    from app.providers.drive.client import build_drive_service
    from app.services.auth_service import get_auth_service

    settings = get_settings()
    if not settings.torrent_enabled:
        return {"skipped": "torrent disabled"}

    # Purely local filesystem cleanup — no Drive dependency, so it runs
    # regardless of whether credentials/inbox/library are configured yet,
    # and regardless of whether any ebook-shaped file was found this pass
    # (the two are unrelated: a non-ebook file is never a `pending` row at
    # all, so it'd never be reached if this were nested under that check).
    removed = await _cleanup_incoming_junk(settings.torrent_incoming_folder)

    async with async_session_factory() as session:
        repo = SettingsRepository(session)
        try:
            creds = await get_auth_service().get_credentials(repo)
        except Exception:  # noqa: BLE001
            return {"skipped": "no drive credentials", "junk_removed": removed}
        inbox = await DriveService.get_inbox_folder_config(repo)
        library = await DriveService.get_library_folder_config(repo)
    if creds is None or inbox is None or library is None:
        return {"skipped": "not configured", "junk_removed": removed}

    try:
        async with async_session_factory() as session:
            pending = await local_scan_service.scan_local_folder(session, settings.torrent_incoming_folder)
            if not pending:
                return {"pending": 0, "junk_removed": removed}
            provider = DriveProvider(build_drive_service(creds))
            await local_scan_service.match_against_wishlist(
                session, pending, provider, inbox.folder_id, library.folder_id
            )
            result = await local_scan_service.copy_to_drive(
                session,
                [row.id for row in pending if row.status == LocalFileStatus.pending],
                provider, inbox.folder_id,
            )
        return {"pending": len(pending), "junk_removed": removed, **result}
    except Exception:  # noqa: BLE001 — a bad tick must never kill the schedule
        logger.exception("torrent: local-scan handoff tick failed")
        return {"error": "local-scan tick failed", "junk_removed": removed}


async def _cleanup_incoming_junk(folder: str) -> int:
    """`torrent_incoming_folder` is owned exclusively by this subsystem —
    unlike the shared `torrents_watch_folder` (James's own pre-existing
    manual audiobook workflow, never touched here), nothing legitimate is
    ever supposed to sit in this one except ebook-shaped files awaiting
    pickup. Librarr's book_type routing isn't foolproof (confirmed live
    2026-09-16: it matched an audiobook for an ebook request), and
    `local_scan_service.scan_local_folder` silently and permanently ignores
    anything that isn't ebook-shaped — by design for the shared folder, but
    here it just means a wrong-format download accumulates on disk forever
    with nothing else ever reclaiming the space. Deletes any non-ebook file
    older than `_JUNK_FILE_GRACE_PERIOD` (skips anything newer, in case
    Librarr is still mid-move into the folder). Returns the count removed;
    never raises."""
    root = Path(folder)
    if not root.is_dir():
        return 0
    cutoff = time.time() - _JUNK_FILE_GRACE_PERIOD.total_seconds()
    removed = 0
    for path in root.rglob("*"):
        if not path.is_file() or local_scan_service._is_watchable(path.name):
            continue
        try:
            if path.stat().st_mtime > cutoff:
                continue
            path.unlink()
        except OSError:
            logger.exception("torrent: couldn't remove junk file %s", path)
            continue
        removed += 1
        logger.info("torrent: removed non-ebook file from incoming folder: %s", path.name)
    return removed
