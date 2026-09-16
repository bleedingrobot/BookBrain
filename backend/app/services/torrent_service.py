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
plain folder) alongside its own Prowlarr + qBittorrent. BookBrain talks to
Prowlarr only through Librarr; Librarr owns indexer search, scoring, and
download submission internally, exposing a simple request lifecycle API
(`POST /api/requests` -> poll `GET /api/requests/{id}` through
pending/searching/downloading/completed/failed).

One deliberate, narrow exception: `_release_stale_fetching`'s qBittorrent
delete call (2026-09-17). Traced live why autoget had stalled for hours: a
dead 0-seeder torrent was squatting one of qBittorrent's `max_active_downloads`
slots (3 here) for 7-11 hours straight, so nothing behind it in the queue
ever got a turn — three at once, in fact, filling every slot. Giving up on a
book in *our* bookkeeping (`AcquisitionCandidate.status = failed`) was never
enough to fix that, because reading jcraney143/librarr's own source showed
it has no code path that removes an abandoned request's underlying torrent:
`PUT /api/requests/{id}/cancel` only flips its DB row, and the only call
anywhere to its `TorrentClient.DeleteTorrent` is on a *successful* import
(`internal/download/watcher.go`). So on this one path only, BookBrain also
calls qBittorrent's own API directly to actually free the slot — everything
else still goes through Librarr as before.

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

import asyncio
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
from app.services.text_match import normalize_title

logger = logging.getLogger(__name__)

_BUDGET_KEY = "torrent"
# Prowlarr/qBittorrent already rate-limit themselves; Librarr sits in front
# of both, so this is mostly an outer safety bound like Libgen's own budget,
# not a hard external constraint.
# James's ask 2026-09-16: "no clogging, push through" — 20/hour was the
# actual bottleneck behind a 45-minute silent stall once the earlier bugs
# stopped suppressing real throughput (submit_tick can already chain up to
# _MAX_SUBMISSIONS_PER_TICK per 5-minute tick — 60/hour at full chaining —
# so 20 was strictly tighter than the system's own natural pace, not a
# deliberate ceiling above it). Raised to sit at that natural pace instead
# of below it.
_SEARCH_BUDGET_PER_HOUR = 60
# Full automation with no per-book human approval removes the natural
# throttle a manual "Get this" click provides elsewhere — this cap is the
# replacement brake, not optional polish. James's ask 2026-09-16: raised
# from 3 — these are small epub files, not big transfers, and a dead
# torrent no longer squats a slot for hours (_STALLED_ZERO_PROGRESS_AFTER
# releases it in 5 minutes), so more concurrent slots no longer means more
# hours of potential dead weight the way it used to. James's ask
# 2026-09-17 ("keep going as much as possible"): raised again to 15,
# matching qBittorrent's own max_active_downloads (also raised from 3 to 15
# the same day) — this cap was quietly the real ceiling on torrent
# throughput even after that: BookBrain never fed qBittorrent more than 6
# concurrent submissions no matter how many download slots it had free.
_MAX_CONCURRENT_TORRENTS = 15
_LIBRARR_TIMEOUT = 15.0
# James's ask 2026-09-16: don't wait out the rest of the 5-min submit
# interval when a book fails fast — try the next one immediately. Bounded
# on both axes: _QUICK_POLL_MAX_WAIT_SECONDS caps how long one book is worth
# waiting on before assuming it's genuinely still searching/downloading (not
# a fast miss), and _MAX_SUBMISSIONS_PER_TICK caps the whole tick's length
# so a bad patch of the backlog (several fast misses in a row) can't turn
# one tick into an unbounded loop. James's ask 2026-09-16: raised from 5 —
# a "no search results" miss resolves in a few seconds (confirmed live:
# ~5s each), so even the full 15 is nowhere near turning one 5-minute tick
# into an unbounded loop, and it lets one tick burn through a bad patch of
# the backlog instead of trickling one book every 5 minutes regardless of
# how fast each one actually resolves.
_QUICK_POLL_INTERVAL_SECONDS = 5
_QUICK_POLL_MAX_WAIT_SECONDS = 45
_MAX_SUBMISSIONS_PER_TICK = 15
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
# A much faster release for the specific, common case that doesn't need
# 4 hours to diagnose: a torrent showing literally 0 B/s throughput (no
# seeders willing to serve it — confirmed live 2026-09-16: "Witch World"
# sat at 0 B/s / 0 seeds / 0 peers for over an hour and was never going to
# recover). James's ask 2026-09-16: a dead torrent shouldn't squat on one
# of only _MAX_CONCURRENT_TORRENTS slots for hours — "smash through what
# it can and leave the rest for the other acquisition channels" — so once
# a book has had a fair chance to find peers, a confirmed-zero-throughput
# torrent is released early instead of waiting out the full generous
# window meant for something merely slow (which this check never touches,
# since it only fires when Librarr's own /api/downloads reports 0 B/s).
# James's ask 2026-09-16: 5 minutes, not 15 — these are small epub files
# (a few hundred KB to a few MB), not season-pack-sized downloads; a
# torrent with real seeders starts moving data within seconds of adding,
# same as "Hawkmoon" confirmed live (2 seeders, already progressing 23s
# after submission) — 5 minutes of literal 0 B/s is already generous.
_STALLED_ZERO_PROGRESS_AFTER = timedelta(minutes=5)
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


