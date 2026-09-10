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
from sqlalchemy import func, select
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
# viewer can cover a not-yet-owned release — prompts/27 Part 1);
# v6 adds per-book `contentWarnings` to `meta` and surfaces `moods` in the
# viewer (prompts/31 Part A); v7 adds `listsCount` (E1) + `published` /
# `audioHours` (Part H).
INDEX_VERSION = 7

# Per-book Hardcover metadata surfaced to the viewer as badges + a genre
# facet. `description` stays out — the viewer already gets a blurb from the
# `description` field; Hardcover's copy is folded in server-side instead
# (description_service, prompts/26 Part C).
_META_KEYS = (
    "rating",
    "ratingsCount",
    "pages",
    "category",
    "literaryType",
    "genres",
    "moods",
    "contentWarnings",
    "listsCount",
    "published",
    "audioHours",
)

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
NEW_RELEASES_VERSION = 2  # v2 adds trending[] (prompts/31 Part G)

# prompts/32 — the SFF news feed sidecar.
NEWS_FILENAME = "bookbrain-news.json"
NEWS_VERSION = 1

# prompts/31 Part F — Hardcover "Prompts" → the books James owns that answer them.
PROMPTS_FILENAME = "bookbrain-prompts.json"
PROMPTS_VERSION = 1

# prompts/31 Part E2 — curated Hardcover lists → wishlist candidates.
LISTS_FILENAME = "bookbrain-lists.json"
LISTS_VERSION = 1
_NEW_RELEASES_CAP = 60
_WISHLIST_FILENAME = "bookbrain-wishlist.json"

# prompts/29 — one binary sidecar of int8-quantised sentence embeddings for
# the viewer's semantic search. A 4-byte LE uint32 header length, then a JSON
# header, then `count * dim` int8 bytes (component * 127, clamped) row-major
# in `ids` order.
EMBEDDINGS_FILENAME = "bookbrain-embeddings.bin"
EMBEDDINGS_VERSION = 1
_OCTET_MIME = "application/octet-stream"

# prompts/30 — the account owner's Hardcover reading status/rating per
# library book. Its own sidecar (lazy fetch). USER data, not catalogue — see
# hardcover_reading_service's licence note.
READING_FILENAME = "bookbrain-reading.json"
READING_VERSION = 4  # v2 wantUnowned[]; v3 goal{}; v4 per-book progress (Part I)
# prompts/30 Phase 3 — the viewer queues "mark read" here; a sync applies it
# to Hardcover then drops the applied entries.
READING_PENDING_FILENAME = "bookbrain-reading-pending.json"
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


def _write_bytes_file(
    provider: DriveProvider,
    library_folder_id: str,
    name: str,
    data: bytes,
    mime_type: str = _JSON_MIME,
) -> None:
    existing = next(
        (f for f in provider.list_files_in_folder(library_folder_id) if f["name"] == name), None
    )
    if existing is not None:
        provider.update_file_content(existing["id"], new_name=name, data=data, mime_type=mime_type)
    else:
        provider.upload_new_file(
            name=name, data=data, parent_id=library_folder_id, mime_type=mime_type
        )


def _write_json_file(
    provider: DriveProvider, library_folder_id: str, name: str, payload: dict
) -> None:
    _write_bytes_file(
        provider,
        library_folder_id,
        name,
        json.dumps(payload, ensure_ascii=False, indent=0).encode("utf-8"),
    )


def _read_json_file(provider: DriveProvider, library_folder_id: str, name: str) -> dict:
    """The current contents of a sidecar, or `{}` if it's missing / unreadable."""
    try:
        found = next(
            (f for f in provider.list_files_in_folder(library_folder_id) if f["name"] == name), None
        )
        if found is None:
            return {}
        return json.loads(provider.download_file(found["id"]).decode("utf-8"))
    except Exception:
        logger.exception("could not read sidecar %s", name)
        return {}


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


