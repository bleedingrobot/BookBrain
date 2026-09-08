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
from app.data.models import (
    Author,
    Book,
    File,
    FileStatus,
    Identifier,
    IdentifierType,
    MetadataSource,
)
from app.providers.drive.client import build_drive_service
from app.providers.drive.provider import DriveProvider
from app.services.text_match import normalize_person_name, normalize_title

logger = logging.getLogger(__name__)

INDEX_FILENAME = "bookbrain-index.json"
# v2 adds per-book isbn; v3 adds the top-level `series` map; v4 adds per-book
# `meta` (Hardcover rating / pages / category / genres — prompts/26 Part B);
# v5 adds per-entry `isbn13` inside the `series` map's book lists (so the
# viewer can cover a not-yet-owned release — prompts/27 Part 1).
INDEX_VERSION = 5

# Per-book Hardcover metadata surfaced to the viewer as badges + a genre
# facet. `description` stays out — the viewer already gets a blurb from the
# `description` field; Hardcover's copy is folded in server-side instead
# (description_service, prompts/26 Part C).
_META_KEYS = ("rating", "ratingsCount", "pages", "category", "literaryType", "genres", "moods")

# prompts/25 Phase 3 — kept out of the main index (which is ~1MB) so the
# viewer only fetches "readers also liked" data when a book is actually
# expanded.
RECS_FILENAME = "bookbrain-recommendations.json"
RECS_VERSION = 1
_RECS_PER_BOOK = 12
_JSON_MIME = "application/json"

# prompts/27 Part 2 — "From authors you read": Hardcover's recent + near-future
# books for every author in the library, minus what's already owned or
# wishlisted. Its own sidecar (like recommendations) so it's a lazy fetch.
NEW_RELEASES_FILENAME = "bookbrain-new-releases.json"
NEW_RELEASES_VERSION = 1
_NEW_RELEASES_CAP = 60
_WISHLIST_FILENAME = "bookbrain-wishlist.json"
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


def _book_meta(hardcover_json: object) -> dict:
    """The subset of `Book.hardcover_json.meta` (written by hardcover_recs_service)
    the viewer shows — dropping unset keys and empty lists so the index stays
    lean."""
    meta = hardcover_json.get("meta") if isinstance(hardcover_json, dict) else None
    if not isinstance(meta, dict):
        return {}
    out: dict = {}
    for key in _META_KEYS:
        value = meta.get(key)
        if value in (None, "", []):
            continue
        out[key] = value
    return out


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
        entry = {
            "title": book.canonical_title,
            "author": book.author.name if book.author else None,
            "series": book.series.name if book.series else None,
            "seriesNumber": book.series_number,
            "description": _plain_text(book.description or epub_desc.get(f.id)),
            "addedAt": f.discovered_at.isoformat() if f.discovered_at else None,
            "isbn": isbn_by_book.get(book.id),
        }
        meta = _book_meta(book.hardcover_json)
        if meta:
            entry["meta"] = meta
        books[f.drive_file_id] = entry

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


def _norm_key(title: str | None, author: str | None) -> str:
    return f"{normalize_title(title)}|{normalize_person_name(author)}"


