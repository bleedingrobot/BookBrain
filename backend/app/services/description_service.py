"""Fills in `books.description` for organised books that have no blurb —
neither in the EPUB nor from any metadata provider. Tries Google Books /
Open Library first (real publisher copy), then optionally a short
model-written blurb. Flows to the viewer on the next index refresh, since
build_index_payload already reads `book.description` first."""

import asyncio
import logging
import re
import uuid
from collections.abc import Callable

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.data.db import async_session_factory
from app.data.models import Author, Book, File, FileStatus, Identifier, IdentifierType, MetadataSource
from app.schemas.descriptions import DescriptionBackfillEstimate
from app.providers.ai.anthropic_client import AnthropicIdentificationClient
from app.schemas.descriptions import DescriptionJobState, DescriptionJobStatus
from app.services.candidate_service import default_candidate_service

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_MIN_LEN = 40  # ignore uselessly short provider "descriptions"
_CONCURRENCY = 3
_OL_TIMEOUT = httpx.Timeout(8.0, connect=4.0)


def _clean(text: str | None) -> str | None:
    if not text:
        return None
    out = _WS_RE.sub(" ", _TAG_RE.sub(" ", text)).strip()
    return out or None


async def _open_library_description(client: httpx.AsyncClient, isbn: str) -> str | None:
    """Open Library keeps the blurb on the *work*, not the edition — walk
    isbn → edition → work. Free, no key. Raises httpx.ConnectError so the
    caller can stop trying if the host is unreachable; other failures just
    yield None."""
    try:
        edition = (await client.get(f"https://openlibrary.org/isbn/{isbn}.json")).json()
        works = edition.get("works") or []
        if not works:
            return None
        work = (await client.get(f"https://openlibrary.org{works[0]['key']}.json")).json()
        desc = work.get("description")
        if isinstance(desc, dict):
            desc = desc.get("value")
        return _clean(desc) if isinstance(desc, str) else None
    except httpx.ConnectError:
        raise
    except (httpx.HTTPError, ValueError, KeyError):
        return None


async def _books_needing_descriptions(session: AsyncSession) -> list[tuple[int, str, str | None]]:
    epub_desc_books = (
        select(File.book_id)
        .join(MetadataSource, MetadataSource.file_id == File.id)
        .where(MetadataSource.field_name == "description", File.book_id.is_not(None))
    )
    rows = await session.execute(
        select(Book.id, Book.canonical_title, Author.name)
        .join(File, File.book_id == Book.id)
        .outerjoin(Author, Book.author_id == Author.id)
        .where(
            File.status == FileStatus.organised,
            Book.description.is_(None),
            Book.id.not_in(epub_desc_books),
        )
        .distinct()
    )
    return [(r[0], r[1], r[2]) for r in rows.all()]


async def _isbns_for(session: AsyncSession, book_ids: list[int]) -> dict[int, tuple[str | None, str | None]]:
    out: dict[int, tuple[str | None, str | None]] = {}
    if not book_ids:
        return out
    rows = await session.execute(
        select(Identifier.book_id, Identifier.type, Identifier.value).where(
            Identifier.book_id.in_(book_ids),
            Identifier.type.in_([IdentifierType.isbn13, IdentifierType.isbn10]),
        )
    )
    for bid, itype, value in rows.all():
        i13, i10 = out.get(bid, (None, None))
        if itype == IdentifierType.isbn13:
            i13 = value
        else:
            i10 = value
        out[bid] = (i13, i10)
    return out


async def estimate_description_backfill() -> DescriptionBackfillEstimate:
    """Pure DB count + arithmetic — makes **zero** AI calls. Upper bound: it
    assumes every description-less book will need the model (the free
    provider pass may satisfy some), which is the number to warn on."""
    settings = get_settings()
    cap = settings.ai_description_cap
    try:
        async with async_session_factory() as session:
            books_missing = len(await _books_needing_descriptions(session))
    except Exception:
        logger.exception("description estimate: query failed")
        books_missing = 0
    will_process = min(books_missing, cap)
    return DescriptionBackfillEstimate(
        books_missing=books_missing,
        will_process=will_process,
        cap=cap,
        estimated_cost_usd=round(will_process * settings.ai_description_cost_usd, 2),
    )


