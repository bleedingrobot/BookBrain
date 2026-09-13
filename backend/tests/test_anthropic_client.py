import json

import anthropic
import httpx2
import pytest

from app.providers.ai.anthropic_client import AIIdentificationError, AnthropicIdentificationClient

MESSAGES_URL = "https://api.anthropic.com/v1/messages"


class _RecordingTransport:
    """Replays `responses` in order (repeating the last one if called more
    times than provided), recording every request it saw.

    The installed `anthropic` SDK now requires a custom `http_client` to be
    an `httpx2.AsyncClient` (its own vendored httpx fork) rather than a plain
    `httpx.AsyncClient` — and `respx`, which these tests used to mock the
    Messages API, only knows how to patch plain `httpx`. `httpx2.MockTransport`
    is httpx2's own equivalent of `httpx.MockTransport` / respx, so this
    class is the handler passed to it.
    """

    def __init__(self, responses: list[httpx2.Response]) -> None:
        self.responses = responses
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self.responses) - 1)
        return self.responses[index]


def _client(
    *responses: httpx2.Response, model: str | None = None
) -> tuple[AnthropicIdentificationClient, _RecordingTransport]:
    transport = _RecordingTransport(list(responses))
    http_client = httpx2.AsyncClient(transport=httpx2.MockTransport(transport))
    client = AnthropicIdentificationClient(
        client=anthropic.AsyncAnthropic(api_key="test-key", http_client=http_client),
        model=model,
    )
    return client, transport


def _payload(transport: _RecordingTransport, index: int = -1) -> dict:
    return json.loads(transport.requests[index].content)


def _tool_use_response(input_data: dict) -> dict:
    return {
        "id": "msg_01",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5",
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_01",
                "name": "identify_book",
                "input": input_data,
            }
        ],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }


async def test_identify_parses_tool_use_response() -> None:
    client, _ = _client(
        httpx2.Response(
            200,
            json=_tool_use_response(
                {
                    "title": "Dune",
                    "author": "Frank Herbert",
                    "series": "Dune Chronicles",
                    "series_number": 1,
                    "ai_confidence": 92,
                    "reasoning_summary": "Matches the copyright page and cover text.",
                    "needs_human_review": False,
                }
            ),
        )
    )

    result, raw = await client.identify("some prompt")

    assert result.title == "Dune"
    assert result.author == "Frank Herbert"
    assert result.series == "Dune Chronicles"
    assert result.ai_confidence == 92
    assert result.needs_human_review is False
    assert raw["stop_reason"] == "tool_use"


async def test_identify_sends_forced_tool_choice() -> None:
    client, transport = _client(
        httpx2.Response(
            200,
            json=_tool_use_response(
                {
                    "title": "Dune",
                    "author": "Frank Herbert",
                    "series": None,
                    "series_number": None,
                    "ai_confidence": 80,
                    "reasoning_summary": "x",
                    "needs_human_review": True,
                }
            ),
        )
    )

    await client.identify("some prompt")

    payload = _payload(transport)
    assert payload["tool_choice"] == {"type": "tool", "name": "identify_book"}
    assert payload["tools"][0]["name"] == "identify_book"


async def test_identify_raises_on_refusal() -> None:
    client, _ = _client(
        httpx2.Response(
            200,
            json={
                "id": "msg_01",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5",
                "content": [],
                "stop_reason": "refusal",
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 0},
            },
        )
    )

    with pytest.raises(AIIdentificationError):
        await client.identify("some prompt")


async def test_identify_series_parses_tool_use_response() -> None:
    client, _ = _client(
        httpx2.Response(
            200,
            json={
                "id": "msg_02",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_02",
                        "name": "identify_series",
                        "input": {"series": "Dune Chronicles", "series_number": 1},
                    }
                ],
                "stop_reason": "tool_use",
                "stop_sequence": None,
                "usage": {"input_tokens": 50, "output_tokens": 10},
            },
        )
    )

    result, raw = await client.identify_series("Dune", "Frank Herbert")

    assert result.series == "Dune Chronicles"
    assert result.series_number == 1
    assert raw["stop_reason"] == "tool_use"


async def test_identify_series_raises_on_refusal() -> None:
    client, _ = _client(
        httpx2.Response(
            200,
            json={
                "id": "msg_02",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5",
                "content": [],
                "stop_reason": "refusal",
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 0},
            },
        )
    )

    with pytest.raises(AIIdentificationError):
        await client.identify_series("Dune", "Frank Herbert")


def _grounded_then_identify_response(input_data: dict) -> dict:
    """One turn: the model runs a web search (server-side, inline) and then
    commits via identify_book."""
    return {
        "id": "msg_g1",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5",
        "content": [
            {
                "type": "server_tool_use",
                "id": "srv_01",
                "name": "web_search",
                "input": {"query": "Scion James Islington series"},
            },
            {
                "type": "web_search_tool_result",
                "tool_use_id": "srv_01",
                "content": [
                    {
                        "type": "web_search_result",
                        "title": "Scion by James Islington - Wikipedia",
                        "url": "https://en.wikipedia.org/wiki/Scion_(Islington)",
                        "encrypted_content": "x",
                        "page_age": None,
                    }
                ],
            },
            {"type": "tool_use", "id": "toolu_g1", "name": "identify_book", "input": input_data},
        ],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 400, "output_tokens": 80},
    }


