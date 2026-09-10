"""Match unfilled viewer requests to OpenBooks EPUBs, for James to approve.

The viewer's ``bookbrain-wishlist.json`` is the request list — anyone in the
household can add a book there (status ``wanted``). This service searches
OpenBooks for each still-``wanted`` item, keeps the plausible **EPUB** matches
in ``acquisition_candidates``, and lets James approve one per request from the
Find a Book page. Approving downloads it into the Drive inbox (via
``acquire_service``) and flips the wishlist item to ``sourced`` so the
household can see it's handled; the viewer's own reconcile moves it to
``acquired`` once the organised book lands in the library.

The admin "Search…" button (a background job) and a nightly step populate the
candidate rows without downloading; "Get this" per row downloads one. When
James turns on auto-get, `autoget_tick` (an in-process job) instead drives the
whole thing itself while everything's idle: each tick it picks the next book
that's due, searches OpenBooks for it *right then* and downloads the best EPUB
immediately — so the download command is never stale — backing a book off when
the sources won't deliver it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
import zlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.db import async_session_factory
from app.data.models import (
    AcquisitionCandidate,
    AcquisitionStatus,
    Author,
    Book,
    File,
    FileStatus,
)
from app.providers.drive.provider import DriveProvider
from app.services import acquire_service, openbooks_service
from app.services.openbooks_service import BookResult, OpenBooksError, OpenBooksRateLimited
from app.services.text_match import normalize, normalize_person_name, normalize_title, title_similarity

logger = logging.getLogger(__name__)

_WISHLIST_FILENAME = "bookbrain-wishlist.json"

_MIN_SCORE = 0.72
_STRONG_SCORE = 0.9
_MAX_ALTERNATIVES = 6
_MAX_REQUESTS_PER_RUN = 40  # safety cap; each search costs ~11s of rate-limit wait
_SEARCH_SPACING_SECONDS = 11.0  # OpenBooks enforces >=10s between searches server-side


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------


def score_candidate(
    req_title: str, req_author: str | None, cand: BookResult, *, demerit: float = 0.0
) -> float:
    """0..1-ish confidence that `cand` is `req_title`/`req_author`.

    OpenBooks' result parser routinely swaps the author and title fields and
    sometimes only the raw `full` (`!server Author - Title.epub`) is right, so
    every field is treated as a haystack and both orientations are tried.

    `demerit` (0..~0.25) knocks down a server that's been timing out on us
    lately (see `_server_demerits`) so ranking rotates off it."""
    rt = normalize_title(req_title)
    if not rt:
        return 0.0

    haystack = " ".join(normalize(x) for x in (cand.title, cand.author, cand.full))
    title_contained = rt in haystack

    fuzzy = max(
        title_similarity(req_title, cand.title),
        title_similarity(req_title, cand.author),
        title_similarity(req_title, cand.full),
    )
    score = _STRONG_SCORE if title_contained else fuzzy
    if score < _MIN_SCORE:
        return 0.0

    if req_author:
        author_key = normalize_person_name(req_author).replace(" ", "")
        if author_key and author_key in haystack:
            score = min(1.0, score + 0.08)

    # small nudge toward a "retail"/versioned rip over a bare scan
    if re.search(r"retail|\(v\d", cand.full, re.IGNORECASE):
        score = min(1.0, score + 0.02)

    # Reliability: prefer servers that reliably complete a DCC transfer, and a
    # sensibly-sized file, over the flaky ones that send truncated files (which
    # have crashed the OpenBooks process). These drop a pick well below a good
    # one so "Get this" never auto-selects it — it stays a manual alternative.
    server = (cand.server or "").lower()
    if server in _RELIABLE_SERVERS:
        score += 0.06

    sz = _size_bytes(cand.size)
    if sz is None:
        score -= 0.15  # unknown size ("N/A") — mildly suspect
    elif sz < 40_000:
        score -= 0.5  # a 12 KB "epub" is a stub / reading guide / broken rip
    elif sz > 60_000_000:
        score -= 0.1  # implausibly large for an epub — usually mislabeled

    score -= demerit  # a server that keeps timing out on us

    return round(max(0.0, min(1.0, score)), 4)


# Book servers on #ebook that reliably finish a DCC transfer (observed).
_RELIABLE_SERVERS = {"bsk", "oatmeal", "ook", "dv8", "horla", "pondering-ebooks2"}

_SIZE_RE = re.compile(r"([\d.]+)\s*(kb|mb|gb|b)?\b", re.IGNORECASE)
_SIZE_UNIT = {"b": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3, "": 1024**2}  # bare number = MB


def _size_bytes(size: str | None) -> int | None:
    """OpenBooks reports sizes like "1.26MB" / "970.98KB" / "N/A" / a bare
    "1.26" (Firebook — MB). Returns bytes, or None when it can't be read."""
    if not size:
        return None
    m = _SIZE_RE.match(size.strip())
    if not m:
        return None
    try:
        return int(float(m.group(1)) * _SIZE_UNIT[(m.group(2) or "").lower()])
    except (ValueError, KeyError):
        return None


