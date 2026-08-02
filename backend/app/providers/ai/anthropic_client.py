import anthropic

from app.core.config import get_settings
from app.providers.ai.schema import IDENTIFY_BOOK_TOOL, IDENTIFY_SERIES_TOOL
from app.providers.ai.types import AIIdentificationResult, AISeriesResult


class AIIdentificationError(Exception):
    pass


class AnthropicIdentificationClient:
    """Thin wrapper over the Anthropic Messages API. SPEC.md's v1
    simplification: hard-code to Anthropic, kept behind this one class so
    swapping providers later doesn't touch business logic."""

    def __init__(
        self, client: anthropic.AsyncAnthropic | None = None, model: str | None = None
    ) -> None:
        settings = get_settings()
        self._client = client or anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        self.model_name = model or settings.anthropic_model

    async def identify(self, prompt: str) -> tuple[AIIdentificationResult, dict]:
        response = await self._client.messages.create(
            model=self.model_name,
            max_tokens=1024,
            tools=[IDENTIFY_BOOK_TOOL],
            tool_choice={"type": "tool", "name": "identify_book"},
            messages=[{"role": "user", "content": prompt}],
        )

        if response.stop_reason == "refusal":
            raise AIIdentificationError("model declined to identify this book")

        for block in response.content:
            if block.type == "tool_use" and block.name == "identify_book":
                return AIIdentificationResult.from_tool_input(block.input), response.to_dict()

        raise AIIdentificationError("model did not return the identify_book tool call")

    async def identify_series(self, title: str, author: str | None) -> tuple[AISeriesResult, dict]:
        who = f'"{title}" by {author}' if author else f'"{title}"'
        prompt = (
            f"Is {who} part of a book series? Use your general bibliographic "
            "knowledge — the EPUB file and metadata providers had no series "
            "information for this book. If it's standalone or you aren't sure, "
            "report series as null."
        )
        response = await self._client.messages.create(
            model=self.model_name,
            max_tokens=256,
            tools=[IDENTIFY_SERIES_TOOL],
            tool_choice={"type": "tool", "name": "identify_series"},
            messages=[{"role": "user", "content": prompt}],
        )

        if response.stop_reason == "refusal":
            raise AIIdentificationError("model declined to look up series info")

        for block in response.content:
            if block.type == "tool_use" and block.name == "identify_series":
                return AISeriesResult.from_tool_input(block.input), response.to_dict()

        raise AIIdentificationError("model did not return the identify_series tool call")
