"""A local Ollama instance — typically on a separate gaming PC, reached over
Tailscale — as a second LLM backend alongside `anthropic_client.py`. Used
only by the slow-drip LLM-tagging background job (`llm_tagging_service`),
never by the main identification pipeline. Structured output via Ollama's
`format: "json"` mode. See prompts/38-llm-tagging.md.

Two distinct failure modes, kept as separate exceptions because the caller
treats them very differently:
- `OllamaUnavailable`: the host itself didn't answer (off, asleep, Ollama not
  running, network hiccup). Not this book's fault — the caller should skip
  the tick silently and try again later, without marking anything failed.
- `OllamaBadResponse`: the host answered but the content was unusable
  (invalid JSON). The caller marks that specific book/step as failed with a
  retry backoff, since retrying the same call immediately would likely just
  fail the same way.
"""

import json
import logging

import httpx

logger = logging.getLogger(__name__)

_REACHABILITY_TIMEOUT = 5.0


class OllamaUnavailable(Exception):
    """The host didn't respond — retry later, don't blame the book."""


class OllamaBadResponse(Exception):
    """The host responded but not with usable JSON."""


class OllamaClient:
    def __init__(self, host: str, *, model: str, num_ctx: int, timeout_seconds: float) -> None:
        self._host = host.rstrip("/")
        self._model = model
        self._num_ctx = num_ctx
        self._timeout = timeout_seconds

    async def is_reachable(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=_REACHABILITY_TIMEOUT) as client:
                resp = await client.get(f"{self._host}/api/tags")
                return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def generate_json(self, *, system: str, prompt: str) -> dict:
        """One non-streaming `/api/generate` call in structured-output mode.
        Raises `OllamaUnavailable` for any transport/HTTP failure and
        `OllamaBadResponse` when the model's own output isn't valid JSON.

        `"think": False` is essential, not optional, for a reasoning model
        like qwen3: Ollama's `format: "json"` grammar-constrains the very
        first generated token to already be valid JSON, which collides head
        -on with a thinking model's trained habit of opening with a
        `<think>...</think>` block — observed in practice as the model
        getting forced to close an empty `{}` after 1-2 tokens, every time.
        Disabling thinking skips straight to the (grammar-constrained,
        therefore reliable) answer. Ignored harmlessly by models that don't
        support thinking."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._host}/api/generate",
                    json={
                        "model": self._model,
                        "system": system,
                        "prompt": prompt,
                        "format": "json",
                        "stream": False,
                        "think": False,
                        "options": {"num_ctx": self._num_ctx},
                    },
                )
                resp.raise_for_status()
                body = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OllamaUnavailable(f"Ollama request failed: {exc}") from exc

        raw = body.get("response", "")
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            raise OllamaBadResponse(f"model did not return valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise OllamaBadResponse("model's JSON response was not an object")
        return parsed