def _rank(
    req_title: str,
    req_author: str | None,
    results: list[BookResult],
    *,
    demerits: dict[str, float] | None = None,
) -> list[tuple[float, BookResult]]:
    demerits = demerits or {}
    seen: set[str] = set()
    ranked: list[tuple[float, BookResult]] = []
    for cand in results:
        if cand.format.lower() != "epub":  # EPUB only, by design
            continue
        if cand.full in seen:
            continue
        seen.add(cand.full)
        s = score_candidate(
            req_title, req_author, cand,
            demerit=demerits.get((cand.server or "").lower(), 0.0),
        )
        if s >= _MIN_SCORE:
            ranked.append((s, cand))
    # A deterministic per-candidate tiebreak: for a clean retail match every
    # server scores identically, and a plain sort then always picks whichever
    # one OpenBooks listed first — which sent ~90% of our downloads to a single
    # server and got our nick rate-limited. This spreads ties across servers
    # (stable per `full`, so a given book's pick doesn't flap).
    ranked.sort(
        key=lambda sb: (sb[0], zlib.crc32(sb[1].full.encode()) % 1000 / 1_000_000.0),
        reverse=True,
    )
    return ranked


# A #ebook book bot rate-limits DCC requests from one nick; OpenBooks uses a
# fixed nick, so after enough requests to a server it just stops answering us
# (75s timeout). Count recent timeouts per server and dock that server's score
# so ranking rotates to the ones that are still delivering. The window is
# short so a server recovers on its own once we stop hammering it.
_SERVER_DEMERIT_WINDOW = timedelta(hours=2)
_SERVER_DEMERIT_STEP = 0.06  # per recent timeout
_SERVER_DEMERIT_CAP = 0.25
_DOWNLOAD_TIMEOUT_MARKER = "didn't deliver the file within"


async def _server_demerits(session: AsyncSession) -> dict[str, float]:
    """{server_lower: penalty} from download timeouts in the last couple of
    hours — feed to `_rank(..., demerits=)`."""
    since = datetime.now(UTC).replace(tzinfo=None) - _SERVER_DEMERIT_WINDOW
    rows = (
        await session.execute(
            select(AcquisitionCandidate.candidate_server, func.count())
            .where(
                AcquisitionCandidate.status == AcquisitionStatus.failed,
                AcquisitionCandidate.candidate_server.is_not(None),
                AcquisitionCandidate.message.like(f"%{_DOWNLOAD_TIMEOUT_MARKER}%"),
                AcquisitionCandidate.updated_at >= since,
            )
            .group_by(AcquisitionCandidate.candidate_server)
        )
    ).all()
    return {
        srv.lower(): min(_SERVER_DEMERIT_CAP, n * _SERVER_DEMERIT_STEP)
        for srv, n in rows
        if srv
    }


def _cand_dict(score: float, b: BookResult) -> dict:
    return {
        "full": b.full,
        "title": b.title,
        "author": b.author,
        "format": b.format,
        "size": b.size,
        "server": b.server,
        "score": score,
    }


# --------------------------------------------------------------------------
# the viewer wishlist sidecar
# --------------------------------------------------------------------------


@dataclass
class _Wishlist:
    file_id: str | None
    raw: dict
    items: list[dict]


def _read_wishlist(provider: DriveProvider, library_folder_id: str) -> _Wishlist:
    found = next(
        (f for f in provider.list_files_in_folder(library_folder_id) if f["name"] == _WISHLIST_FILENAME),
        None,
    )
    if found is None:
        return _Wishlist(file_id=None, raw={"version": 2, "items": []}, items=[])
    raw = json.loads(provider.download_file(found["id"]).decode("utf-8"))
    items = [i for i in (raw.get("items") or []) if isinstance(i, dict) and isinstance(i.get("title"), str)]
    return _Wishlist(file_id=found["id"], raw=raw, items=items)


def _mark_wishlist_sourced(provider: DriveProvider, library_folder_id: str, request_id: str) -> bool:
    """Best-effort read-modify-write: flip one item to ``sourced``. Plain
    last-writer-wins (the viewer writes this file too); a lost update just
    means the household doesn't see the status move until reconcile."""
    wl = _read_wishlist(provider, library_folder_id)
    if wl.file_id is None:
        return False
    now = datetime.now(UTC).isoformat()
    changed = False
    for item in wl.raw.get("items") or []:
        if isinstance(item, dict) and item.get("id") == request_id and item.get("status") == "wanted":
            item["status"] = "sourced"
            item["statusBy"] = "BookBrain"
            item["statusNote"] = "Auto-sourced from OpenBooks"
            item["statusAt"] = now
            item["acquired"] = False
            changed = True
    if not changed:
        return False
    provider.update_file_content(
        wl.file_id,
        new_name=_WISHLIST_FILENAME,
        data=json.dumps(wl.raw, ensure_ascii=False).encode("utf-8"),
        mime_type="application/json",
    )
    return True


# --------------------------------------------------------------------------
# refresh (search) — the button's job + the nightly step
# --------------------------------------------------------------------------


@dataclass
class RefreshJob:
    job_id: str
    status: str = "running"  # running | done | failed
    searched: int = 0
    total: int = 0
    with_candidates: int = 0
    outstanding: int = 0
    detail: str | None = None


_jobs: dict[str, RefreshJob] = {}


def get_refresh_job(job_id: str) -> RefreshJob | None:
    return _jobs.get(job_id)


