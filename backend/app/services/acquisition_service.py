"""Match unfilled viewer requests to OpenBooks EPUBs, for James to approve.

The viewer's ``bookbrain-wishlist.json`` is the request list — anyone in the
household can add a book there (status ``wanted``). This service searches
OpenBooks for each still-``wanted`` item, keeps the plausible **EPUB** matches
in ``acquisition_candidates``, and lets James approve one per request from the
Find a Book page. Approving downloads it into the Drive inbox (via
``acquire_service``) and flips the wishlist item to ``sourced`` so the
household can see it's handled; the viewer's own reconcile moves it to
``acquired`` once the organised book lands in the library.

Two entry points populate the queue: the admin "Search open requests" button
(a background job) and a nightly step. Neither downloads anything — that's
always a manual approve.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
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


def score_candidate(req_title: str, req_author: str | None, cand: BookResult) -> float:
    """0..1-ish confidence that `cand` is `req_title`/`req_author`.

    OpenBooks' result parser routinely swaps the author and title fields and
    sometimes only the raw `full` (`!server Author - Title.epub`) is right, so
    every field is treated as a haystack and both orientations are tried."""
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


def _rank(req_title: str, req_author: str | None, results: list[BookResult]) -> list[tuple[float, BookResult]]:
    seen: set[str] = set()
    ranked: list[tuple[float, BookResult]] = []
    for cand in results:
        if cand.format.lower() != "epub":  # EPUB only, by design
            continue
        if cand.full in seen:
            continue
        seen.add(cand.full)
        s = score_candidate(req_title, req_author, cand)
        if s >= _MIN_SCORE:
            ranked.append((s, cand))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return ranked


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
    session: AsyncSession, item: dict, ranked: list[tuple[float, BookResult]]
) -> bool:
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
    row.resolved_at = None
    row.message = None

    if ranked:
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
        ranked = _rank(item["title"], item.get("author"), results)
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
        ranked = _rank(row.request_title, row.request_author, cands)
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
        await _prune_now_in_library(session)
        await _rerank_existing(session)
        rows = list((await session.execute(select(AcquisitionCandidate))).scalars())

    views: list[RequestView] = []
    seen_ids = {r.request_id for r in rows}
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
