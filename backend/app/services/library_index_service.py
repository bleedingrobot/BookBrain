"""Writes a small `bookbrain-index.json` into the Drive library root so the
static library-viewer has real structured metadata (author/series/description/
added-date, keyed by Drive file id) instead of scraping it back out of the
organized filename. The viewer reads it if present and falls back to filename
parsing when it isn't, so this is purely additive — nothing breaks if the
file is missing or stale."""

import asyncio
import json
import logging
import re
from datetime import UTC, datetime

from google.oauth2.credentials import Credentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.data.db import async_session_factory
from app.data.models import Book, File, FileStatus, Identifier, IdentifierType, MetadataSource
from app.providers.drive.client import build_drive_service
from app.providers.drive.provider import DriveProvider

logger = logging.getLogger(__name__)

INDEX_FILENAME = "bookbrain-index.json"
INDEX_VERSION = 3  # v2 adds per-book isbn; v3 adds the top-level `series` map

# prompts/25 Phase 3 — kept out of the main index (which is ~1MB) so the
# viewer only fetches "readers also liked" data when a book is actually
# expanded.
RECS_FILENAME = "bookbrain-recommendations.json"
RECS_VERSION = 1
_RECS_PER_BOOK = 12
_JSON_MIME = "application/json"
_DESCRIPTION_CAP = 1500
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
# Publisher boilerplate labels that lead a lot of EPUB <dc:description>
# blurbs — strip them so the viewer shows the actual copy, not "SUMMARY:".
_DESC_LABEL_RE = re.compile(
    r"^\s*(SUMMARY|SYNOPSIS|DESCRIPTION|OVERVIEW|ABOUT THE BOOK|PRODUCT DESCRIPTION|"
    r"PUBLISHER(?:'S| ?')? ?(?:DESCRIPTION|NOTE|SUMMARY)|FROM THE PUBLISHER|"
    r"BOOK DESCRIPTION|REVIEW)\s*[:\-–—]\s*",
    re.IGNORECASE,
)


def _plain_text(html: str | None) -> str | None:
    """EPUB descriptions come through as HTML fragments (`<b>…<BR>…`). The
    viewer renders plain text, so flatten tags to spaces and collapse
    whitespace here rather than shipping a sanitizer to the browser."""
    if not html:
        return None
    text = _WS_RE.sub(" ", _TAG_RE.sub(" ", html)).strip()
    text = _DESC_LABEL_RE.sub("", text, count=1).strip()
    if not text:
        return None
    return text[:_DESCRIPTION_CAP]


async def build_index_payload(session: AsyncSession) -> dict:
    files = (
        (
            await session.execute(
                select(File)
                .where(File.status == FileStatus.organised, File.book_id.is_not(None))
                .options(
                    selectinload(File.book).selectinload(Book.author),
                    selectinload(File.book).selectinload(Book.series),
                )
            )
        )
        .scalars()
        .all()
    )

    # Description: the book's own if set, else whatever the EPUB carried
    # (metadata_sources is keyed by file, not book).
    file_ids = [f.id for f in files]
    epub_desc: dict[int, str] = {}
    if file_ids:
        rows = (
            await session.execute(
                select(MetadataSource.file_id, MetadataSource.value).where(
                    MetadataSource.file_id.in_(file_ids),
                    MetadataSource.field_name == "description",
                )
            )
        ).all()
        for fid, value in rows:
            epub_desc.setdefault(fid, value)

    # ISBN per book — the viewer uses it to pull a cover thumbnail from the
    # Open Library covers API. Prefer ISBN-13.
    book_ids = {f.book_id for f in files}
    isbn_by_book: dict[int, str] = {}
    if book_ids:
        rows = (
            await session.execute(
                select(Identifier.book_id, Identifier.type, Identifier.value).where(
                    Identifier.book_id.in_(book_ids),
                    Identifier.type.in_([IdentifierType.isbn13, IdentifierType.isbn10]),
                )
            )
        ).all()
        for bid, itype, value in rows:
            if bid not in isbn_by_book or itype == IdentifierType.isbn13:
                isbn_by_book[bid] = value

    books: dict[str, dict] = {}
    for f in files:
        book = f.book
        assert book is not None  # guarded by the query's book_id filter
        books[f.drive_file_id] = {
            "title": book.canonical_title,
            "author": book.author.name if book.author else None,
            "series": book.series.name if book.series else None,
            "seriesNumber": book.series_number,
            "description": _plain_text(book.description or epub_desc.get(f.id)),
            "addedAt": f.discovered_at.isoformat() if f.discovered_at else None,
            "isbn": isbn_by_book.get(book.id),
        }

    # prompts/25 Phase 2 — Hardcover's canonical membership for each series in
    # the library, keyed by the same Series.name the per-book `series` field
    # uses. Only series with an actual match; the viewer falls back to its own
    # gap heuristic for everything else.
    series_map: dict[str, dict] = {}
    seen_series = {f.book.series for f in files if f.book and f.book.series}
    for s in seen_series:
        hc = s.hardcover_json if isinstance(s.hardcover_json, dict) else None
        if not hc or hc.get("match") == "none" or not hc.get("books"):
            continue
        series_map[s.name] = {
            "hardcoverId": hc.get("id"),
            "hardcoverName": hc.get("name"),
            "hardcoverSlug": hc.get("slug"),
            "primaryCount": hc.get("primaryCount"),
            "books": hc["books"],
        }

    return {
        "version": INDEX_VERSION,
        "generatedAt": datetime.now(UTC).isoformat(),
        "count": len(books),
        "books": books,
        "series": series_map,
    }