def has_active_refresh_job() -> bool:
    return any(j.status == "running" for j in _jobs.values())


def new_refresh_job() -> RefreshJob:
    job = RefreshJob(job_id=uuid.uuid4().hex)
    _jobs[job.job_id] = job
    return job


def _request_query(item: dict) -> str:
    parts = [item.get("title") or ""]
    if item.get("author"):
        parts.append(item["author"])
    return " ".join(" ".join(parts).split())


def _book_key(title: str | None, author: str | None, isbn13: str | None) -> str:
    if isbn13 and isbn13.strip():
        return f"isbn:{isbn13.strip()}"
    return f"{normalize_title(title)}|{normalize_person_name(author)}"


async def _gather_targets(provider: DriveProvider, library_folder_id: str) -> list[dict]:
    """Every book BookBrain should try to get, from all three sources:
    unfilled wishlist requests, the owner's un-owned Hardcover want-to-read,
    and curated-list candidates. Deduped by book (ISBN, else title+author);
    the wishlist wins a tie."""
    from app.services.library_index_service import (
        LISTS_FILENAME,
        READING_FILENAME,
        _read_json_file,
    )

    wl = await asyncio.to_thread(_read_wishlist, provider, library_folder_id)
    reading = await asyncio.to_thread(_read_json_file, provider, library_folder_id, READING_FILENAME)
    lists = await asyncio.to_thread(_read_json_file, provider, library_folder_id, LISTS_FILENAME)

    async with async_session_factory() as session:
        owned = {
            _owned_key(t, a)
            for t, a in (
                await session.execute(
                    select(Book.canonical_title, Author.name)
                    .join(File, File.book_id == Book.id)
                    .join(Author, Author.id == Book.author_id, isouter=True)
                    .where(File.status == FileStatus.organised)
                )
            ).all()
        }

    targets: list[dict] = []
    seen: set[str] = set()

    def _add(request_id: str, source: str, title: str, author, isbn13) -> None:
        title = (title or "").strip()
        if not title:
            return
        author = author.strip() if isinstance(author, str) and author.strip() else None
        isbn13 = isbn13.strip() if isinstance(isbn13, str) and isbn13.strip() else None
        if _owned_key(title, author) in owned:  # already in the library
            return
        key = _book_key(title, author, isbn13)
        if key in seen:
            return
        seen.add(key)
        targets.append(
            {"request_id": request_id, "source": source, "title": title, "author": author, "isbn13": isbn13}
        )

    for item in wl.items:
        if item.get("status") == "wanted" and item.get("id"):
            _add(item["id"], "wishlist", item.get("title"), item.get("author"), item.get("isbn13"))

    for i in reading.get("wantUnowned") or []:
        if isinstance(i, dict):
            key = _book_key(i.get("title"), i.get("author"), i.get("isbn13"))
            _add(f"wtr:{key}", "want_to_read", i.get("title"), i.get("author"), i.get("isbn13"))

    for i in lists.get("candidates") or []:
        if isinstance(i, dict):
            key = _book_key(i.get("title"), i.get("author"), i.get("isbn13"))
            _add(f"list:{key}", "list", i.get("title"), i.get("author"), i.get("isbn13"))

    return targets


async def _search_one(item: dict) -> list[BookResult]:
    query = _request_query(item)
    try:
        outcome = await openbooks_service.search(query)
    except OpenBooksRateLimited as exc:
        await asyncio.sleep(exc.wait_seconds + 1)
        outcome = await openbooks_service.search(query)
    return outcome.results


async def _upsert(
    session: AsyncSession,
    item: dict,
    ranked: list[tuple[float, BookResult]],
    *,
    preserve_existing: bool = False,
) -> bool:
    """Write the search outcome for `item` onto its candidate row (creating it
    if new). With `preserve_existing`, a search that turns up nothing keeps the
    row's previous match rather than wiping it to `no_match` — for the auto-get
    loop, where a momentary empty result (bot offline) shouldn't discard a good
    lead found last time."""
    row = (
        await session.execute(
            select(AcquisitionCandidate).where(AcquisitionCandidate.request_id == item["request_id"])
        )
    ).scalar_one_or_none()
    if row is None:
        row = AcquisitionCandidate(request_id=item["request_id"], request_title=item["title"])
        session.add(row)

    row.source = item.get("source") or "wishlist"
    row.request_title = item["title"]
    row.request_author = item.get("author")

    if ranked:
        row.resolved_at = None
        row.message = None
        best_score, best = ranked[0]
        row.status = AcquisitionStatus.pending
        row.candidate_full = best.full
        row.candidate_title = best.title
        row.candidate_author = best.author
        row.candidate_format = best.format
        row.candidate_size = best.size
        row.candidate_server = best.server
        row.score = best_score
        row.alternatives_json = [_cand_dict(s, b) for s, b in ranked[1 : 1 + _MAX_ALTERNATIVES]]
        return True

    if preserve_existing and row.candidate_full and row.status in (
        AcquisitionStatus.pending,
        AcquisitionStatus.failed,
    ):
        row.message = "re-search turned up nothing new — keeping the previous match"
        row.resolved_at = datetime.now(UTC)  # still count it as an attempt (backoff)
        return False

    row.resolved_at = None
    row.status = AcquisitionStatus.no_match
    row.candidate_full = None
    row.candidate_title = None
    row.candidate_author = None
    row.candidate_format = None
    row.candidate_size = None
    row.candidate_server = None
    row.score = None
    row.alternatives_json = None
    row.message = "no EPUB match found"
    return False


