import httpx
import pytest
import respx

from app.providers.ai.ollama_client import OllamaBadResponse, OllamaClient, OllamaUnavailable

HOST = "http://100.64.0.1:11434"


def _client() -> OllamaClient:
    return OllamaClient(HOST, model="qwen3:14b", num_ctx=8192, timeout_seconds=5.0)


@respx.mock
async def test_is_reachable_true_on_200() -> None:
    respx.get(f"{HOST}/api/tags").mock(return_value=httpx.Response(200, json={"models": []}))
    assert await _client().is_reachable() is True


@respx.mock
async def test_is_reachable_false_on_non_200() -> None:
    respx.get(f"{HOST}/api/tags").mock(return_value=httpx.Response(500))
    assert await _client().is_reachable() is False


@respx.mock
async def test_is_reachable_false_on_connection_error() -> None:
    respx.get(f"{HOST}/api/tags").mock(side_effect=httpx.ConnectError("refused"))
    assert await _client().is_reachable() is False


@respx.mock
async def test_generate_json_happy_path() -> None:
    respx.post(f"{HOST}/api/generate").mock(
        return_value=httpx.Response(200, json={"response": '{"genres": ["Fantasy"]}'})
    )
    result = await _client().generate_json(system="sys", prompt="prompt")
    assert result == {"genres": ["Fantasy"]}


@respx.mock
async def test_generate_json_sends_expected_body() -> None:
    route = respx.post(f"{HOST}/api/generate").mock(
        return_value=httpx.Response(200, json={"response": "{}"})
    )
    await _client().generate_json(system="sys", prompt="prompt")
    body = route.calls.last.request.content
    import json

    payload = json.loads(body)
    assert payload["model"] == "qwen3:14b"
    assert payload["format"] == "json"
    assert payload["stream"] is False
    assert payload["think"] is False
    assert payload["options"]["num_ctx"] == 8192


@respx.mock
async def test_generate_json_raises_unavailable_on_transport_error() -> None:
    respx.post(f"{HOST}/api/generate").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(OllamaUnavailable):
        await _client().generate_json(system="sys", prompt="prompt")


@respx.mock
async def test_generate_json_raises_unavailable_on_http_error_status() -> None:
    respx.post(f"{HOST}/api/generate").mock(return_value=httpx.Response(500))
    with pytest.raises(OllamaUnavailable):
        await _client().generate_json(system="sys", prompt="prompt")


@respx.mock
async def test_generate_json_raises_bad_response_on_invalid_json() -> None:
    respx.post(f"{HOST}/api/generate").mock(
        return_value=httpx.Response(200, json={"response": "not json"})
    )
    with pytest.raises(OllamaBadResponse):
        await _client().generate_json(system="sys", prompt="prompt")


@respx.mock
async def test_generate_json_raises_bad_response_on_non_object_json() -> None:
    respx.post(f"{HOST}/api/generate").mock(
        return_value=httpx.Response(200, json={"response": "[1, 2, 3]"})
    )
    with pytest.raises(OllamaBadResponse):
        await _client().generate_json(system="sys", prompt="prompt")


@respx.mock
async def test_generate_json_sends_schema_as_format_when_given() -> None:
    import json

    route = respx.post(f"{HOST}/api/generate").mock(
        return_value=httpx.Response(200, json={"response": "{}"})
    )
    schema = {"type": "object", "properties": {"genres": {"type": "array"}}, "required": ["genres"]}
    await _client().generate_json(system="sys", prompt="prompt", schema=schema)
    assert json.loads(route.calls.last.request.content)["format"] == schema