_IDENTIFY_INPUT = {
    "title": "Scion",
    "author": "James Islington",
    "series": None,
    "series_number": None,
    "ai_confidence": 88,
    "reasoning_summary": "Web search confirms Scion is a standalone.",
    "needs_human_review": False,
}


async def test_grounded_identify_declares_web_search_and_records_grounding() -> None:
    client, transport = _client(
        httpx2.Response(200, json=_grounded_then_identify_response(_IDENTIFY_INPUT))
    )

    result, raw = await client.identify("some prompt", ground=True)

    payload = _payload(transport)
    tool_names = {t.get("name") for t in payload["tools"]}
    assert "web_search" in tool_names and "identify_book" in tool_names
    assert "tool_choice" not in payload  # can't force a tool and allow search
    assert "Today's date is" in payload["system"]

    assert result.title == "Scion"
    assert result.series is None
    assert raw["grounding"]["queries"] == ["Scion James Islington series"]
    assert raw["grounding"]["results"] == ["Scion by James Islington - Wikipedia"]


async def test_grounded_identify_falls_back_to_forced_call_on_refusal() -> None:
    client, _ = _client(
        httpx2.Response(
            200,
            json={
                "id": "msg_r",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5",
                "content": [],
                "stop_reason": "refusal",
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 0},
            },
        ),
        httpx2.Response(200, json=_tool_use_response(_IDENTIFY_INPUT)),
    )

    result, raw = await client.identify("some prompt", ground=True)

    assert result.title == "Scion"
    assert raw["grounding"]["fell_back"] == "refusal"


async def test_grounded_identify_forces_the_tool_when_model_answers_in_text() -> None:
    text_turn = {
        "id": "msg_t",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5",
        "content": [
            {
                "type": "server_tool_use",
                "id": "srv_9",
                "name": "web_search",
                "input": {"query": "Scion Islington"},
            },
            {
                "type": "web_search_tool_result",
                "tool_use_id": "srv_9",
                "content": [
                    {
                        "type": "web_search_result",
                        "title": "Scion - Goodreads",
                        "url": "https://g.co",
                        "encrypted_content": "x",
                        "page_age": None,
                    }
                ],
            },
            {"type": "text", "text": "This is Scion by James Islington, a standalone."},
        ],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 300, "output_tokens": 40},
    }
    client, transport = _client(
        httpx2.Response(200, json=text_turn),
        httpx2.Response(200, json=_tool_use_response(_IDENTIFY_INPUT)),
    )

    result, raw = await client.identify("some prompt", ground=True)

    forced_payload = _payload(transport)
    assert forced_payload["tool_choice"] == {"type": "tool", "name": "identify_book"}
    assert result.title == "Scion"
    assert raw["grounding"]["results"] == ["Scion - Goodreads"]


async def test_grounded_identify_falls_back_when_web_search_tool_is_rejected() -> None:
    # e.g. Haiku doesn't support the server tool — API returns 400. Must not
    # fail the identification, just do it un-grounded.
    client, _ = _client(
        httpx2.Response(
            400,
            json={
                "type": "error",
                "error": {"type": "invalid_request_error", "message": "web_search unsupported"},
            },
        ),
        httpx2.Response(200, json=_tool_use_response(_IDENTIFY_INPUT)),
        model="claude-haiku-4-5",
    )

    result, raw = await client.identify("some prompt", ground=True)

    assert result.title == "Scion"
    assert "grounding" not in raw  # un-grounded fallback


def test_web_search_tool_variant_by_model() -> None:
    from app.providers.ai.anthropic_client import _web_search_tool

    assert _web_search_tool("claude-opus-5", 2)["type"] == "web_search_20260209"
    assert _web_search_tool("claude-sonnet-5", 3)["type"] == "web_search_20260209"
    assert _web_search_tool("claude-haiku-4-5", 2)["type"] == "web_search_20250305"
    assert _web_search_tool("claude-haiku-4-5", 2)["max_uses"] == 2


async def test_ungrounded_identify_is_a_single_forced_call() -> None:
    client, transport = _client(httpx2.Response(200, json=_tool_use_response(_IDENTIFY_INPUT)))

    await client.identify("some prompt")  # ground defaults to False

    payload = _payload(transport)
    assert payload["tool_choice"] == {"type": "tool", "name": "identify_book"}
    assert [t["name"] for t in payload["tools"]] == ["identify_book"]
    assert len(transport.requests) == 1


async def test_identify_raises_when_no_tool_use_block() -> None:
    client, _ = _client(
        httpx2.Response(
            200,
            json={
                "id": "msg_01",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5",
                "content": [{"type": "text", "text": "I cannot identify this."}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )
    )

    with pytest.raises(AIIdentificationError):
        await client.identify("some prompt")