async def _fetch_zero_speed_torrents(client: httpx.AsyncClient) -> dict[str, str | None]:
    """Librarr's own titles (not ours — its search-result title, e.g. "Witch
    World (Witch World, n. 1) by Andre Norton EPUB"), mapped to torrent info
    hash (or None if this particular /api/downloads response didn't include
    one), for every torrent-type download currently reported at 0 B/s. This
    is the only place per-torrent throughput (or a hash at all) is exposed —
    a per-request GET /api/requests/{id} only ever reports the coarse
    pending/searching/downloading/completed/failed status (see _poll_librarr
    / the module docstring), with no way to tell a healthy "downloading"
    from a dead one stalled at 0 seeders. Degrades quietly to "nothing
    confirmed stalled" on any error, same philosophy as _poll_librarr. The
    hash (when present) is what makes `_qbittorrent_delete_torrent` below
    possible — but its absence shouldn't block the release itself, only the
    qBittorrent cleanup step, so a missing hash maps to None rather than
    dropping the title."""
    try:
        response = await client.get("/api/downloads")
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("torrent: fetching Librarr downloads failed: %s", exc)
        return {}
    out: dict[str, str | None] = {}
    for d in data.get("downloads") or []:
        if d.get("source") != "torrent":
            continue
        title = d.get("title")
        if isinstance(title, str) and title and str(d.get("speed") or "").strip() in ("0 B/s", ""):
            hash_ = d.get("hash")
            out[title] = hash_ if isinstance(hash_, str) and hash_ else None
    return out


def _matching_zero_speed(needle: str | None, by_title: dict[str, str | None]) -> tuple[bool, str | None]:
    """(matched, hash-if-known) — matched can be True with hash None (a
    zero-speed torrent Librarr didn't report a hash for), which still
    justifies releasing the candidate but not deleting anything downstream."""
    normalized = normalize_title(needle)
    if not normalized:
        return False, None
    for title, hash_ in by_title.items():
        if normalized in normalize_title(title):
            return True, hash_
    return False, None