async def build_embeddings_payload(session: AsyncSession) -> bytes:
    """`bookbrain-embeddings.bin` — every organised book that has an embedding
    (from embedding_service), int8-quantised. 4-byte LE header length, JSON
    header, then the int8 vector block."""
    import numpy as np

    rows = (
        await session.execute(
            select(File.drive_file_id, Book.embedding, Book.embedding_model)
            .join(Book, Book.id == File.book_id)
            .where(File.status == FileStatus.organised, Book.embedding.is_not(None))
        )
    ).all()

    ids: list[str] = []
    vectors: list[np.ndarray] = []
    model = None
    dim = 0
    for drive_id, blob, emb_model in rows:
        vec = np.frombuffer(blob, dtype=np.float32)
        if dim == 0:
            dim = int(vec.shape[0])
        if vec.shape[0] != dim:
            continue  # a stale row from a different model — skip, next refresh fixes it
        ids.append(drive_id)
        vectors.append(vec)
        model = model or emb_model

    header = json.dumps(
        {
            "version": EMBEDDINGS_VERSION,
            "generatedAt": datetime.now(UTC).isoformat(),
            "model": model,
            "dim": dim,
            "count": len(ids),
            "ids": ids,
        },
        ensure_ascii=False,
    ).encode("utf-8")

    if vectors:
        block = np.clip(np.round(np.stack(vectors) * 127.0), -127, 127).astype(np.int8).tobytes()
    else:
        block = b""
    return len(header).to_bytes(4, "little") + header + block


async def regenerate_embeddings(
    creds: Credentials | None, library_folder_id: str | None
) -> int | None:
    """Write bookbrain-embeddings.bin. Best-effort, never raises (same
    contract as regenerate_recommendations)."""
    if creds is None or not library_folder_id:
        return None
    try:
        async with async_session_factory() as session:
            data = await build_embeddings_payload(session)
        provider = DriveProvider(build_drive_service(creds))
        await asyncio.to_thread(
            _write_bytes_file, provider, library_folder_id, EMBEDDINGS_FILENAME, data, _OCTET_MIME
        )
        count = int.from_bytes(data[:4], "little")
        header = json.loads(data[4 : 4 + count])
        logger.info("embeddings sidecar: %d books", header["count"])
        return int(header["count"])
    except Exception:
        logger.exception("embeddings sidecar refresh failed")
        return None


def _norm_key(title: str | None, author: str | None) -> str:
    return f"{normalize_title(title)}|{normalize_person_name(author)}"


async def match_hardcover_book_ids(
    session: AsyncSession, hc_ids: set[int]
) -> dict[int, str]:
    """Hardcover book id → the Drive file id of the organised book we own with
    that id, for the `hc_ids` given. Uses `Book.hardcover_json.id` (written by
    hardcover_recs_service), so it's exact — no ISBN/title fuzz. Shared by the
    lists (E2) and prompts (F) sidecars, which both deal in Hardcover ids."""
    if not hc_ids:
        return {}
    hc_id_col = func.json_extract(Book.hardcover_json, "$.id")
    rows = (
        await session.execute(
            select(File.drive_file_id, hc_id_col)
            .join(Book, Book.id == File.book_id)
            .where(
                File.status == FileStatus.organised,
                Book.hardcover_json.is_not(None),
                hc_id_col.in_(hc_ids),
            )
        )
    ).all()
    out: dict[int, str] = {}
    for drive_id, hc_id in rows:
        if isinstance(hc_id, int):
            out.setdefault(hc_id, drive_id)
    return out


