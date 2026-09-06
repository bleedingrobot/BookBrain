import anthropic
import httpx
import pytest
import respx

from app.providers.ai.anthropic_client import AIIdentificationError, AnthropicIdentificationClient

MESSAGES_URL = "https://api.anthropic.com/v1/messages"


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


@respx.mock
async def test_identify_parses_tool_use_response() -> None:
    respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(
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

    async with httpx.AsyncClient() as http_client:
        client = AnthropicIdentificationClient(
            client=anthropic.AsyncAnthropic(api_key="test-key", http_client=http_client)
        )
        result, raw = await client.identify("some prompt")

    assert result.title == "Dune"
    assert result.author == "Frank Herbert"
    assert result.series == "Dune Chronicles"
    assert result.ai_confidence == 92
    assert result.needs_human_review is False
    assert raw["stop_reason"] == "tool_use"


@respx.mock
async def test_identify_sends_forced_tool_choice() -> None:
    route = respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(
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

    async with httpx.AsyncClient() as http_client:
        client = AnthropicIdentificationClient(
            client=anthropic.AsyncAnthropic(api_key="test-key", http_client=http_client)
        )
        await client.identify("some prompt")

    sent_body = route.calls.last.request.content
    import json

    payload = json.loads(sent_body)
    assert payload["tool_choice"] == {"type": "tool", "name": "identify_book"}
    assert payload["tools"][0]["name"] == "identify_book"


@respx.mock
async def test_identify_raises_on_refusal() -> None:
    respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(
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

    async with httpx.AsyncClient() as http_client:
        client = AnthropicIdentificationClient(
            client=anthropic.AsyncAnthropic(api_key="test-key", http_client=http_client)
        )
        with pytest.raises(AIIdentificationError):
            await client.identify("some prompt")


@respx.mock
async def test_identify_series_parses_tool_use_response() -> None:
    respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(
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

    async with httpx.AsyncClient() as http_client:
        client = AnthropicIdentificationClient(
            client=anthropic.AsyncAnthropic(api_key="test-key", http_client=http_client)
        )
        result, raw = await client.identify_series("Dune", "Frank Herbert")

    assert result.series == "Dune Chronicles"
    assert result.series_number == 1
    assert raw["stop_reason"] == "tool_use"


@respx.mock
async def test_identify_series_raises_on_refusal() -> None:
    respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(
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

    async with httpx.AsyncClient() as http_client:
        client = AnthropicIdentificationClient(
            client=anthropic.AsyncAnthropic(api_key="test-key", http_client=http_client)
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


@respx.mock
async def test_grounded_identify_declares_web_search_and_records_grounding() -> None:
    route = respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(200, json=_grounded_then_identify_response(_IDENTIFY_INPUT))
    )

    async with httpx.AsyncClient() as http_client:
        client = AnthropicIdentificationClient(
            client=anthropic.AsyncAnthropic(api_key="test-key", http_client=http_client)
        )
        result, raw = await client.identify("some prompt", ground=True)

    import json

    payload = json.loads(route.calls.last.request.content)
    tool_names = {t.get("name") for t in payload["tools"]}
    assert "web_search" in tool_names and "identify_book" in tool_names
    assert "tool_choice" not in payload  # can't force a tool and allow search
    assert "Today's date is" in payload["system"]

    assert result.title == "Scion"
    assert result.series is None
    assert raw["grounding"]["queries"] == ["Scion James Islington series"]
    assert raw["grounding"]["results"] == ["Scion by James Islington - Wikipedia"]


@respx.mock
async def test_grounded_identify_falls_back_to_forced_call_on_refusal() -> None:
    responses = [
        httpx.Response(
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
        httpx.Response(200, json=_tool_use_response(_IDENTIFY_INPUT)),
    ]
    respx.post(MESSAGES_URL).mock(side_effect=responses)

    async with httpx.AsyncClient() as http_client:
        client = AnthropicIdentificationClient(
            client=anthropic.AsyncAnthropic(api_key="test-key", http_client=http_client)
        )
        result, raw = await client.identify("some prompt", ground=True)

    assert result.title == "Scion"
    assert raw["grounding"]["fell_back"] == "refusal"


@respx.mock
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
    responses = [
        httpx.Response(200, json=text_turn),
        httpx.Response(200, json=_tool_use_response(_IDENTIFY_INPUT)),
    ]
    respx.post(MESSAGES_URL).mock(side_effect=responses)

    async with httpx.AsyncClient() as http_client:
        client = AnthropicIdentificationClient(
            client=anthropic.AsyncAnthropic(api_key="test-key", http_client=http_client)
        )
        result, raw = await client.identify("some prompt", ground=True)

    import json

    forced_payload = json.loads(respx.calls.last.request.content)
    assert forced_payload["tool_choice"] == {"type": "tool", "name": "identify_book"}
    assert result.title == "Scion"
    assert raw["grounding"]["results"] == ["Scion - Goodreads"]


@respx.mock
async def test_ungrounded_identify_is_a_single_forced_call() -> None:
    route = respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(200, json=_tool_use_response(_IDENTIFY_INPUT))
    )

    async with httpx.AsyncClient() as http_client:
        client = AnthropicIdentificationClient(
            client=anthropic.AsyncAnthropic(api_key="test-key", http_client=http_client)
        )
        await client.identify("some prompt")  # ground defaults to False

    import json

    payload = json.loads(route.calls.last.request.content)
    assert payload["tool_choice"] == {"type": "tool", "name": "identify_book"}
    assert [t["name"] for t in payload["tools"]] == ["identify_book"]
    assert route.call_count == 1


@respx.mock
async def test_identify_raises_when_no_tool_use_block() -> None:
    respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(
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

    async with httpx.AsyncClient() as http_client:
        client = AnthropicIdentificationClient(
            client=anthropic.AsyncAnthropic(api_key="test-key", http_client=http_client)
        )
        with pytest.raises(AIIdentificationError):
            await client.identify("some prompt")