async def _qbittorrent_delete_torrent(hash_: str) -> bool:
    """Direct qBittorrent call — see the module docstring for why this one
    path reaches around Librarr instead of going through it: Librarr itself
    has no code path that frees a dead torrent's slot for an abandoned
    request, so nothing else will ever do this. Best-effort: any failure
    (qBittorrent down, wrong creds, hash already gone) just means the slot
    stays stuck a bit longer, not a crash — same degrade-quietly philosophy
    as every other Librarr/qBittorrent call in this module."""
    settings = get_settings()
    if not settings.qbittorrent_password:
        return False
    try:
        async with httpx.AsyncClient(base_url=settings.qbittorrent_url, timeout=10.0) as client:
            login = await client.post(
                "/api/v2/auth/login",
                data={"username": settings.qbittorrent_username, "password": settings.qbittorrent_password},
            )
            login.raise_for_status()
            # A successful login's actual response body isn't a reliable
            # signal across versions — confirmed live 2026-09-17: this
            # instance answers 204 No Content with an empty body, not the
            # classic 200 + "Ok." the WebAPI docs describe. The cookie jar
            # getting a QBT_SID_* cookie at all is what actually means
            # "authenticated" everywhere; httpx.AsyncClient keeps it and
            # resends it automatically on the delete call below.
            if not any(name.startswith("QBT_SID") for name in client.cookies):
                logger.warning("torrent: qBittorrent login didn't set a session cookie (status %d)", login.status_code)
                return False
            resp = await client.post("/api/v2/torrents/delete", data={"hashes": hash_, "deleteFiles": "true"})
            resp.raise_for_status()
            return True
    except httpx.HTTPError as exc:
        logger.warning("torrent: qBittorrent delete for hash %s failed: %s", hash_, exc)
        return False


async def _torrent_autoget_enabled() -> bool:
    from app.core.settings_keys import TORRENT_AUTOGET_ENABLED
    from app.data.repositories.settings_repository import SettingsRepository

    async with async_session_factory() as session:
        return (await SettingsRepository(session).get(TORRENT_AUTOGET_ENABLED)) == "true"


async def submit_tick(trigger: str = "scheduler") -> dict:
    """One iteration of the torrent-submission cycle — chains through
    multiple books in a single call when each resolves as a fast "no
    results" miss, instead of submitting exactly one and waiting out the
    rest of the scheduled interval regardless of how quickly it failed.
    Stops chaining the moment a submission is either still genuinely in
    flight (respecting it — that's real download progress, not something to
    rush past) or any non-per-book condition (cap/budget/nothing left to
    try) is hit. Never raises."""
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

    attempts: list[dict] = []
    for _ in range(_MAX_SUBMISSIONS_PER_TICK):
        outcome = await _submit_one(targets)
        attempts.append(outcome)
        if "submitted" not in outcome or outcome.get("quick_result") != "failed":
            # Either nothing was actually submitted this pass (a cap/budget/
            # nothing-due skip — not a per-book thing to chain past), or it
            # resolved as `completed`/is still genuinely in flight — either
            # way, stop here rather than rushing past real progress.
            break

    if len(attempts) == 1:
        return attempts[0]
    logger.info(
        "torrent: chained through %d quick misses this tick (%s)",
        len(attempts), ", ".join(a.get("submitted", a.get("skipped", "?")) for a in attempts),
    )
    return {"chained_attempts": len(attempts), "attempts": attempts}


async def _submit_one(targets: list[dict]) -> dict:
    """Picks and submits exactly one book, mirroring the original single-
    attempt submit_tick behaviour, then briefly polls that one submission
    (outside the shared lock — nothing left here needs serializing against
    the other cycles once the row is written) to see if it resolves fast.
    `quick_result` in the returned dict is "failed" if it did (letting
    submit_tick's caller chain to the next book immediately instead of
    waiting for poll_tick's own schedule to notice), "completed" or
    "still_in_flight" otherwise."""
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

    # Outside the lock — a book's own search resolving here doesn't need
    # serializing against the other cycles' picks, only the write above did.
    quick_result = await _quick_resolve(rid, librarr_request_id)
    return {"submitted": title, "librarr_request_id": librarr_request_id, "quick_result": quick_result}