async def build_new_releases_payload(
    session: AsyncSession,
    wishlist_keys: set[str],
    global_raw: list[dict] | None = None,
) -> dict:
    """`bookbrain-new-releases.json` — every organised author's Hardcover
    recent + near-future books (Author.hardcover_json, from
    hardcover_new_releases_service), split into `recent` / `upcoming` and with
    anything already owned or on the wishlist removed. `global_raw` (Part 3)
    is Hardcover's overall most-anticipated list, filtered the same way and
    de-duped against the author feed, written under `global`."""
    rows = (
        (
            await session.execute(
                select(Author)
                .join(Book, Book.author_id == Author.id)
                .join(File, File.book_id == Book.id)
                .where(File.status == FileStatus.organised)
                .distinct()
            )
        )
        .scalars()
        .all()
    )

    owned_titles = (
        await session.execute(
            select(Book.canonical_title, Author.name)
            .join(File, File.book_id == Book.id)
            .join(Author, Author.id == Book.author_id, isouter=True)
            .where(File.status == FileStatus.organised)
        )
    ).all()
    owned_keys = {_norm_key(t, a) for t, a in owned_titles}

    today = datetime.now(UTC).date().isoformat()
    recent: list[dict] = []
    upcoming: list[dict] = []
    seen: set[str] = set()
    for author in rows:
        hc = author.hardcover_json if isinstance(author.hardcover_json, dict) else None
        for book in (hc or {}).get("books") or []:
            if not isinstance(book, dict) or not isinstance(book.get("title"), str):
                continue
            key = _norm_key(book["title"], author.name)
            if key in seen or key in owned_keys or key in wishlist_keys:
                continue
            seen.add(key)
            item = {
                "title": book["title"],
                "author": author.name,
                "isbn13": book.get("isbn13"),
                "releaseDate": book.get("releaseDate"),
                "genres": book.get("genres") or [],
                "source": "author",
            }
            (upcoming if (book.get("releaseDate") or "") > today else recent).append(item)

    global_items: list[dict] = []
    for book in global_raw or []:
        if not isinstance(book, dict) or not isinstance(book.get("title"), str):
            continue
        key = _norm_key(book["title"], book.get("author"))
        if key in seen or key in owned_keys or key in wishlist_keys:
            continue
        seen.add(key)
        global_items.append(
            {
                "title": book["title"],
                "author": book.get("author"),
                "isbn13": book.get("isbn13"),
                "releaseDate": book.get("releaseDate"),
                "genres": book.get("genres") or [],
                "source": "global",
            }
        )

    recent.sort(key=lambda b: b.get("releaseDate") or "", reverse=True)
    upcoming.sort(key=lambda b: b.get("releaseDate") or "")
    global_items.sort(key=lambda b: b.get("releaseDate") or "")
    return {
        "version": NEW_RELEASES_VERSION,
        "generatedAt": datetime.now(UTC).isoformat(),
        "recent": recent[:_NEW_RELEASES_CAP],
        "upcoming": upcoming[:_NEW_RELEASES_CAP],
        "global": global_items[:_NEW_RELEASES_CAP],
    }


def _read_wishlist_keys(provider: DriveProvider, library_folder_id: str) -> set[str]:
    """Normalised title|author keys for everything on the Drive wishlist, so
    the new-releases feed doesn't re-surface a book someone already asked
    for. Best-effort — no wishlist file yet just means an empty set."""
    try:
        found = next(
            (
                f
                for f in provider.list_files_in_folder(library_folder_id)
                if f["name"] == _WISHLIST_FILENAME
            ),
            None,
        )
        if found is None:
            return set()
        raw = json.loads(provider.download_file(found["id"]).decode("utf-8"))
        return {
            _norm_key(i.get("title"), i.get("author"))
            for i in raw.get("items") or []
            if isinstance(i, dict) and isinstance(i.get("title"), str)
        }
    except Exception:
        logger.exception("new-releases: could not read the wishlist for exclusion")
        return set()


async def regenerate_new_releases(
    creds: Credentials | None, library_folder_id: str | None
) -> dict | None:
    """Write bookbrain-new-releases.json. Best-effort, never raises into the
    caller (same contract as regenerate_recommendations)."""
    if creds is None or not library_folder_id:
        return None
    try:
        from app.services import hardcover_new_releases_service

        provider = DriveProvider(build_drive_service(creds))
        wishlist_keys = await asyncio.to_thread(_read_wishlist_keys, provider, library_folder_id)
        global_raw = await hardcover_new_releases_service.fetch_global_anticipated()
        async with async_session_factory() as session:
            payload = await build_new_releases_payload(session, wishlist_keys, global_raw)
        await asyncio.to_thread(
            _write_json_file, provider, library_folder_id, NEW_RELEASES_FILENAME, payload
        )
        total = len(payload["recent"]) + len(payload["upcoming"])
        logger.info("new releases refreshed: %d books", total)
        return total
    except Exception:
        logger.exception("new releases refresh failed")
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
