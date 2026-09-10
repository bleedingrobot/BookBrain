import json

import pytest
import websockets

from app.core.config import get_settings
from app.services import openbooks_service
from app.services.openbooks_service import (
    OpenBooksError,
    OpenBooksRateLimited,
    OpenBooksUnavailable,
)

CONNECT_OK = {"type": 1, "appearance": 1, "title": "Welcome", "name": "tester"}


async def _serve(handler):
    """Start a fake OpenBooks WS server on an ephemeral port, point the
    service at it, and return the server (close it in the test)."""
    server = await websockets.serve(handler, "localhost", 0)
    port = server.sockets[0].getsockname()[1]
    return server, f"ws://localhost:{port}/ws"


@pytest.fixture
def point_at(monkeypatch):
    def _apply(url: str) -> None:
        monkeypatch.setattr(get_settings(), "openbooks_ws_url", url)

    return _apply


async def _expect(ws, msg_type: int) -> dict:
    raw = await ws.recv()
    msg = json.loads(raw)
    assert msg["type"] == msg_type
    return msg


@pytest.mark.asyncio
async def test_search_parses_results(point_at):
    async def handler(ws):
        await _expect(ws, 1)  # CONNECT
        await ws.send(json.dumps(CONNECT_OK))
        await _expect(ws, 2)  # SEARCH
        await ws.send(json.dumps({"type": 0, "appearance": 0, "title": "Search accepted."}))
        await ws.send(
            json.dumps(
                {
                    "type": 2,
                    "appearance": 1,
                    "title": "2 Search Results Received",
                    "books": [
                        {
                            "server": "Bsk",
                            "author": "Brandon Sanderson",
                            "title": "The Final Empire",
                            "format": "epub",
                            "size": "2.2MB",
                            "full": "!Bsk Brandon Sanderson - The Final Empire.epub",
                        },
                        {
                            "server": "Oatmeal",
                            "author": "Brandon Sanderson",
                            "title": "The Final Empire",
                            "format": "mobi",
                            "size": "3.1MB",
                            "full": "!Oatmeal Brandon Sanderson - The Final Empire.mobi",
                        },
                    ],
                    "errors": ["one junk line"],
                }
            )
        )

    server, url = await _serve(handler)
    point_at(url)
    try:
        outcome = await openbooks_service.search("mistborn")
    finally:
        server.close()
        await server.wait_closed()

    assert len(outcome.results) == 2
    assert outcome.parse_errors == 1
    assert outcome.results[0].full == "!Bsk Brandon Sanderson - The Final Empire.epub"
    assert outcome.results[1].format == "mobi"


@pytest.mark.asyncio
async def test_search_rate_limited(point_at):
    async def handler(ws):
        await _expect(ws, 1)
        await ws.send(json.dumps(CONNECT_OK))
        await _expect(ws, 2)
        await ws.send(
            json.dumps(
                {
                    "type": 4,
                    "appearance": 2,
                    "title": "You are searching too frequently!",
                    "detail": "Please wait 7 seconds to submit another search.",
                }
            )
        )

    server, url = await _serve(handler)
    point_at(url)
    try:
        with pytest.raises(OpenBooksRateLimited) as exc:
            await openbooks_service.search("mistborn")
    finally:
        server.close()
        await server.wait_closed()
    assert exc.value.wait_seconds == 7.0


@pytest.mark.asyncio
async def test_search_no_results(point_at):
    async def handler(ws):
        await _expect(ws, 1)
        await ws.send(json.dumps(CONNECT_OK))
        await _expect(ws, 2)
        await ws.send(
            json.dumps({"type": 0, "appearance": 3, "title": "No results found for the query."})
        )

    server, url = await _serve(handler)
    point_at(url)
    try:
        outcome = await openbooks_service.search("kjsdfkjsdf")
    finally:
        server.close()
        await server.wait_closed()
    assert outcome.results == []
    assert outcome.message == "No results found for the query."


@pytest.mark.asyncio
async def test_download_returns_local_path(point_at, tmp_path):
    book = tmp_path / "books" / "Brandon Sanderson - The Final Empire.epub"
    book.parent.mkdir(parents=True)
    book.write_bytes(b"PK\x03\x04 not really a zip but exists")

    async def handler(ws):
        await _expect(ws, 1)
        await ws.send(json.dumps(CONNECT_OK))
        req = await _expect(ws, 3)
        assert req["payload"]["book"] == "!Bsk x.epub"
        await ws.send(json.dumps({"type": 0, "appearance": 0, "title": "Download request received."}))
        await ws.send(
            json.dumps(
                {
                    "type": 3,
                    "appearance": 1,
                    "title": "Book file received.",
                    "detail": str(book),
                    "downloadPath": "",
                    "name": "",
                }
            )
        )

    server, url = await _serve(handler)
    point_at(url)
    try:
        path = await openbooks_service.download("!Bsk x.epub")
    finally:
        server.close()
        await server.wait_closed()
    assert path == book


@pytest.mark.asyncio
async def test_download_server_error(point_at):
    async def handler(ws):
        await _expect(ws, 1)
        await ws.send(json.dumps(CONNECT_OK))
        await _expect(ws, 3)
        await ws.send(
            json.dumps({"type": 0, "appearance": 3, "title": "Server is not available. Try another one."})
        )

    server, url = await _serve(handler)
    point_at(url)
    try:
        with pytest.raises(OpenBooksError, match="not available"):
            await openbooks_service.download("!Dead x.epub")
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_unavailable_when_nothing_listening(point_at):
    point_at("ws://localhost:5999/ws")  # nothing there
    with pytest.raises(OpenBooksUnavailable):
        await openbooks_service.search("mistborn")


@pytest.mark.asyncio
async def test_oversized_query_rejected(point_at):
    with pytest.raises(OpenBooksError, match="too long"):
        await openbooks_service.search("x" * 500)


@pytest.mark.asyncio
async def test_reconnects_after_drop(point_at):
    calls = {"n": 0}

    async def handler(ws):
        calls["n"] += 1
        first = calls["n"] == 1
        await _expect(ws, 1)
        await ws.send(json.dumps(CONNECT_OK))
        await _expect(ws, 2)
        if first:
            await ws.close()  # simulate a mid-op drop
            return
        await ws.send(
            json.dumps({"type": 2, "appearance": 1, "title": "0 results", "books": [], "errors": []})
        )

    server, url = await _serve(handler)
    point_at(url)
    try:
        outcome = await openbooks_service.search("mistborn")
    finally:
        server.close()
        await server.wait_closed()
    assert calls["n"] == 2
    assert outcome.results == []