async def _quick_resolve(rid: str, librarr_request_id: str) -> str:
    """Polls a just-submitted request for up to `_QUICK_POLL_MAX_WAIT_SECONDS`,
    applying the same status handling `poll_tick` does (via
    `_apply_librarr_status`) — so a fast "no results" miss is caught and
    released right here instead of waiting on poll_tick's own schedule (up
    to 60s) to notice, letting submit_tick immediately chain to the next
    book. Returns "failed", "completed", or "still_in_flight" (a genuinely
    progressing download, left for poll_tick to keep tracking normally)."""
    deadline = time.monotonic() + _QUICK_POLL_MAX_WAIT_SECONDS
    async with _librarr_client() as client:
        while time.monotonic() < deadline:
            await asyncio.sleep(_QUICK_POLL_INTERVAL_SECONDS)
            data = await _poll_librarr(client, librarr_request_id)
            if data is None:
                continue
            resolved = await _apply_librarr_status(client, rid, librarr_request_id, data)
            # _apply_librarr_status also returns non-None for `searching`/
            # `downloading` (recognized, but not settled) — only a terminal
            # status is actually "resolved" for our purposes here; anything
            # else, keep polling until the deadline.
            if resolved in (LibrarrRequestStatus.completed, LibrarrRequestStatus.failed):
                return resolved.value
    return "still_in_flight"


async def _apply_librarr_status(
    client: httpx.AsyncClient, request_id: str, librarr_request_id: str, data: dict
) -> LibrarrRequestStatus | None:
    """Parses one Librarr poll response and applies it: retries the approve
    call if it's somehow still `pending` (returns None — nothing settled),
    flips the matching AcquisitionCandidate back to failed on `failed`, and
    just records the tracking row's status otherwise (`searching`/
    `downloading`/`completed` — `completed` is deliberately not turned into
    an approve here; that's local_scan_tick's job once a real file shows
    up). Shared by `poll_tick` and `_quick_resolve` (submit_tick's fast-path
    check) so a book that fails quickly doesn't have to wait for two
    separate scheduled ticks to notice — the "chain to the next book
    immediately" behaviour James asked for depends on this being usable
    outside poll_tick's own loop too. Returns the resolved status, or None
    if nothing settled (unrecognized status string, or still pending)."""
    raw_status = str(data.get("status") or "").lower()
    try:
        new_status = LibrarrRequestStatus(raw_status)
    except ValueError:
        return None  # unrecognized status string — leave as-is, retry next tick

    if new_status == LibrarrRequestStatus.pending:
        # Shouldn't normally happen (submit_tick only creates a row after
        # approving), but if it does — a transient failure on the original
        # approve call, or Librarr resetting it for its own reasons — retry
        # approving rather than leaving it stuck forever with nothing
        # progressing it.
        await _approve_librarr_request(client, librarr_request_id)
        return None

    failure_message: str | None = None
    if new_status == LibrarrRequestStatus.failed:
        # "attention_note" confirmed live 2026-09-16 (e.g. "No search
        # results found") — "error"/"message" kept as fallbacks in case a
        # different failure path uses a different field.
        failure_message = str(
            data.get("attention_note") or data.get("error") or data.get("message")
            or "Librarr reported failure"
        )

    async with async_session_factory() as session:
        row = (
            await session.execute(
                select(LibrarrRequest).where(LibrarrRequest.librarr_request_id == librarr_request_id)
            )
        ).scalar_one_or_none()
        if row is not None:
            row.status = new_status
            if new_status in (LibrarrRequestStatus.completed, LibrarrRequestStatus.failed):
                row.resolved_at = datetime.now(UTC)
            if failure_message is not None:
                row.message = failure_message
            await session.commit()

    if new_status == LibrarrRequestStatus.failed:
        async with async_session_factory() as session:
            candidate = (
                await session.execute(
                    select(AcquisitionCandidate).where(
                        AcquisitionCandidate.request_id == request_id,
                        AcquisitionCandidate.status == AcquisitionStatus.fetching,
                    )
                )
            ).scalar_one_or_none()
            if candidate is not None:
                candidate.status = AcquisitionStatus.failed
                candidate.message = failure_message
                candidate.resolved_at = datetime.now(UTC)
                await session.commit()
        logger.warning(
            "torrent: Librarr request %s for %r failed: %s", librarr_request_id, request_id, failure_message,
        )

    return new_status


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
            resolved = await _apply_librarr_status(
                client, tracked.request_id, tracked.librarr_request_id, data
            )
            if resolved is None:
                continue
            updated += 1
            if resolved == LibrarrRequestStatus.failed:
                newly_failed += 1

    released = await _release_stale_fetching()
    return {"polled": len(in_flight), "updated": updated, "failed": newly_failed, "released": released}