async def refresh_candidates(
    provider: DriveProvider,
    library_folder_id: str,
    *,
    job: RefreshJob | None = None,
    limit: int | None = None,
) -> dict:
    """Search OpenBooks for the books BookBrain should try to get (wishlist
    requests + Hardcover want-to-read + list candidates) that haven't been
    searched yet or whose title/author changed, and (re)populate their
    candidate rows. `limit` caps how many are searched this run — the rest
    wait for the next click / the nightly."""
    targets = await _gather_targets(provider, library_folder_id)

    async with async_session_factory() as session:
        await _dedupe_candidates(session)
        demerits = await _server_demerits(session)
        existing = {
            r.request_id: r
            for r in (await session.execute(select(AcquisitionCandidate))).scalars()
        }

    todo: list[dict] = []
    for item in targets:
        row = existing.get(item["request_id"])
        unchanged = (
            row is not None
            and row.request_title == item["title"]
            and (row.request_author or None) == (item.get("author") or None)
        )
        if row is not None and unchanged and row.status in (
            AcquisitionStatus.approved,
            AcquisitionStatus.skipped,
            AcquisitionStatus.pending,
        ):
            continue
        todo.append(item)

    cap = max(1, min(limit or _MAX_REQUESTS_PER_RUN, 100))
    outstanding = len(todo)
    todo = todo[:cap]
    if job is not None:
        job.total = len(todo)

    searched = with_candidates = 0
    for idx, item in enumerate(todo):
        if idx > 0:
            await asyncio.sleep(_SEARCH_SPACING_SECONDS)
        try:
            results = await _search_one(item)
        except OpenBooksError as exc:
            logger.warning("acquire: search failed for %r: %s", _request_query(item), exc)
            if job is not None:
                job.detail = f"search failed: {exc}"
            break
        searched += 1
        ranked = _rank(item["title"], item.get("author"), results, demerits=demerits)
        async with async_session_factory() as session:
            hit = await _upsert(session, item, ranked)
            await session.commit()
        if hit:
            with_candidates += 1
        if job is not None:
            job.searched = searched
            job.with_candidates = with_candidates

    left = max(0, outstanding - searched)
    result = {
        "targets": len(targets),
        "outstanding": left,
        "searched": searched,
        "withCandidates": with_candidates,
    }
    if job is not None:
        job.status = "done"
        job.outstanding = left
        job.detail = (
            job.detail or f"searched {searched}, {with_candidates} with a match, {left} left"
        )
    return result