async def backfill_descriptions(
    *,
    use_ai: bool = False,
    limit: int | None = None,
    ai_cap: int | None = None,
    on_progress: Callable[[dict[str, int], int], None] | None = None,
) -> dict[str, int]:
    counts = {"from_provider": 0, "from_ai": 0, "not_found": 0, "remaining": 0}
    candidates = default_candidate_service()
    ai = AnthropicIdentificationClient() if use_ai else None
    # Free provider lookups stay uncapped; only model-written blurbs are
    # rationed. _books_needing_descriptions is stateless, so a re-run picks
    # up whatever the cap left behind.
    ai_budget = {"left": ai_cap if (use_ai and ai_cap is not None) else None}
    deferred = {"n": 0}

    try:
        async with async_session_factory() as session:
            needing = await _books_needing_descriptions(session)
            isbns = await _isbns_for(session, [b[0] for b in needing])
    except Exception:
        logger.exception("description backfill: query failed")
        return counts

    todo = needing if limit is None else needing[:limit]
    counts["remaining"] = len(needing) - len(todo)
    total = len(todo)
    sem = asyncio.Semaphore(_CONCURRENCY)
    http = httpx.AsyncClient(timeout=_OL_TIMEOUT, follow_redirects=True)
    ol_dead = {"count": 0}  # stop hammering Open Library if it won't connect

    async def one(book_id: int, title: str, author: str | None) -> None:
        async with sem:
            desc: str | None = None
            source = "not_found"
            try:
                i13, i10 = isbns.get(book_id, (None, None))
                # 1. free: Google Books / Open Library search candidates
                found = await candidates.generate_candidates(
                    isbn13=i13, isbn10=i10, title=title, authors=author
                )
                blurbs = sorted(
                    (_clean(c.description) for c in found),
                    key=lambda s: len(s or ""),
                    reverse=True,
                )
                desc = next((b for b in blurbs if b and len(b) >= _MIN_LEN), None)
                # 2. free: Open Library's work-level blurb (search results don't carry it)
                if not desc and (i13 or i10) and ol_dead["count"] < 5:
                    try:
                        desc = await _open_library_description(http, i13 or i10)
                        ol_dead["count"] = 0
                    except httpx.ConnectError:
                        ol_dead["count"] += 1
                if desc:
                    source = "from_provider"
                # 3. opt-in, costs API credits: a model-written blurb, rationed
                elif ai is not None:
                    if ai_budget["left"] is not None and ai_budget["left"] <= 0:
                        deferred["n"] += 1
                    else:
                        if ai_budget["left"] is not None:
                            ai_budget["left"] -= 1
                        ai_text = _clean(await ai.describe(title, author))
                        if ai_text:
                            desc, source = ai_text, "from_ai"
            except Exception:
                logger.exception("description backfill failed for book %s", book_id)

            if desc:
                try:
                    async with async_session_factory() as write:
                        book = await write.get(Book, book_id)
                        if book is not None and book.description is None:
                            book.description = desc[:2000]
                            await write.commit()
                except Exception:
                    logger.exception("description backfill: write failed for book %s", book_id)
                    source = "not_found"

            counts[source] += 1
            if on_progress is not None:
                on_progress(counts, total)

    try:
        await asyncio.gather(*(one(bid, t, a) for bid, t, a in todo))
    finally:
        await http.aclose()
    # Books whose model blurb we skipped because the per-run cap ran out are
    # still "to go" — a re-run continues from here.
    counts["remaining"] += deferred["n"]
    logger.info("description backfill: %s", counts)
    return counts


class DescriptionService:
    def __init__(self) -> None:
        self._jobs: dict[str, DescriptionJobStatus] = {}

    def create_job(self) -> DescriptionJobStatus:
        job_id = str(uuid.uuid4())
        status = DescriptionJobStatus(job_id=job_id, status=DescriptionJobState.running)
        self._jobs[job_id] = status
        return status

    def get_status(self, job_id: str) -> DescriptionJobStatus | None:
        return self._jobs.get(job_id)

    async def run(self, job_id: str, *, use_ai: bool) -> None:
        def progress(counts: dict[str, int], total: int) -> None:
            done = counts["from_provider"] + counts["from_ai"] + counts["not_found"]
            self._jobs[job_id] = DescriptionJobStatus(
                job_id=job_id,
                status=DescriptionJobState.running,
                from_provider=counts["from_provider"],
                from_ai=counts["from_ai"],
                not_found=counts["not_found"],
                remaining=total - done,
            )

        counts = await backfill_descriptions(
            use_ai=use_ai,
            ai_cap=get_settings().ai_description_cap if use_ai else None,
            on_progress=progress,
        )
        self._jobs[job_id] = DescriptionJobStatus(
            job_id=job_id,
            status=DescriptionJobState.done,
            from_provider=counts["from_provider"],
            from_ai=counts["from_ai"],
            not_found=counts["not_found"],
            remaining=counts["remaining"],
        )


_description_service = DescriptionService()


def get_description_service() -> DescriptionService:
    return _description_service
