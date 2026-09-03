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
from app.data.models import Book, File, FileStatus, MetadataSource
from app.providers.drive.client import build_drive_service
from app.providers.drive.provider import DriveProvider

logger = logging.getLogger(__name__)

INDEX_FILENAME = "bookbrain-index.json"
INDEX_VERSION = 1
_JSON_MIME = "application/json"
_DESCRIPTION_CAP = 1500
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _plain_text(html: str | None) -> str | None:
    """EPUB descriptions come through as HTML fragments (`<b>…<BR>…`). The
    viewer renders plain text, so flatten tags to spaces and collapse
    whitespace here rather than shipping a sanitizer to the browser."""
    if not html:
        return None
    text = _WS_RE.sub(" ", _TAG_RE.sub(" ", html)).strip()
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
        }

    return {
        "version": INDEX_VERSION,
        "generatedAt": datetime.now(UTC).isoformat(),
        "count": len(books),
        "books": books,
    }


def _write_index(provider: DriveProvider, library_folder_id: str, payload: dict) -> None:
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
