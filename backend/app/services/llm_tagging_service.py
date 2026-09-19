"""prompts/38 — a slow-drip background job that reads each organised book's
actual full text on a local Ollama instance (the gaming PC "JamesGaming",
reached over Tailscale — never the main identification pipeline) and derives
genres/moods/themes/representation/content-warnings plus two descriptions
(a spoiler-free short blurb, a full-length summary that may include the
ending), writing the result to `Book.llm_tags_json.full`.

Deliberately unobtrusive: one small unit of work per scheduler tick
(`app.jobs.scheduler.sync_llm_tagging_schedule`), only inside allowed time
windows, only while Ollama answers, never competing with James for the GPU.

The whole book is map-reduced, since a novel is typically far bigger than a
local model's context window: each tick maps exactly one chunk (a
lightweight "what happens here + what evidence is in this section" call);
once every chunk is mapped, one more call reduces the chunk summaries into
the final schema. Progress lives entirely in `Book.llm_tags_json.full`
(status/chunksDone/chunkResults), so a window closing mid-book loses no
work — the next tick resumes at the next chunk. At most one book's pass is
ever in flight at a time.

An excerpt-only pass (opening/middle/ending, one call, done) originally ran
alongside this to compare against — see prompts/38-llm-tagging.md's update
note. James compared the two on a real book and preferred the full-text
read by a clear margin (richer tags, more self-aware confidence notes), so
the excerpt path was removed rather than kept running at 1/34th the cost for
a result he'd never use. `validate_tag_result`'s shape still matches what
that pass produced, so any book's already-stored `llm_tags_json.excerpt` is
harmless leftover data, just nothing new writes it any more.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import Settings, get_settings
from app.core.settings_keys import LLM_TAGGING_ENABLED
from app.data.db import async_session_factory
from app.data.models import Book, File, FileStatus
from app.data.repositories.settings_repository import SettingsRepository
from app.providers.ai.ollama_client import OllamaBadResponse, OllamaClient, OllamaUnavailable
from app.providers.epub.errors import EpubParseError, EpubParseTimeoutError
from app.providers.epub.parser import extract_full_text_documents_safely
from app.services import theme_dedup_service
from app.services.auth_service import get_auth_service
from app.services.llm_tagging_download import download_file_with_hard_timeout

logger = logging.getLogger(__name__)

# Retry a failed book/step after this long, rather than hammering it every
# tick — malformed output or a bad extraction usually isn't transient.
_RETRY_BACKOFF = timedelta(hours=24)

# Allowed windows are in James's own local time (`settings.llm_tagging_timezone`),
# NOT the server's system clock — this box runs on UTC, which is 12 hours off
# from his actual timezone, so comparing against `datetime.now()` server-side
# would get every window exactly backwards (his evening reads as his workday
# and vice versa). From 9pm through to 8am every night, plus weekday office
# hours — never daytime weekends, and never 8am-9pm on a weekday outside the
# office-hours slot, when the gaming PC is presumably in actual use.
_OVERNIGHT_START_HOUR = 21  # 9pm
_OVERNIGHT_END_HOUR = 8  # 8am, any day
_WORKDAY_START_HOUR = 9  # 9am
_WORKDAY_END_HOUR = 15  # 3pm, Mon-Fri only

# Roughly 4 chars/token for English prose — good enough for sizing chunks;
# Ollama's own tokenizer will differ slightly, and num_ctx has headroom.
_CHARS_PER_TOKEN = 4
# System + user prompt scaffolding, schema instructions, and the model's own
# JSON output all eat into num_ctx before any book text does.
_PROMPT_OVERHEAD_TOKENS = 1500
_MIN_CHUNK_CHARS = 2000

_MAX_LIST_ITEMS = 12
_STR_CAPS = {
    "ageRating": 40,
    "confidenceNotes": 500,
    "shortDescription": 600,
    "longSummary": 4000,
}
_TAG_LIST_KEYS = ("genres", "moods", "themes", "representation", "contentWarnings")


def _clean_list(key: str, value: list) -> list[str]:
    items = [str(v).strip() for v in value if str(v).strip()]
    if key == "themes":
        # prompts/47 A.2 — the prompt asks for specific phrases; this catches
        # the bare "identity"/"survival" the model still slips in.
        items = [t for t in items if not theme_dedup_service.is_generic_theme(t)]
    return items[:_MAX_LIST_ITEMS]


def _content_budget_chars(num_ctx: int) -> int:
    return max(_MIN_CHUNK_CHARS, (num_ctx - _PROMPT_OVERHEAD_TOKENS) * _CHARS_PER_TOKEN)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _in_allowed_window(now: datetime) -> bool:
    hour = now.hour
    if hour >= _OVERNIGHT_START_HOUR or hour < _OVERNIGHT_END_HOUR:
        return True
    return now.weekday() < 5 and _WORKDAY_START_HOUR <= hour < _WORKDAY_END_HOUR


def _local_now(settings: Settings) -> datetime:
    """Wall-clock time in `settings.llm_tagging_timezone`, not the server's
    own — see the module-level note above `_OVERNIGHT_START_HOUR`. Falls
    back to UTC (logged once per bad tick, harmlessly conservative) if the
    configured zone name doesn't exist."""
    try:
        return datetime.now(ZoneInfo(settings.llm_tagging_timezone))
    except ZoneInfoNotFoundError:
        logger.warning(
            "llm tagging: unknown llm_tagging_timezone %r, falling back to UTC",
            settings.llm_tagging_timezone,
        )
        return datetime.now(UTC)