async def run_refresh_job(
    job_id: str, provider: DriveProvider, library_folder_id: str, *, limit: int | None = None
) -> None:
    job = _jobs.get(job_id)
    if job is None:  # pragma: no cover - defensive
        return
    try:
        await refresh_candidates(provider, library_folder_id, job=job, limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.exception("acquire: refresh job failed")
        job.status = "failed"
        job.detail = str(exc)


# --------------------------------------------------------------------------
# listing + approve / skip / reset
# --------------------------------------------------------------------------


@dataclass
class RequestView:
    request_id: str
    source: str  # wishlist | want_to_read | list
    title: str
    author: str | None
    requested_by: str | None
    cover: str | None
    status: str
    candidate: dict | None
    alternatives: list[dict] = field(default_factory=list)
    score: float | None = None
    message: str | None = None
    resolved_at: str | None = None


async def list_suggestions(provider: DriveProvider, library_folder_id: str) -> dict:
    """Books to look for that aren't on the wishlist: the owner's Hardcover
    want-to-read that isn't owned (`bookbrain-reading.json` → `wantUnowned`)
    and the curated-list candidates (`bookbrain-lists.json` → `candidates`).
    Read-only, from the sidecars the viewer already writes."""
    from app.services.library_index_service import (
        LISTS_FILENAME,
        READING_FILENAME,
        _read_json_file,
    )

    reading = await asyncio.to_thread(_read_json_file, provider, library_folder_id, READING_FILENAME)
    lists = await asyncio.to_thread(_read_json_file, provider, library_folder_id, LISTS_FILENAME)

    def _clean(items: object, *, with_list: bool) -> list[dict]:
        out: list[dict] = []
        for i in items or []:  # type: ignore[union-attr]
            if not isinstance(i, dict) or not isinstance(i.get("title"), str) or not i["title"].strip():
                continue
            entry = {
                "title": i["title"].strip(),
                "author": i.get("author") if isinstance(i.get("author"), str) else None,
                "isbn13": i.get("isbn13") if isinstance(i.get("isbn13"), str) else None,
            }
            if with_list:
                entry["from_list"] = i.get("fromList") if isinstance(i.get("fromList"), str) else None
            out.append(entry)
        return out

    return {
        "want_to_read": _clean(reading.get("wantUnowned"), with_list=False),
        "from_lists": _clean(lists.get("candidates"), with_list=True),
    }


def _owned_key(title: str | None, author: str | None) -> str:
    return f"{normalize_title(title)}|{normalize_person_name(author)}"


async def _prune_now_in_library(session: AsyncSession) -> int:
    """Delete `approved` candidate rows whose book is now an organised file —
    it's done, no need to keep it in the "Books to get" list. (Wishlist rows
    self-clean once the viewer reconciles the item to `acquired`; this covers
    the want-to-read / list rows that have no wishlist item.)"""
    owned = {
        _owned_key(t, a)
        for t, a in (
            await session.execute(
                select(Book.canonical_title, Author.name)
                .join(File, File.book_id == Book.id)
                .join(Author, Author.id == Book.author_id, isouter=True)
                .where(File.status == FileStatus.organised)
            )
        ).all()
    }
    if not owned:
        return 0
    approved = (
        await session.execute(
            select(AcquisitionCandidate).where(
                AcquisitionCandidate.status == AcquisitionStatus.approved
            )
        )
    ).scalars()
    pruned = 0
    for row in approved:
        if _owned_key(row.request_title, row.request_author) in owned:
            await session.delete(row)
            pruned += 1
    if pruned:
        await session.commit()
        logger.info("acquire: cleared %d sourced book(s) now in the library", pruned)
    return pruned


# Which row in a duplicate group we fold onto the keeper: best status first,
# then best score, then the freshest search (highest id).
_DEDUPE_STATUS_RANK = {
    AcquisitionStatus.approved: 0,
    AcquisitionStatus.skipped: 1,
    AcquisitionStatus.pending: 2,
    AcquisitionStatus.failed: 3,
    AcquisitionStatus.no_match: 4,
}
_DEDUPE_CARRY_FIELDS = (
    "status", "candidate_full", "candidate_title", "candidate_author",
    "candidate_format", "candidate_size", "candidate_server", "score",
    "alternatives_json", "message", "resolved_at",
)


async def _dedupe_candidates(session: AsyncSession) -> int:
    """One book can be on the wishlist AND the Hardcover want-to-read AND a
    curated list — three `request_id`s, three candidate rows, so auto-get
    wastes a retry on each copy and the panel counts are inflated. Collapse
    each book (normalised title+author) to one row: keep a wishlist row if the
    group has one (its id drives the wishlist `sourced` flip), else the
    oldest, and fold the best status + candidate from the group onto it."""
    rows = list((await session.execute(select(AcquisitionCandidate))).scalars())
    groups: dict[str, list[AcquisitionCandidate]] = defaultdict(list)
    for row in rows:
        groups[_owned_key(row.request_title, row.request_author)].append(row)

    removed = 0
    for group in groups.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda r: (0 if (r.source or "wishlist") == "wishlist" else 1, r.id))
        keeper = group[0]
        best = min(
            group,
            key=lambda r: (_DEDUPE_STATUS_RANK.get(r.status, 9), -(r.score or 0.0), -r.id),
        )
        if best is not keeper:
            for name in _DEDUPE_CARRY_FIELDS:
                setattr(keeper, name, getattr(best, name))
        for row in group:
            if row is not keeper:
                await session.delete(row)
                removed += 1
    if removed:
        await session.commit()
        logger.info("acquire: merged %d duplicate candidate row(s) by book", removed)
    return removed


def _row_candidates(row: AcquisitionCandidate) -> list[BookResult]:
    out: list[BookResult] = []
    if row.candidate_full:
        out.append(
            BookResult(
                server=row.candidate_server or "",
                author=row.candidate_author or "",
                title=row.candidate_title or "",
                format=row.candidate_format or "epub",
                size=row.candidate_size or "",
                full=row.candidate_full,
            )
        )
    for a in row.alternatives_json or []:
        if isinstance(a, dict) and a.get("full"):
            out.append(
                BookResult(
                    server=a.get("server") or "",
                    author=a.get("author") or "",
                    title=a.get("title") or "",
                    format=a.get("format") or "epub",
                    size=a.get("size") or "",
                    full=a["full"],
                )
            )
    return out


async def _rerank_existing(session: AsyncSession) -> int:
    """Re-score the stored candidate + alternatives for every un-resolved row
    with the current `score_candidate` and promote a better pick if the
    ranking changed — so a scoring tweak (e.g. dropping stub-sized files)
    applies to the queue without a fresh OpenBooks search."""
    demerits = await _server_demerits(session)
    rows = (
        await session.execute(
            select(AcquisitionCandidate).where(
                AcquisitionCandidate.status.in_(
                    [AcquisitionStatus.pending, AcquisitionStatus.failed]
                )
            )
        )
    ).scalars()
    changed = 0
    for row in rows:
        cands = _row_candidates(row)
        if not cands:
            continue
        ranked = _rank(row.request_title, row.request_author, cands, demerits=demerits)
        if not ranked:
            continue
        best_score, best = ranked[0]
        if best.full == row.candidate_full:
            continue
        row.candidate_full = best.full
        row.candidate_title = best.title
        row.candidate_author = best.author
        row.candidate_format = best.format
        row.candidate_size = best.size
        row.candidate_server = best.server
        row.score = best_score
        row.alternatives_json = [_cand_dict(s, b) for s, b in ranked[1 : 1 + _MAX_ALTERNATIVES]]
        if row.status == AcquisitionStatus.failed:
            row.status = AcquisitionStatus.pending
            row.message = None
        changed += 1
    if changed:
        await session.commit()
        logger.info("acquire: re-ranked %d row(s) to a better candidate", changed)
    return changed