async def build_new_releases_payload(
    session: AsyncSession,
    wishlist_keys: set[str],
    global_raw: list[dict] | None = None,
    trending_raw: list[dict] | None = None,
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

    # prompts/31 Part G — trending books, in Hardcover's order. Kept whole
    # (owned ones get an "In library" badge viewer-side); just skip anything
    # already in the author/global feeds.
    trending: list[dict] = []
    for book in trending_raw or []:
        if not isinstance(book, dict) or not isinstance(book.get("title"), str):
            continue
        key = _norm_key(book["title"], book.get("author"))
        if key in seen:
            continue
        seen.add(key)
        trending.append(
            {
                "title": book["title"],
                "author": book.get("author"),
                "isbn13": book.get("isbn13"),
                "releaseDate": None,
                "genres": book.get("genres") or [],
                "source": "trending",
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
        "trending": trending[:_NEW_RELEASES_CAP],
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
        trending_raw = await hardcover_new_releases_service.fetch_trending()
        async with async_session_factory() as session:
            payload = await build_new_releases_payload(
                session, wishlist_keys, global_raw, trending_raw
            )
        await asyncio.to_thread(
            _write_json_file, provider, library_folder_id, NEW_RELEASES_FILENAME, payload
        )
        total = len(payload["recent"]) + len(payload["upcoming"]) + len(payload["global"])
        logger.info(
            "new releases refreshed: %d books (%d recent, %d upcoming, %d global, %d trending)",
            total,
            len(payload["recent"]),
            len(payload["upcoming"]),
            len(payload["global"]),
            len(payload["trending"]),
        )
        return total
    except Exception:
        logger.exception("new releases refresh failed")
        return None


async def regenerate_news(
    creds: Credentials | None, library_folder_id: str | None
) -> int | None:
    """prompts/32 — fetch the SFF news feeds and write bookbrain-news.json.
    Best-effort, never raises. The fetch needs no creds; the Drive write does."""
    if creds is None or not library_folder_id:
        return None
    try:
        from app.services import sff_news_service

        items = await sff_news_service.fetch_news()
        if not items:
            return None
        payload = {
            "version": NEWS_VERSION,
            "generatedAt": datetime.now(UTC).isoformat(),
            "items": items,
        }
        provider = DriveProvider(build_drive_service(creds))
        await asyncio.to_thread(
            _write_json_file, provider, library_folder_id, NEWS_FILENAME, payload
        )
        logger.info("sff news sidecar: %d items", len(items))
        return len(items)
    except Exception:
        logger.exception("sff news refresh failed")
        return None


async def build_prompts_payload(session: AsyncSession, prompts_raw: list[dict]) -> dict:
    """`bookbrain-prompts.json` — each Hardcover prompt with the Drive file ids
    of the books James owns that answer it. Keeps only prompts with >= 2
    owned answers (a one-book match isn't interesting)."""
    all_ids = {i for p in prompts_raw for i in p.get("bookIds") or []}
    owned = await match_hardcover_book_ids(session, all_ids)

    prompts: list[dict] = []
    for p in prompts_raw:
        drive_ids = [owned[i] for i in p.get("bookIds") or [] if i in owned]
        # dedupe, keep order
        seen: set[str] = set()
        drive_ids = [d for d in drive_ids if not (d in seen or seen.add(d))]
        if len(drive_ids) < 2:
            continue
        prompts.append(
            {
                "question": p["question"],
                "slug": p.get("slug"),
                "driveIds": drive_ids,
            }
        )
    prompts.sort(key=lambda p: len(p["driveIds"]), reverse=True)
    return {
        "version": PROMPTS_VERSION,
        "generatedAt": datetime.now(UTC).isoformat(),
        "prompts": prompts,
    }


async def regenerate_prompts(
    creds: Credentials | None, library_folder_id: str | None
) -> int | None:
    """prompts/31 Part F. Best-effort, never raises. No-op without a token."""
    if creds is None or not library_folder_id:
        return None
    try:
        from app.services import hardcover_prompts_service

        prompts_raw = await hardcover_prompts_service.fetch_prompts()
        if not prompts_raw:
            return None
        async with async_session_factory() as session:
            payload = await build_prompts_payload(session, prompts_raw)
        provider = DriveProvider(build_drive_service(creds))
        await asyncio.to_thread(
            _write_json_file, provider, library_folder_id, PROMPTS_FILENAME, payload
        )
        logger.info("prompts sidecar: %d your-library questions", len(payload["prompts"]))
        return len(payload["prompts"])
    except Exception:
        logger.exception("prompts sidecar refresh failed")
        return None


async def _owned_hardcover_ids(session: AsyncSession) -> set[int]:
    hc_id_col = func.json_extract(Book.hardcover_json, "$.id")
    rows = (
        await session.execute(
            select(hc_id_col)
            .join(File, File.book_id == Book.id)
            .where(File.status == FileStatus.organised, hc_id_col.is_not(None))
            .distinct()
        )
    ).all()
    return {r[0] for r in rows if isinstance(r[0], int)}


async def build_lists_payload(
    session: AsyncSession, result: dict, wishlist_keys: set[str]
) -> dict:
    """`bookbrain-lists.json` — curated Hardcover lists James part-owns, plus
    the not-yet-owned books on them as wishlist candidates (minus anything
    already owned by title/author or on the wishlist)."""
    owned_titles = (
        await session.execute(
            select(Book.canonical_title, Author.name)
            .join(File, File.book_id == Book.id)
            .join(Author, Author.id == Book.author_id, isouter=True)
            .where(File.status == FileStatus.organised)
        )
    ).all()
    owned_keys = {_norm_key(t, a) for t, a in owned_titles}

    candidates = [
        c
        for c in result.get("candidates") or []
        if _norm_key(c.get("title"), c.get("author")) not in owned_keys
        and _norm_key(c.get("title"), c.get("author")) not in wishlist_keys
    ]
    return {
        "version": LISTS_VERSION,
        "generatedAt": datetime.now(UTC).isoformat(),
        "lists": result.get("lists") or [],
        "candidates": candidates,
    }


async def regenerate_lists(
    creds: Credentials | None, library_folder_id: str | None
) -> int | None:
    """prompts/31 Part E2. Best-effort, never raises. No-op without a token."""
    if creds is None or not library_folder_id:
        return None
    try:
        from app.services import hardcover_lists_service

        provider = DriveProvider(build_drive_service(creds))
        wishlist_keys = await asyncio.to_thread(_read_wishlist_keys, provider, library_folder_id)
        async with async_session_factory() as session:
            owned_ids = await _owned_hardcover_ids(session)
            result = await hardcover_lists_service.fetch_list_candidates(owned_ids)
            if not result.get("candidates"):
                return None
            payload = await build_lists_payload(session, result, wishlist_keys)
        await asyncio.to_thread(
            _write_json_file, provider, library_folder_id, LISTS_FILENAME, payload
        )
        logger.info(
            "lists sidecar: %d lists, %d candidates",
            len(payload["lists"]),
            len(payload["candidates"]),
        )
        return len(payload["candidates"])
    except Exception:
        logger.exception("lists sidecar refresh failed")
        return None


async def build_reading_payload(
    session: AsyncSession, reading_rows: list[dict], reader: str, goal: dict | None = None
) -> dict:
    """`bookbrain-reading.json` — the Hardcover reading rows matched to
    organised library files (ISBN-13 first, then normalised title+author).
    `unmatched` is counts only for read/reading (don't leak what he's read
    that isn't owned), but `wantUnowned` carries the *want-to-read* rows that
    aren't in the library — those are wishlist candidates, which is the whole
    point of listing them (prompts/30 want↔wishlist)."""
    files = (
        await session.execute(
            select(File.drive_file_id, Book.id, Book.canonical_title, Author.name)
            .join(Book, Book.id == File.book_id)
            .outerjoin(Author, Author.id == Book.author_id)
            .where(File.status == FileStatus.organised, File.book_id.is_not(None))
        )
    ).all()
    book_ids = {bid for _, bid, _, _ in files}
    isbn_to_drive: dict[str, str] = {}
    if book_ids:
        for bid, value in (
            await session.execute(
                select(Identifier.book_id, Identifier.value).where(
                    Identifier.book_id.in_(book_ids),
                    Identifier.type.in_([IdentifierType.isbn13, IdentifierType.isbn10]),
                )
            )
        ).all():
            drive = next((d for d, b, _, _ in files if b == bid), None)
            if drive:
                isbn_to_drive.setdefault(value, drive)
    key_to_drive = {_norm_key(t, a): d for d, _, t, a in files}

    books: dict[str, dict] = {}
    unmatched = {"read": 0, "want": 0, "reading": 0}
    want_unowned: list[dict] = []
    for row in reading_rows:
        drive = isbn_to_drive.get(row.get("isbn13") or "") or key_to_drive.get(
            _norm_key(row.get("title"), row.get("author"))
        )
        if drive is None:
            if row.get("status") in unmatched:
                unmatched[row["status"]] += 1
            if (
                row.get("status") == "want"
                and isinstance(row.get("title"), str)
                and len(want_unowned) < 300
            ):
                want_unowned.append(
                    {
                        "title": row["title"],
                        "author": row.get("author"),
                        "isbn13": row.get("isbn13"),
                    }
                )
            continue
        entry = {"status": row.get("status")}
        if row.get("rating") is not None:
            entry["rating"] = row["rating"]
        if row.get("readDate"):
            entry["readDate"] = row["readDate"]
        if row.get("readCount"):
            entry["readCount"] = row["readCount"]
        # prompts/31 Part I — Hardcover's reader position, only worth showing
        # while the book is genuinely mid-read.
        prog = row.get("progress")
        if isinstance(prog, int | float) and 0.0 < prog < 0.98:
            entry["progress"] = round(float(prog), 3)
        books.setdefault(drive, entry)

    payload = {
        "version": READING_VERSION,
        "generatedAt": datetime.now(UTC).isoformat(),
        "reader": reader,
        "count": len(books),
        "unmatched": unmatched,
        "wantUnowned": want_unowned,
        "books": books,
    }
    if goal is not None:
        payload["goal"] = goal
    return payload


def _read_pending_reading(provider: DriveProvider, library_folder_id: str) -> list[dict]:
    """The viewer's queued reading-status changes. Best-effort."""
    try:
        found = next(
            (
                f
                for f in provider.list_files_in_folder(library_folder_id)
                if f["name"] == READING_PENDING_FILENAME
            ),
            None,
        )
        if found is None:
            return []
        raw = json.loads(provider.download_file(found["id"]).decode("utf-8"))
        return [
            c
            for c in raw.get("changes") or []
            if isinstance(c, dict) and isinstance(c.get("driveFileId"), str)
        ]
    except Exception:
        logger.exception("reading: could not read the pending-changes queue")
        return []


async def regenerate_reading(
    creds: Credentials | None, library_folder_id: str | None
) -> int | None:
    """prompts/30. Two-way: apply any queued write-back changes to Hardcover,
    drop the applied ones from the Drive queue, then re-pull the owner's
    Hardcover reading data and write bookbrain-reading.json. Best-effort,
    never raises. No-op without a Hardcover token."""
    if creds is None or not library_folder_id:
        return None
    try:
        from app.core.config import get_settings
        from app.services import hardcover_reading_service

        provider = DriveProvider(build_drive_service(creds))

        # Phase 3 — flush the viewer's queued "mark read" etc. to Hardcover first.
        pending = await asyncio.to_thread(_read_pending_reading, provider, library_folder_id)
        if pending:
            result = await hardcover_reading_service.apply_pending(pending)
            done = set(result["applied"])
            left = [c for c in pending if c["driveFileId"] not in done]
            await asyncio.to_thread(
                _write_json_file,
                provider,
                library_folder_id,
                READING_PENDING_FILENAME,
                {"version": 1, "changes": left},
            )
            logger.info("reading write-back: %d applied, %d left queued", len(done), len(left))

        rows, username, complete = await hardcover_reading_service.fetch_reading()
        if not rows:
            return None
        goal = await hardcover_reading_service.fetch_goal()
        reader = (get_settings().hardcover_reader_name or "").strip() or username or "reader"
        async with async_session_factory() as session:
            payload = await build_reading_payload(session, rows, reader, goal)

        # A PARTIAL pull (a page failed) that has fewer rows than the sidecar we
        # already have would make read books look unread — worse than stale.
        # Only overwrite on a complete pull, or when the new one isn't smaller.
        if not complete:
            existing = await asyncio.to_thread(
                _read_json_file, provider, library_folder_id, READING_FILENAME
            )
            prev_rows = existing.get("count", 0) + sum((existing.get("unmatched") or {}).values())
            new_rows = payload["count"] + sum(payload["unmatched"].values())
            if existing and new_rows < prev_rows:
                logger.warning(
                    "reading sidecar: partial pull (%d < %d rows) — keeping the existing file",
                    new_rows,
                    prev_rows,
                )
                return None

        await asyncio.to_thread(
            _write_json_file, provider, library_folder_id, READING_FILENAME, payload
        )
        logger.info(
            "reading sidecar: %d matched, %s unmatched%s",
            payload["count"],
            payload["unmatched"],
            "" if complete else " (partial — but not smaller)",
        )
        return payload["count"]
    except Exception:
        logger.exception("reading sidecar refresh failed")
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