def chunk_documents(docs: list[str], *, target_chars: int) -> list[str]:
    """Group spine documents into chunks near `target_chars`, in order.
    A single document bigger than the budget on its own is hard-split."""
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for doc in docs:
        if current and current_len + len(doc) > target_chars:
            chunks.append("\n\n".join(current))
            current, current_len = [], 0
        if len(doc) > target_chars:
            for i in range(0, len(doc), target_chars):
                chunks.append(doc[i : i + target_chars])
            continue
        current.append(doc)
        current_len += len(doc)
    if current:
        chunks.append("\n\n".join(current))
    return chunks


# --------------------------------------------------------------------------
# Response validation — tolerant of extra/missing keys, strict about shape.
# --------------------------------------------------------------------------


def validate_tag_result(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("model response was not a JSON object")
    out: dict = {}
    for key in _TAG_LIST_KEYS:
        value = raw.get(key) or []
        if not isinstance(value, list):
            raise ValueError(f"{key!r} was not a list")
        out[key] = _clean_list(key, value)
    for key, cap in _STR_CAPS.items():
        value = raw.get(key)
        out[key] = str(value).strip()[:cap] if value else None
    if not out["shortDescription"] or not out["longSummary"]:
        raise ValueError("missing shortDescription/longSummary")
    return out


def validate_chunk_result(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("chunk response was not a JSON object")
    out = {"chunkSummary": str(raw.get("chunkSummary") or "").strip()[:1500]}
    for key in _TAG_LIST_KEYS:
        value = raw.get(key)
        value = value if isinstance(value, list) else []
        out[key] = _clean_list(key, value)
    return out


# --------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------

_THEME_RULE = (
    "Each theme must be a specific multi-word phrase about THIS book, never a "
    'bare abstract noun like "identity", "survival", "power" or "love".'
)

_SCHEMA_INSTRUCTIONS = f"""Respond with ONLY a JSON object, no other text, with exactly these keys:
- "ageRating": one short label ("General", "Teen", "Mature", or "Explicit")
- "genres": array of genre strings
- "moods": array of mood/tone strings (e.g. "atmospheric", "fast-paced")
- "themes": array of thematic strings (e.g. "found family", "revenge against a corrupt church"). {_THEME_RULE}
- "representation": array of identity/representation strings actually present on the page (e.g. "gay protagonist", "wheelchair user") — omit anything you're not seeing evidence for
- "contentWarnings": array of content-warning strings (e.g. "graphic violence", "on-page suicide")
- "confidenceNotes": one short sentence on how confident you are and why (e.g. limited sample, ambiguous genre)
- "shortDescription": a 2-3 sentence, SPOILER-FREE back-cover-style blurb. Do not reveal how the book ends, any twists, or events from its final act.
- "longSummary": a fuller synopsis of several paragraphs. This one MAY include major plot points and how the book ends."""

# prompts/47 A.4 — passed to Ollama as `format`, so decoding is
# grammar-constrained to these exact shapes. The English above stays: the
# schema fixes the shape, the prompt still explains what goes in each field.
# The validators stay too (belt and braces, and they apply the caps/stoplist).
_STRING_LIST = {"type": "array", "items": {"type": "string"}, "maxItems": _MAX_LIST_ITEMS}

CHUNK_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "chunkSummary": {"type": "string"},
        **{key: _STRING_LIST for key in _TAG_LIST_KEYS},
    },
    "required": ["chunkSummary", *_TAG_LIST_KEYS],
}