async def list_requests(provider: DriveProvider, library_folder_id: str) -> list[RequestView]:
    wl = await asyncio.to_thread(_read_wishlist, provider, library_folder_id)
    by_id = {i["id"]: i for i in wl.items if i.get("id")}

    async with async_session_factory() as session:
        await _dedupe_candidates(session)
        await _prune_now_in_library(session)
        await _rerank_existing(session)
        rows = list((await session.execute(select(AcquisitionCandidate))).scalars())

    views: list[RequestView] = []
    seen_ids = {r.request_id for r in rows}
    # Books that already have a candidate row (possibly under a want-to-read /
    # list id) — don't also list them as an "unsearched" wishlist item.
    seen_keys = {_owned_key(r.request_title, r.request_author) for r in rows}
    for row in rows:
        source = row.source or "wishlist"
        if source == "wishlist":
            item = by_id.get(row.request_id)
            if item is None:
                continue  # request deleted from the wishlist
            # An item the household already moved on (sourced/acquired/declined
            # by hand) shouldn't clutter the queue unless we did it.
            if item.get("status") not in ("wanted", "sourced"):
                continue
            title = item.get("title") or row.request_title
            author = item.get("author")
            requested_by = item.get("requestedBy")
            cover = item.get("cover")
        else:
            title = row.request_title
            author = row.request_author
            requested_by = None
            cover = None

        candidate = (
            {
                "full": row.candidate_full,
                "title": row.candidate_title,
                "author": row.candidate_author,
                "format": row.candidate_format,
                "size": row.candidate_size,
                "server": row.candidate_server,
                "score": row.score,
            }
            if row.candidate_full
            else None
        )
        views.append(
            RequestView(
                request_id=row.request_id,
                source=source,
                title=title,
                author=author,
                requested_by=requested_by,
                cover=cover,
                status=row.status.value,
                candidate=candidate,
                alternatives=row.alternatives_json or [],
                score=row.score,
                message=row.message,
                resolved_at=row.resolved_at.isoformat() if row.resolved_at else None,
            )
        )

    # Every wishlist request that hasn't been searched yet still belongs in
    # the list (so the admin matches the viewer's Wishlist) — as "unsearched".
    # The big want-to-read / list backlog only appears once searched.
    for item in wl.items:
        rid = item.get("id")
        if not rid or rid in seen_ids or not isinstance(item.get("title"), str):
            continue
        if item.get("status") not in ("wanted", "sourced"):
            continue
        if _owned_key(item["title"], item.get("author")) in seen_keys:
            continue  # already shown via its candidate row
        views.append(
            RequestView(
                request_id=rid,
                source="wishlist",
                title=item["title"],
                author=item.get("author"),
                requested_by=item.get("requestedBy"),
                cover=item.get("cover"),
                status="unsearched",
                candidate=None,
            )
        )

    order = {"pending": 0, "unsearched": 1, "no_match": 2, "failed": 2, "approved": 3, "skipped": 4}
    src_order = {"wishlist": 0, "want_to_read": 1, "list": 2}
    views.sort(key=lambda v: (order.get(v.status, 9), src_order.get(v.source, 9), -(v.score or 0)))
    return views


def _filename_for(full: str, title: str | None, author: str | None) -> str:
    """Prefer a clean "Author - Title.epub"; fall back to the name embedded in
    the `!server <name>` command."""
    if title:
        base = f"{author} - {title}" if author else title
        return f"{' '.join(base.split())}.epub"
    name = full.split(" ", 1)[1] if " " in full else full
    return name.strip()


