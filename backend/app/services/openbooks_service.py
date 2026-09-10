"""Client for a locally-running OpenBooks server (server mode WebSocket API).

OpenBooks (https://github.com/evan-buss/openbooks) is an IRC client that
searches and downloads ebooks from the IRC Highway ``#ebook`` channel. We run
it separately (``backend/tools/run-openbooks.ps1``) and talk to its
``/ws`` endpoint as *the single allowed client* — the server refuses a second
WebSocket connection, so the OpenBooks web UI must stay closed while BookBrain
holds the socket.

Protocol (see ``server/messages.go`` in the OpenBooks source):

* Requests: ``{"type": <int>, "payload": {...}}`` — CONNECT=1 ``{}``,
  SEARCH=2 ``{"query": "..."}``, DOWNLOAD=3 ``{"book": "<full command>"}``.
  The server caps an incoming message at 512 bytes.
* Responses all carry ``type`` and ``appearance`` (0 notify .. 3 danger) plus
  ``title`` / ``detail``. A SEARCH response adds ``books`` + ``errors``; a
  DOWNLOAD response adds ``name`` + ``downloadPath`` (empty when OpenBooks is
  run with ``--no-browser-downloads``, in which case ``detail`` is the
  absolute path of the saved file).

This is experimental — a manual "find this book" tool, not a nightly
auto-fill loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException
from websockets.protocol import State

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# server/messages.go MessageType
_STATUS, _CONNECT, _SEARCH, _DOWNLOAD, _RATELIMIT = 0, 1, 2, 3, 4
# server/messages.go NotificationType
_DANGER = 3

# The server rejects an incoming frame over 512 bytes (client.go maxMessageSize).
# Leave headroom for the ``{"type":N,"payload":{...}}`` envelope.
_MAX_QUERY_BYTES = 400
_MAX_BOOK_BYTES = 460

_CONNECT_TIMEOUT = 30.0
_SEARCH_TIMEOUT = 90.0
# A healthy DCC transfer of an epub is seconds. A longer wait just means the
# source server is dead or trickling a truncated file — and a truncated
# transfer arriving after we've moved on has crashed the OpenBooks process
# (its `send on closed channel` bug), so don't sit on a bad one.
_DOWNLOAD_TIMEOUT = 150.0


class OpenBooksError(RuntimeError):
    """Base for every OpenBooks failure surfaced to a route."""


class OpenBooksUnavailable(OpenBooksError):
    """OpenBooks isn't reachable, refused the connection, or couldn't join IRC."""


class OpenBooksRateLimited(OpenBooksError):
    """The server's own search cooldown (>=10s between searches) is active."""

    def __init__(self, wait_seconds: float) -> None:
        super().__init__(f"OpenBooks is rate-limiting searches; try again in {wait_seconds:.0f}s")
        self.wait_seconds = wait_seconds


@dataclass(frozen=True)
class BookResult:
    server: str
    author: str
    title: str
    format: str
    size: str
    full: str  # the raw "!server filename" IRC command; pass back verbatim to download()

    @classmethod
    def from_raw(cls, raw: dict) -> "BookResult":
        return cls(
            server=str(raw.get("server") or ""),
            author=str(raw.get("author") or ""),
            title=str(raw.get("title") or ""),
            format=str(raw.get("format") or ""),
            size=str(raw.get("size") or ""),
            full=str(raw.get("full") or ""),
        )


@dataclass
class SearchOutcome:
    results: list[BookResult] = field(default_factory=list)
    parse_errors: int = 0
    message: str | None = None  # set when the server reported "no results"


def _parse_wait_seconds(detail: str) -> float:
    match = re.search(r"(\d+(?:\.\d+)?)", detail or "")
    return float(match.group(1)) if match else 10.0