TAG_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "ageRating": {"type": "string", "enum": ["General", "Teen", "Mature", "Explicit"]},
        **{key: _STRING_LIST for key in _TAG_LIST_KEYS},
        "confidenceNotes": {"type": "string"},
        "shortDescription": {"type": "string"},
        "longSummary": {"type": "string"},
    },
    "required": ["ageRating", *_TAG_LIST_KEYS, "confidenceNotes", "shortDescription", "longSummary"],
}

_MAP_SYSTEM_PROMPT = (
    "You are reading one section of a novel out of several, in order. "
    "Summarize only what happens in THIS section and note any genre/mood/"
    "theme/representation/content-warning evidence you see here. You do not "
    "have the rest of the book."
)

_REDUCE_SYSTEM_PROMPT = (
    "You are a book cataloguer. You are given section-by-section summaries "
    "and evidence notes covering an entire novel, in reading order, and must "
    "synthesize them into one final catalogue entry plus two descriptions."
)


def _book_label(book: Book) -> str:
    author = book.author.name if book.author else None
    return f'"{book.canonical_title}" by {author}' if author else f'"{book.canonical_title}"'


def _build_map_prompt(book: Book, chunk_text: str, index: int, total: int) -> str:
    return (
        f"Book: {_book_label(book)}. Section {index + 1} of {total}.\n\n"
        f"Text:\n{chunk_text}\n\n"
        'Respond with ONLY a JSON object: {"chunkSummary": "...", '
        '"genres": [...], "moods": [...], "themes": [...], '
        '"representation": [...], "contentWarnings": [...]}\n'
        f"{_THEME_RULE}"
    )


def _build_reduce_prompt(book: Book, chunk_results: list[dict]) -> str:
    sections = []
    for i, chunk in enumerate(chunk_results):
        evidence = ", ".join(
            v for key in _TAG_LIST_KEYS[:-1] for v in chunk.get(key, [])
        ) or "none noted"
        sections.append(
            f"[section {i + 1}] {chunk.get('chunkSummary', '')} (evidence: {evidence})"
        )
    joined = "\n".join(sections)
    return (
        f"Book: {_book_label(book)}\n\n"
        f"Section-by-section summaries, in reading order:\n{joined}\n\n{_SCHEMA_INSTRUCTIONS}"
    )


# --------------------------------------------------------------------------
# Work selection
# --------------------------------------------------------------------------


def _is_pending(entry: dict | None, now: datetime) -> bool:
    if not entry:
        return True
    if entry.get("status") in ("mapping", "reducing"):
        return False  # in-progress, surfaced separately by the caller
    error = entry.get("error")
    if error:
        failed_at = _parse_iso(entry.get("failedAt"))
        return failed_at is None or now - failed_at > _RETRY_BACKOFF
    return False  # has a real, non-error result


def select_work_item(rows: list[File], now: datetime) -> File | None:
    """The next file to advance: resume any book already mid-map/reduce
    before starting a new one, else the oldest book still needing a pass
    (never attempted, or failed past its retry backoff)."""
    for file in rows:
        full = (file.book.llm_tags_json or {}).get("full")
        if full and full.get("status") in ("mapping", "reducing"):
            return file
    for file in rows:
        full = (file.book.llm_tags_json or {}).get("full")
        if _is_pending(full, now):
            return file
    return None


def _mark_failed(book: Book, message: str) -> None:
    llm_tags = dict(book.llm_tags_json or {})
    llm_tags["full"] = {"error": message[:500], "failedAt": _now_iso()}
    book.llm_tags_json = llm_tags


async def _extract_documents(data: bytes, settings: Settings) -> list[str]:
    return await asyncio.to_thread(
        extract_full_text_documents_safely,
        data,
        max_entry_bytes=settings.epub_max_entry_bytes,
        max_total_bytes=settings.epub_max_total_bytes,
        max_entries=settings.epub_max_entries,
        timeout_seconds=settings.epub_parse_timeout_seconds,
    )