def _write_index(provider: DriveProvider, library_folder_id: str, payload: dict) -> None:
    # Record the covers/ folder id (if it exists) so the viewer can list it
    # without a separate lookup on every sync.
    covers = next(
        (f for f in provider.list_folders(library_folder_id) if f["name"] == "covers"), None
    )
    payload["coversFolder"] = covers["id"] if covers is not None else None

    data = json.dumps(payload, ensure_ascii=False, indent=0).encode("utf-8")
    existing = next(
        (f for f in provider.list_files_in_folder(library_folder_id) if f["name"] == INDEX_FILENAME),
        None,
    )
    if existing is not None:
        provider.update_file_content(
            existing["id"], new_name=INDEX_FILENAME, data=data, mime_type=_JSON_MIME
        )
    else:
        provider.upload_new_file(
            name=INDEX_FILENAME, data=data, parent_id=library_folder_id, mime_type=_JSON_MIME
        )


async def build_recommendations_payload(session: AsyncSession) -> dict:
    """`bookbrain-recommendations.json` — "readers also liked" per organised
    file, from Book.hardcover_json (prompts/25 Phase 3 / hardcover_recs_service).
    Keyed by drive_file_id so the viewer joins it to the row it already has."""
    files = (
        (
            await session.execute(
                select(File)
                .where(File.status == FileStatus.organised, File.book_id.is_not(None))
                .options(selectinload(File.book))
            )
        )
        .scalars()
        .all()
    )

    books: dict[str, list[dict]] = {}
    for f in files:
        hc = f.book.hardcover_json if f.book and isinstance(f.book.hardcover_json, dict) else None
        similar = (hc or {}).get("similar") or []
        own_title = _plain_text(f.book.canonical_title) if f.book else None
        recs = [
            {"title": r["title"], "author": r.get("author"), "isbn13": r.get("isbn13")}
            for r in similar
            if isinstance(r, dict)
            and isinstance(r.get("title"), str)
            and (not own_title or r["title"].strip().lower() != own_title.strip().lower())
        ][:_RECS_PER_BOOK]
        if recs:
            books[f.drive_file_id] = recs

    return {
        "version": RECS_VERSION,
        "generatedAt": datetime.now(UTC).isoformat(),
        "count": len(books),
        "books": books,
    }


def _write_json_file(
    provider: DriveProvider, library_folder_id: str, name: str, payload: dict
) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=0).encode("utf-8")
    existing = next(
        (f for f in provider.list_files_in_folder(library_folder_id) if f["name"] == name), None
    )
    if existing is not None:
        provider.update_file_content(existing["id"], new_name=name, data=data, mime_type=_JSON_MIME)
    else:
        provider.upload_new_file(
            name=name, data=data, parent_id=library_folder_id, mime_type=_JSON_MIME
        )


async def regenerate_recommendations(
    creds: Credentials | None, library_folder_id: str | None
) -> int | None:
    """Write bookbrain-recommendations.json. Best-effort, never raises into
    the caller (same contract as regenerate_library_index)."""
    if creds is None or not library_folder_id:
        return None
    try:
        async with async_session_factory() as session:
            payload = await build_recommendations_payload(session)
        provider = DriveProvider(build_drive_service(creds))
        await asyncio.to_thread(
            _write_json_file, provider, library_folder_id, RECS_FILENAME, payload
        )
        logger.info("recommendations refreshed: %d books", payload["count"])
        return payload["count"]
    except Exception:
        logger.exception("recommendations refresh failed")
        return None


async def regenerate_library_index(
    creds: Credentials | None, library_folder_id: str | None
) -> int | None:
    """Best-effort: called at the tail of organize / rebuild. Returns the
    book count written, or None if it couldn't run (no creds / no library
    folder). Never raises into the caller — a failed index refresh must not
    fail the organize or rebuild job that triggered it."""
    if creds is None or not library_folder_id:
        return None
    try:
        async with async_session_factory() as session:
            payload = await build_index_payload(session)
        provider = DriveProvider(build_drive_service(creds))
        await asyncio.to_thread(_write_index, provider, library_folder_id, payload)
        logger.info("library index refreshed: %d books", payload["count"])
        return payload["count"]
    except Exception:
        logger.exception("library index refresh failed")
        return None
