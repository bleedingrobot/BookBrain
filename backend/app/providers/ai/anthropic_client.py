import anthropic

from app.core.config import get_settings
from app.providers.ai.schema import IDENTIFY_BOOK_TOOL
from app.providers.ai.types import AIIdentificationResult


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