class _OpenBooksClient:
    """Holds one cached WebSocket connection, reused across operations and
    reconnected on demand. A single lock serialises everything — OpenBooks is
    a single-user tool and its search endpoint is globally rate-limited."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._ws: websockets.ClientConnection | None = None
        self.nick: str | None = None

    async def aclose(self) -> None:
        async with self._lock:
            await self._drop()

    async def _drop(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001 - best effort
                pass
            self._ws = None
            self.nick = None

    async def _send(self, msg_type: int, payload: dict) -> None:
        assert self._ws is not None
        await self._ws.send(json.dumps({"type": msg_type, "payload": payload}))

    async def _recv_until(self, deadline: float) -> dict | None:
        """One message, or None once `deadline` has passed (or a poll timed
        out). A `ConnectionClosed` still propagates — `_with_reconnect` wants
        it. Guards against a non-positive `wait_for` timeout, which raises a
        bare `TimeoutError` that would otherwise escape as a 500."""
        assert self._ws is not None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=remaining)
        except (TimeoutError, asyncio.TimeoutError):
            return None
        return json.loads(raw)

    async def _ensure_connected(self) -> None:
        if self._ws is not None and self._ws.state is State.OPEN:
            return
        self._ws = None
        url = get_settings().openbooks_ws_url
        try:
            self._ws = await websockets.connect(url, max_size=None, open_timeout=_CONNECT_TIMEOUT)
        except InvalidStatus as exc:
            code = getattr(getattr(exc, "response", None), "status_code", None)
            if code == 400:
                raise OpenBooksUnavailable(
                    "OpenBooks already has a client connected — close the OpenBooks web UI and retry"
                ) from exc
            raise OpenBooksUnavailable(f"OpenBooks refused the connection (HTTP {code})") from exc
        except (OSError, asyncio.TimeoutError, WebSocketException) as exc:
            raise OpenBooksUnavailable(
                f"can't reach OpenBooks at {url} — is `run-openbooks.ps1` running?"
            ) from exc

        # CONNECT -> the server joins IRC and replies with a CONNECT status.
        await self._send(_CONNECT, {})
        deadline = time.monotonic() + _CONNECT_TIMEOUT
        while True:
            try:
                msg = await self._recv_until(deadline)
            except ConnectionClosed as exc:
                await self._drop()
                raise OpenBooksUnavailable("OpenBooks did not confirm an IRC connection") from exc
            if msg is None:
                break
            if msg.get("type") == _CONNECT:
                self.nick = msg.get("name")
                logger.info("openbooks: connected to IRC as %s", self.nick)
                return
            if msg.get("type") == _STATUS and msg.get("appearance") == _DANGER:
                await self._drop()
                raise OpenBooksUnavailable(msg.get("title") or "OpenBooks could not connect to IRC")
        await self._drop()
        raise OpenBooksUnavailable("OpenBooks did not confirm an IRC connection in time")

    async def _with_reconnect(self, op):
        await self._ensure_connected()
        try:
            return await op()
        except ConnectionClosed:
            logger.warning("openbooks: connection dropped mid-operation, reconnecting once")
            await self._drop()
            await self._ensure_connected()
            return await op()

    async def search(self, query: str) -> SearchOutcome:
        query = " ".join(query.split())
        if not query:
            raise OpenBooksError("empty search query")
        if len(query.encode("utf-8")) > _MAX_QUERY_BYTES:
            raise OpenBooksError("search query is too long for OpenBooks")
        async with self._lock:
            return await self._with_reconnect(lambda: self._search(query))

    async def _search(self, query: str) -> SearchOutcome:
        await self._send(_SEARCH, {"query": query})
        deadline = time.monotonic() + _SEARCH_TIMEOUT
        while True:
            msg = await self._recv_until(deadline)
            if msg is None:
                raise OpenBooksError(
                    f"OpenBooks returned no results for {query!r} within "
                    f"{_SEARCH_TIMEOUT:.0f}s (the IRC search bot may be busy or "
                    "ignoring the query — try more/fuller words)"
                )
            mtype = msg.get("type")
            if mtype == _RATELIMIT:
                raise OpenBooksRateLimited(_parse_wait_seconds(msg.get("detail", "")))
            if mtype == _SEARCH:
                books = [BookResult.from_raw(b) for b in (msg.get("books") or [])]
                return SearchOutcome(results=books, parse_errors=len(msg.get("errors") or []))
            if mtype == _STATUS and msg.get("appearance") == _DANGER:
                # "No results found for the query."
                return SearchOutcome(message=msg.get("title") or "No results found")
            # otherwise a NOTIFY status ("Search accepted into the queue.",
            # "Found N results for your query.") — keep waiting.

    async def download(self, full: str) -> Path:
        full = full.strip()
        if not full:
            raise OpenBooksError("empty download command")
        if len(full.encode("utf-8")) > _MAX_BOOK_BYTES:
            raise OpenBooksError("that result's command is too long for OpenBooks to accept")
        async with self._lock:
            return await self._with_reconnect(lambda: self._download(full))

    async def _download(self, full: str) -> Path:
        await self._send(_DOWNLOAD, {"book": full})
        deadline = time.monotonic() + _DOWNLOAD_TIMEOUT
        while True:
            msg = await self._recv_until(deadline)
            if msg is None:
                raise OpenBooksError(
                    f"OpenBooks didn't deliver the file within {_DOWNLOAD_TIMEOUT:.0f}s "
                    "(the source server may be offline — try another result)"
                )
            mtype = msg.get("type")
            if mtype == _DOWNLOAD:
                return self._resolve_download_path(msg)
            if mtype == _STATUS and msg.get("appearance") == _DANGER:
                raise OpenBooksError(msg.get("title") or "OpenBooks download failed")
            # NOTIFY "Download request received." — keep waiting for the file.

    @staticmethod
    def _resolve_download_path(msg: dict) -> Path:
        detail = (msg.get("detail") or "").strip()
        if detail:
            candidate = Path(detail)
            if candidate.is_absolute() and candidate.exists():
                return candidate
        # Fallback for a server started without --no-browser-downloads:
        # downloadPath is "library/<file>", the file itself is at
        # <download_dir>/books/<file>.
        base = Path(msg.get("downloadPath") or msg.get("name") or "").name
        if base:
            candidate = Path(get_settings().openbooks_download_dir) / "books" / base
            if candidate.exists():
                return candidate
        raise OpenBooksError(
            "OpenBooks reported a completed download but the file could not be located on disk"
        )


_client = _OpenBooksClient()


def _reset_for_tests() -> None:
    """conftest calls this before every test — the module-level client owns an
    asyncio.Lock that would otherwise bind to the first test's event loop."""
    global _client
    _client = _OpenBooksClient()


def is_enabled() -> bool:
    return get_settings().openbooks_enabled


async def search(query: str) -> SearchOutcome:
    return await _client.search(query)


async def download(full: str) -> Path:
    return await _client.download(full)


async def aclose() -> None:
    await _client.aclose()