async def approve_request(
    request_id: str,
    full_override: str | None,
    provider: DriveProvider,
    inbox_folder_id: str,
    library_folder_id: str,
) -> dict:
    async with async_session_factory() as session:
        row = (
            await session.execute(
                select(AcquisitionCandidate).where(AcquisitionCandidate.request_id == request_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise OpenBooksError("no candidate for that request — search open requests first")

        full = full_override or row.candidate_full
        if not full:
            raise OpenBooksError("no download command for that request")

        alt = next((a for a in (row.alternatives_json or []) if a.get("full") == full), None)
        title = alt["title"] if alt else row.candidate_title
        author = alt["author"] if alt else row.candidate_author
        filename = _filename_for(full, title, author)

        try:
            result = await acquire_service.acquire_to_inbox(full, filename, provider, inbox_folder_id)
        except OpenBooksError as exc:
            row.status = AcquisitionStatus.failed
            row.message = str(exc)
            row.resolved_at = datetime.now(UTC)  # so auto-get won't re-hit it straight away
            await session.commit()
            raise

        row.status = AcquisitionStatus.approved
        row.candidate_full = full
        row.candidate_title = title
        row.candidate_author = author
        row.message = None
        row.resolved_at = datetime.now(UTC)
        await session.commit()

    try:
        await asyncio.to_thread(_mark_wishlist_sourced, provider, library_folder_id, request_id)
    except Exception:  # noqa: BLE001
        logger.exception("acquire: couldn't mark wishlist item %s sourced", request_id)

    return result


async def _set_status(request_id: str, status: AcquisitionStatus) -> None:
    async with async_session_factory() as session:
        row = (
            await session.execute(
                select(AcquisitionCandidate).where(AcquisitionCandidate.request_id == request_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise OpenBooksError("no candidate for that request")
        row.status = status
        row.resolved_at = datetime.now(UTC)
        await session.commit()


async def skip_request(request_id: str) -> None:
    await _set_status(request_id, AcquisitionStatus.skipped)


async def reset_request(request_id: str) -> None:
    """Forget a request entirely so the next refresh searches it fresh."""
    async with async_session_factory() as session:
        row = (
            await session.execute(
                select(AcquisitionCandidate).where(AcquisitionCandidate.request_id == request_id)
            )
        ).scalar_one_or_none()
        if row is not None:
            await session.delete(row)
            await session.commit()


# --------------------------------------------------------------------------
# auto-get — download one confident candidate per minute, while idle
# --------------------------------------------------------------------------

_AUTOGET_MIN_SCORE = 0.9  # only a strong title+author match from a decent source
_AUTOSCAN_INBOX_THRESHOLD = 20  # kick a scan once auto-get has piled up this many
_AUTOGET_INTICK_TRIES = 3  # servers to try in one tick before giving up for now
_AUTOGET_INTICK_TIMEOUT_TRIES = 2  # ... but stop after this many 75s timeouts
# Backoff before auto-get re-attempts a book. A fresh search + download every
# tick means nothing sits around going stale — but a book the sources won't
# deliver shouldn't be hammered, so it cools down between tries, longer once
# it's been failing a while, and a searched-but-nothing-found book only gets
# re-checked weekly.
_AUTOGET_RETRY_AFTER = timedelta(minutes=30)
_AUTOGET_STUBBORN_AFTER = timedelta(hours=2)  # a book failing this long → the long backoff
_AUTOGET_STUBBORN_BACKOFF = timedelta(hours=6)
_AUTOGET_NOMATCH_BACKOFF = timedelta(days=7)


async def _run_autoscan(scan_svc, job_id: str, creds, inbox_folder_id: str) -> None:
    try:
        await scan_svc.run_scan(job_id, creds, inbox_folder_id)
    except Exception:  # noqa: BLE001
        logger.exception("acquire: auto-scan failed")


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _target_backoff(row: AcquisitionCandidate) -> timedelta:
    if row.status == AcquisitionStatus.no_match:
        return _AUTOGET_NOMATCH_BACKOFF
    created = _aware(row.created_at)
    if created is not None and datetime.now(UTC) - created > _AUTOGET_STUBBORN_AFTER:
        return _AUTOGET_STUBBORN_BACKOFF
    return _AUTOGET_RETRY_AFTER


def _is_transient_download_error(exc: Exception) -> bool:
    """A slow / dead source, not a bad file — stop trying more candidates this
    tick and let the backoff carry it to the next attempt."""
    m = str(exc).lower()
    return (
        "didn't deliver the file within" in m
        or "download failed" in m
        or "refused the connection" in m
        or "could not connect" in m
        or "did not confirm an irc connection" in m
    )


async def autoget_tick(trigger: str = "scheduler") -> dict:
    """One iteration of the auto-get loop (the scheduler calls this every
    ~60s). While everything's idle: kicks a scan if the inbox has piled up
    (>= 20), else picks the next book that's due (no attempt yet, or its
    backoff has elapsed), **searches OpenBooks for it right now** and downloads
    the best EPUB (>= 0.9) immediately — so the `!server file` command is
    always seconds old, never stale. A book the sources won't deliver cools
    down (30 min, then 6 h once it's been failing > 2 h); one with no match
    at all is re-checked weekly. Never raises."""
    from app.core.config import get_settings
    from app.core.settings_keys import OPENBOOKS_AUTOGET_ENABLED
    from app.data.repositories.settings_repository import SettingsRepository
    from app.providers.drive.client import build_drive_service
    from app.services import openbooks_process_service, openbooks_service
    from app.services.auth_service import get_auth_service
    from app.services.drive_service import DriveService
    from app.services.scan_service import get_scan_service

    if not get_settings().openbooks_enabled:
        return {"skipped": "openbooks disabled"}
    async with async_session_factory() as session:
        if (await SettingsRepository(session).get(OPENBOOKS_AUTOGET_ENABLED)) != "true":
            return {"skipped": "auto-get off"}

    if (
        openbooks_service.is_busy()
        or has_active_refresh_job()
        or get_scan_service().has_running_job()
    ):
        return {"skipped": "busy"}
    if not (await asyncio.to_thread(openbooks_process_service.status)).get("running"):
        return {"skipped": "openbooks server not running"}

    async with async_session_factory() as session:
        repo = SettingsRepository(session)
        try:
            creds = await get_auth_service().get_credentials(repo)
        except Exception:  # noqa: BLE001 — token refresh failure, etc.
            return {"skipped": "no drive credentials"}
        inbox = await DriveService.get_inbox_folder_config(repo)
        library = await DriveService.get_library_folder_config(repo)
    if creds is None or inbox is None or library is None:
        return {"skipped": "not configured"}

    provider = DriveProvider(build_drive_service(creds))

    # Keep the inbox from piling up — auto-get fills it and nothing scans it
    # until the nightly. Kick a scan once it hits the threshold (the next
    # tick's has_running_job() guard then pauses auto-get until it's done).
    inbox_files = await asyncio.to_thread(provider.list_files_in_folder, inbox.folder_id)
    if len(inbox_files) >= _AUTOSCAN_INBOX_THRESHOLD:
        scan_svc = get_scan_service()
        job = scan_svc.create_job()
        asyncio.create_task(_run_autoscan(scan_svc, job.job_id, creds, inbox.folder_id))
        logger.info("acquire: inbox at %d files — auto-scan started", len(inbox_files))
        return {"scan_started": len(inbox_files)}

    targets = await _gather_targets(provider, library.folder_id)
    if not targets:
        return {"skipped": "no targets"}

    now = datetime.now(UTC)
    async with async_session_factory() as session:
        await _dedupe_candidates(session)
        demerits = await _server_demerits(session)
        rows = list((await session.execute(select(AcquisitionCandidate))).scalars())
    by_id = {r.request_id: r for r in rows}
    by_key: dict[str, AcquisitionCandidate] = {}
    for r in rows:
        by_key.setdefault(_owned_key(r.request_title, r.request_author), r)

    item: dict | None = None
    existing: AcquisitionCandidate | None = None
    for t in targets:
        row = by_id.get(t["request_id"]) or by_key.get(_owned_key(t["title"], t["author"]))
        if row is None:
            item, existing = t, None
            break
        if row.status in (AcquisitionStatus.approved, AcquisitionStatus.skipped):
            continue
        last = _aware(row.resolved_at) or _aware(row.updated_at)
        if last is None or now - last >= _target_backoff(row):
            item, existing = t, row
            break
    if item is None:
        return {"skipped": "all caught up or cooling down", "targets": len(targets)}

    rid, title = item["request_id"], item["title"]

    # If this book already has a row under a different id (want-to-read / list
    # vs the wishlist id `_gather_targets` prefers), re-key it so we don't make
    # a second row.
    if existing is not None and existing.request_id != rid:
        async with async_session_factory() as session:
            r = await session.get(AcquisitionCandidate, existing.id)
            if r is not None:
                r.request_id = rid
                r.source = item["source"]
                await session.commit()

    # Search fresh, right now.
    try:
        results = await _search_one(item)
    except OpenBooksError as exc:
        logger.warning("acquire: auto-get search failed for %r: %s", title, exc)
        return {"skipped": "search failed", "error": str(exc)}

    ranked = _rank(title, item.get("author"), results, demerits=demerits)
    async with async_session_factory() as session:
        await _upsert(session, item, ranked, preserve_existing=True)
        await session.commit()
        row = (
            await session.execute(
                select(AcquisitionCandidate).where(AcquisitionCandidate.request_id == rid)
            )
        ).scalar_one_or_none()

    if not ranked:
        if row is not None and row.status == AcquisitionStatus.no_match:
            logger.info("acquire: auto-get — no EPUB match for %r", title)
            return {"no_match": title}
        return {"skipped": "re-search found nothing; kept the prior match", "title": title}

    # One attempt per distinct server (best-scoring for that server), so an
    # in-tick retry always lands on a *different* server — the whole point.
    by_server: dict[str, tuple[float, BookResult]] = {}
    for s, c in ranked:
        if s < _AUTOGET_MIN_SCORE:
            continue
        by_server.setdefault((c.server or "").lower(), (s, c))
    attempts = sorted(by_server.values(), key=lambda x: -x[0])[:_AUTOGET_INTICK_TRIES]
    if not attempts:
        # There's an EPUB but nothing confident enough — leave it in the panel
        # for James, and back it off so we don't re-search it every tick.
        if row is not None:
            async with async_session_factory() as session:
                r = await session.get(AcquisitionCandidate, row.id)
                if r is not None:
                    r.resolved_at = now
                    await session.commit()
        best = ranked[0][0]
        logger.info("acquire: auto-get — best match for %r only %.2f, left for review", title, best)
        return {"skipped": "no confident match", "title": title, "score": best}

    last_exc: Exception | None = None
    timeouts = 0
    for i, (_score, cand) in enumerate(attempts, start=1):
        try:
            result = await approve_request(rid, cand.full, provider, inbox.folder_id, library.folder_id)
            logger.info("acquire: auto-got %r (%s) via %s", title, result.get("filename"), cand.server)
            return {"got": title, "filename": result.get("filename"), "tries": i, "server": cand.server}
        except OpenBooksError as exc:
            last_exc = exc
            if _is_transient_download_error(exc):
                timeouts += 1
                logger.info("acquire: auto-get — %r timed out on %s (%d)", title, cand.server, timeouts)
                if timeouts >= _AUTOGET_INTICK_TIMEOUT_TRIES:
                    break  # two dead servers is enough — let the backoff carry it
            else:
                logger.info("acquire: auto-get — %r didn't validate on %s (%s)", title, cand.server, exc)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            break

    logger.warning("acquire: auto-get failed for %r: %s", title, last_exc)
    return {"failed": title, "error": str(last_exc) if last_exc else "unknown"}