async def _release_stale_fetching() -> int:
    """Free any book that's been `fetching` for too long, regardless of what
    its LibrarrRequest row says — Librarr reporting `completed` is not
    proof a usable file will ever actually reach the handoff folder (see
    `_FETCHING_STUCK_AFTER`'s comment). Two independent triggers: the full
    `_FETCHING_STUCK_AFTER` window (any reason), or the much shorter
    `_STALLED_ZERO_PROGRESS_AFTER` window combined with Librarr's own
    /api/downloads confirming 0 B/s right now (a dead torrent specifically —
    see that constant's comment). Returns the number released.

    Giving up locally isn't enough on its own (2026-09-17) — see the module
    docstring: Librarr never frees the underlying qBittorrent slot for a
    request nothing is polling any more, so this also best-effort cancels
    the matching LibrarrRequest (both remotely and in our own tracking row,
    so poll_tick stops polling it forever) and deletes the matching
    qBittorrent torrent directly when a hash is known — which it is exactly
    when the release reason was a confirmed 0 B/s match, the common case.

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
        ages = {
            row.id: now - updated
            for row in candidates
            if (updated := acquisition_service._aware(row.updated_at)) is not None
        }

        # Only worth the extra Librarr round-trip if something's actually
        # old enough for the fast-release window to possibly apply.
        zero_speed: dict[str, str | None] = {}
        if any(age >= _STALLED_ZERO_PROGRESS_AFTER for age in ages.values()):
            async with _librarr_client() as client:
                zero_speed = await _fetch_zero_speed_torrents(client)

        released: list[tuple[AcquisitionCandidate, str, timedelta, str | None]] = []
        for row in candidates:
            age = ages.get(row.id)
            if age is None:
                continue
            matched, hash_ = _matching_zero_speed(row.candidate_title or row.request_title, zero_speed)
            if age >= _FETCHING_STUCK_AFTER:
                released.append((row, "no usable file arrived in time", age, hash_))
            elif age >= _STALLED_ZERO_PROGRESS_AFTER and matched:
                released.append((row, "stalled with 0 B/s throughput, likely no seeders", age, hash_))

        if released:
            librarr_requests_by_request_id: dict[str, LibrarrRequest] = {
                lr.request_id: lr
                for lr in (
                    await session.execute(
                        select(LibrarrRequest).where(
                            LibrarrRequest.request_id.in_({row.request_id for row, *_ in released}),
                            LibrarrRequest.status.notin_(
                                (LibrarrRequestStatus.completed, LibrarrRequestStatus.failed)
                            ),
                        )
                    )
                )
                .scalars()
                .all()
            }

        for row, reason, age, hash_ in released:
            logger.warning("torrent: %r released early (%s) after %s fetching", row.request_title, reason, age)
            row.status = AcquisitionStatus.failed
            row.message = f"torrent subsystem: {reason}"
            row.resolved_at = datetime.now(UTC)

            librarr_request = librarr_requests_by_request_id.get(row.request_id)
            if librarr_request is not None:
                librarr_request.status = LibrarrRequestStatus.failed
                librarr_request.message = f"released early: {reason}"
                async with _librarr_client() as client:
                    try:
                        resp = await client.put(f"/api/requests/{librarr_request.librarr_request_id}/cancel")
                        resp.raise_for_status()
                    except httpx.HTTPError as exc:
                        logger.warning(
                            "torrent: cancelling Librarr request %s failed: %s",
                            librarr_request.librarr_request_id,
                            exc,
                        )

            if hash_ is not None:
                if await _qbittorrent_delete_torrent(hash_):
                    logger.info("torrent: deleted dead qBittorrent torrent %s for %r", hash_, row.request_title)
                else:
                    logger.warning(
                        "torrent: couldn't delete qBittorrent torrent %s for %r — it'll keep squatting a slot",
                        hash_,
                        row.request_title,
                    )

        if released:
            await session.commit()
    return len(released)


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
                session, pending, provider, inbox.folder_id, library.folder_id, auto_resolve=True
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
