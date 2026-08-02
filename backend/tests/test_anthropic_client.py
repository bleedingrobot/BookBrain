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