async def _run_full_step(client: OllamaClient, book: Book, data: bytes, settings: Settings) -> None:
    """Advance this book's full-text pass by exactly one step: map the next
    unmapped chunk, or (once every chunk is mapped) reduce them all into the
    final result. Re-extracts and re-chunks from `data` every call rather
    than persisting chunk text — cheap and thread-pooled, and deterministic
    as long as `ollama_num_ctx` hasn't changed since the pass started."""
    docs = await _extract_documents(data, settings)
    chunks = chunk_documents(docs, target_chars=_content_budget_chars(settings.ollama_num_ctx))
    if not chunks:
        raise ValueError("no extractable text")

    llm_tags = dict(book.llm_tags_json or {})
    full = dict(llm_tags.get("full") or {})
    if full.get("status") not in ("mapping", "reducing") or full.get("chunksTotal") != len(chunks):
        # Fresh start, or num_ctx changed since a previous attempt and the
        # chunk boundaries no longer line up — restart cleanly rather than
        # mapping the wrong chunk against stale progress.
        full = {"status": "mapping", "chunksTotal": len(chunks), "chunksDone": 0, "chunkResults": []}

    if full["status"] == "mapping":
        idx = full["chunksDone"]
        raw = await client.generate_json(
            system=_MAP_SYSTEM_PROMPT,
            prompt=_build_map_prompt(book, chunks[idx], idx, len(chunks)),
            schema=CHUNK_RESULT_SCHEMA,
        )
        chunk_result = validate_chunk_result(raw)
        full["chunkResults"] = [*full["chunkResults"], chunk_result]
        full["chunksDone"] = idx + 1
        if full["chunksDone"] >= full["chunksTotal"]:
            full["status"] = "reducing"
    elif full["status"] == "reducing":
        raw = await client.generate_json(
            system=_REDUCE_SYSTEM_PROMPT,
            prompt=_build_reduce_prompt(book, full["chunkResults"]),
            schema=TAG_RESULT_SCHEMA,
        )
        tags = validate_tag_result(raw)
        full = {**tags, "status": "done", "generatedAt": _now_iso()}

    llm_tags["full"] = full
    book.llm_tags_json = llm_tags


async def get_progress() -> dict:
    """Cheap DB-only counts (plus a little detail: the book in flight, the
    last few completed, the most recent failure) for the admin status
    endpoint — no Ollama/Drive calls. "pending" here means "would be picked
    up on a future tick" (never attempted, or failed and past its retry
    backoff)."""
    now = datetime.now(UTC)
    async with async_session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(File)
                    .join(Book, File.book_id == Book.id)
                    .where(File.status == FileStatus.organised)
                    .options(selectinload(File.book).selectinload(Book.author))
                )
            )
            .scalars()
            .all()
        )

    counts = {"full_done": 0, "full_pending": 0}
    current: dict | None = None
    recent: list[dict] = []
    last_error: dict | None = None
    for file in rows:
        book = file.book
        full = (book.llm_tags_json or {}).get("full")
        status = full.get("status") if full else None
        author = book.author.name if book.author else None

        if status == "done":
            counts["full_done"] += 1
            recent.append(
                {
                    "title": book.canonical_title,
                    "author": author,
                    "generated_at": full.get("generatedAt"),
                    "genres": full.get("genres") or [],
                }
            )
            continue

        if status in ("mapping", "reducing"):
            counts["full_pending"] += 1
            current = {
                "title": book.canonical_title,
                "author": author,
                "status": status,
                "chunks_done": full.get("chunksDone", 0),
                "chunks_total": full.get("chunksTotal", 0),
            }
            continue

        if _is_pending(full, now):
            counts["full_pending"] += 1
            error = full.get("error") if full else None
            if error:
                failed_at = full.get("failedAt")
                if last_error is None or (failed_at or "") > (last_error["failed_at"] or ""):
                    last_error = {
                        "title": book.canonical_title,
                        "author": author,
                        "error": error,
                        "failed_at": failed_at,
                    }

    recent.sort(key=lambda r: r["generated_at"] or "", reverse=True)
    return {
        "full_done": counts["full_done"],
        "full_pending": counts["full_pending"],
        "current": current,
        "recent": recent[:5],
        "last_error": last_error,
    }


