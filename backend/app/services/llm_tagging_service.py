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

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import Settings, get_settings
from app.core.settings_keys import LLM_TAGGING_ENABLED
from app.data.db import async_session_factory
from app.data.models import Book, File, FileStatus
from app.data.repositories.settings_repository import SettingsRepository
from app.providers.ai.ollama_client import OllamaBadResponse, OllamaClient, OllamaUnavailable
from app.providers.drive.client import build_drive_service
from app.providers.drive.provider import DriveProvider
from app.providers.epub.errors import EpubParseError, EpubParseTimeoutError
from app.providers.epub.parser import extract_full_text_documents_safely
from app.services.auth_service import get_auth_service

logger = logging.getLogger(__name__)

# Retry a failed book/step after this long, rather than hammering it every
# tick — malformed output or a bad extraction usually isn't transient.
_RETRY_BACKOFF = timedelta(hours=24)

# Allowed windows, machine-local time (matches the rest of the scheduler):
# anytime overnight, plus weekday office hours — never evenings/weekends,
# when the gaming PC is presumably in actual use.
_OVERNIGHT_START_HOUR = 23  # 11pm
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
        out[key] = [str(v).strip() for v in value if str(v).strip()][:_MAX_LIST_ITEMS]
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
        out[key] = [str(v).strip() for v in value if str(v).strip()][:_MAX_LIST_ITEMS]
    return out


# --------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------

_SCHEMA_INSTRUCTIONS = """Respond with ONLY a JSON object, no other text, with exactly these keys:
- "ageRating": one short label ("General", "Teen", "Mature", or "Explicit")
- "genres": array of genre strings
- "moods": array of mood/tone strings (e.g. "atmospheric", "fast-paced")
- "themes": array of thematic strings (e.g. "found family", "revenge")
- "representation": array of identity/representation strings actually present on the page (e.g. "gay protagonist", "wheelchair user") — omit anything you're not seeing evidence for
- "contentWarnings": array of content-warning strings (e.g. "graphic violence", "on-page suicide")
- "confidenceNotes": one short sentence on how confident you are and why (e.g. limited sample, ambiguous genre)
- "shortDescription": a 2-3 sentence, SPOILER-FREE back-cover-style blurb. Do not reveal how the book ends, any twists, or events from its final act.
- "longSummary": a fuller synopsis of several paragraphs. This one MAY include major plot points and how the book ends."""

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
        '"representation": [...], "contentWarnings": [...]}'
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
        )
        tags = validate_tag_result(raw)
        full = {**tags, "status": "done", "generatedAt": _now_iso()}

    llm_tags["full"] = full
    book.llm_tags_json = llm_tags


async def get_progress() -> dict:
    """Cheap DB-only counts for the admin status endpoint — no Ollama/Drive
    calls. "pending" here means "would be picked up on a future tick"
    (never attempted, or failed and past its retry backoff)."""
    now = datetime.now(UTC)
    async with async_session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(File)
                    .join(Book, File.book_id == Book.id)
                    .where(File.status == FileStatus.organised)
                    .options(selectinload(File.book))
                )
            )
            .scalars()
            .all()
        )

    counts = {"full_done": 0, "full_pending": 0}
    for file in rows:
        full = (file.book.llm_tags_json or {}).get("full")
        if full and full.get("status") == "done":
            counts["full_done"] += 1
        elif (full and full.get("status") in ("mapping", "reducing")) or _is_pending(full, now):
            counts["full_pending"] += 1
    return counts


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
    if not _in_allowed_window(datetime.now()):  # local wall-clock time, not UTC
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
            provider = DriveProvider(build_drive_service(creds))
            data = await asyncio.to_thread(provider.download_file, file.drive_file_id)
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

        await session.commit()

    return {"processed": book.id}