async def tick() -> dict:
    """One iteration of the LLM-tagging drip (app.jobs.scheduler ticks every
    few minutes). Does at most one unit of work — one map/reduce step of one
    book's full-text pass — then returns. Never raises: every failure is
    caught, logged, and turned into a per-item retry state rather than
    crashing the loop or blocking the next book."""
    settings = get_settings()
    host = settings.ollama_host.strip()
    if not host:
        return {"skipped": "ollama_host not configured"}

    async with async_session_factory() as session:
        if (await SettingsRepository(session).get(LLM_TAGGING_ENABLED)) != "true":
            return {"skipped": "disabled"}

    now = datetime.now(UTC)
    if not _in_allowed_window(_local_now(settings)):
        return {"skipped": "outside allowed window"}

    client = OllamaClient(
        host,
        model=settings.ollama_model,
        num_ctx=settings.ollama_num_ctx,
        timeout_seconds=settings.ollama_timeout_seconds,
    )
    if not await client.is_reachable():
        return {"skipped": "ollama unreachable"}

    async with async_session_factory() as session:
        repo = SettingsRepository(session)
        try:
            creds = await get_auth_service().get_credentials(repo)
        except Exception:  # noqa: BLE001 — token refresh failure etc.
            return {"skipped": "no drive credentials"}
        if creds is None:
            return {"skipped": "drive not connected"}

        rows = (
            (
                await session.execute(
                    select(File)
                    .join(Book, File.book_id == Book.id)
                    .where(File.status == FileStatus.organised)
                    .options(selectinload(File.book).selectinload(Book.author))
                    .order_by(File.discovered_at)
                )
            )
            .scalars()
            .all()
        )

        file = select_work_item(rows, now)
        if file is None:
            return {"skipped": "nothing pending"}
        book = file.book

        try:
            # A chunked response trickling a few bytes just under
            # drive/client.py's idle-recv timeout can stall forever without
            # tripping it. Caught live 2026-09-14 (3+ hours) and again
            # 2026-09-17 (~5 hours): `asyncio.wait_for` around a plain
            # `asyncio.to_thread` download can't actually save you here —
            # once the worker thread is blocked inside a synchronous socket
            # read, cancelling the *await* doesn't stop the thread, and
            # wait_for wIll just sit there waiting for it to finish anyway.
            # A subprocess can be SIGKILLed regardless of what syscall it's
            # stuck in, so the real download now happens in one — see
            # llm_tagging_download.py.
            data = await asyncio.to_thread(
                download_file_with_hard_timeout,
                creds,
                file.drive_file_id,
                timeout_seconds=90.0,
            )
        except Exception as exc:  # noqa: BLE001 — a Drive hiccup, try again next tick
            logger.exception("llm tagging: download failed for book %s", book.id)
            return {"error": f"download failed: {exc}"}

        try:
            await _run_full_step(client, book, data, settings)
        except OllamaUnavailable as exc:
            # The host died mid-tick — not the book's fault, don't mark it
            # failed, just stop for now.
            logger.warning("llm tagging: ollama became unavailable mid-tick: %s", exc)
            return {"skipped": "ollama unavailable"}
        except (OllamaBadResponse, EpubParseError, EpubParseTimeoutError, ValueError) as exc:
            logger.warning("llm tagging: failed for book %s: %s", book.id, exc)
            _mark_failed(book, str(exc))
        except Exception as exc:  # noqa: BLE001 — never crash the loop
            logger.exception("llm tagging: failed for book %s", book.id)
            _mark_failed(book, str(exc))

        just_finished = (book.llm_tags_json or {}).get("full", {}).get("status") == "done"
        await session.commit()

    if just_finished:
        await _refresh_theme_canon()
    return {"processed": book.id}


async def _refresh_theme_canon() -> None:
    """prompts/47 A.3 — fold the newly finished book's themes into the
    library-wide canonical vocabulary. Best-effort: a failure here never
    touches the book's own (already committed) tags."""
    try:
        async with async_session_factory() as session:
            await theme_dedup_service.refresh_theme_canon(session)
    except Exception:  # noqa: BLE001 — never crash the loop
        logger.exception("llm tagging: theme dedup refresh failed")
