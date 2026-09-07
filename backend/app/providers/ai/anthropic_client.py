import datetime as _dt

import anthropic
from anthropic import DefaultAioHttpClient

from app.core.config import get_settings
from app.providers.ai.schema import (
    AUDIT_BOOK_IDENTITY_TOOL,
    IDENTIFY_BOOK_TOOL,
    IDENTIFY_SERIES_TOOL,
    PROPOSE_SERIES_MERGE_TOOL,
    RESOLVE_BOOK_REQUEST_TOOL,
    WEB_SEARCH_TOOL,
    WEB_SEARCH_TOOL_BASIC,
)
from app.providers.ai.types import (
    AIAuditResult,
    AIBookRequestResult,
    AIIdentificationResult,
    AISeriesMergeResult,
    AISeriesResult,
)


class AIIdentificationError(Exception):
    pass


def _web_search_tool(model: str, max_uses: int) -> dict:
    """The web_search server-tool block for this model. The dynamic-filtering
    `_20260209` variant needs Opus 4.6+ / Sonnet 4.6+ / *-5; Haiku 4.5 and
    older take the basic `_20250305`. An unknown/newer id is assumed capable."""
    m = model.lower()
    basic = ("haiku" in m) or any(
        m.startswith(p) for p in ("claude-3", "claude-2", "claude-instant")
    )
    tool = WEB_SEARCH_TOOL_BASIC if basic else WEB_SEARCH_TOOL
    return {**tool, "max_uses": max_uses}


def _collect_grounding(response, grounding: dict) -> None:
    """Pull the web_search queries the model ran and the titles it saw out of
    a grounded response, into ``grounding`` for the review UI ("verified
    against: …"). Best-effort — never raises on an unexpected block shape."""
    for block in getattr(response, "content", []) or []:
        btype = getattr(block, "type", None)
        if btype == "server_tool_use" and getattr(block, "name", None) == "web_search":
            query = (getattr(block, "input", None) or {}).get("query")
            if query:
                grounding["queries"].append(query)
        elif btype == "web_search_tool_result":
            content = getattr(block, "content", None)
            if isinstance(content, list):
                for item in content:
                    title = getattr(item, "title", None)
                    if title:
                        grounding["results"].append(title)
            else:
                code = getattr(content, "error_code", None)
                if code:
                    grounding.setdefault("errors", []).append(code)


class AnthropicIdentificationClient:
    """Thin wrapper over the Anthropic Messages API. SPEC.md's v1
    simplification: hard-code to Anthropic, kept behind this one class so
    swapping providers later doesn't touch business logic."""

    def __init__(
        self, client: anthropic.AsyncAnthropic | None = None, model: str | None = None
    ) -> None:
        settings = get_settings()
        self._client = client or anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key, http_client=DefaultAioHttpClient()
        )
        self.model_name = model or settings.anthropic_model

    async def identify(
        self, prompt: str, *, ground: bool = False
    ) -> tuple[AIIdentificationResult, dict]:
        """Identify a book from the assembled evidence prompt.

        ``ground=True`` (prompts/15 Stage A) lets the model call the web_search
        server tool to verify its answer against the live web before committing
        — the caller (``identification_service.should_ground``) decides when
        that's worth the per-search cost. A refusal or an unusable search on the
        grounded path falls back to a plain forced identification rather than
        erroring.
        """
        if ground:
            try:
                return await self._identify_grounded(prompt)
            except anthropic.APIStatusError:
                # e.g. the model doesn't support the web_search server tool —
                # don't fail the identification, just do it un-grounded.
                pass
        return await self._identify_forced(prompt)

    async def _identify_forced(
        self, prompt: str, messages: list[dict] | None = None
    ) -> tuple[AIIdentificationResult, dict]:
        response = await self._client.messages.create(
            model=self.model_name,
            max_tokens=1024,
            tools=[IDENTIFY_BOOK_TOOL],
            tool_choice={"type": "tool", "name": "identify_book"},
            messages=messages or [{"role": "user", "content": prompt}],
        )

        if response.stop_reason == "refusal":
            raise AIIdentificationError("model declined to identify this book")

        for block in response.content:
            if block.type == "tool_use" and block.name == "identify_book":
                return AIIdentificationResult.from_tool_input(block.input), response.to_dict()

        raise AIIdentificationError("model did not return the identify_book tool call")

    async def _identify_grounded(
        self, prompt: str
    ) -> tuple[AIIdentificationResult, dict]:
        settings = get_settings()
        today = _dt.date.today().isoformat()
        system = (
            f"Today's date is {today}. Some of the books you are asked to identify "
            "were published after your training cutoff, so your own memory of them "
            "may be wrong or absent. Before you call identify_book, use web_search "
            "to verify the title, the author, the first-publication year, and — "
            "most importantly — whether the book belongs to a series and its "
            "number in it. Do not assert a series from memory: confirm it with a "
            "search, and if you cannot confirm it, report series as null rather "
            "than guessing. If search is unavailable or the results are "
            "inconclusive, identify the book from the evidence alone and set "
            "needs_human_review accordingly."
        )
        web_search = _web_search_tool(self.model_name, settings.ai_web_search_max_uses)
        messages: list[dict] = [{"role": "user", "content": prompt}]
        grounding: dict = {"queries": [], "results": []}

        for _ in range(4):
            response = await self._client.messages.create(
                model=self.model_name,
                max_tokens=4096,
                system=system,
                tools=[IDENTIFY_BOOK_TOOL, web_search],
                messages=messages,
            )

            if response.stop_reason == "refusal":
                result, raw = await self._identify_forced(prompt)
                raw["grounding"] = {**grounding, "fell_back": "refusal"}
                return result, raw

            _collect_grounding(response, grounding)

            identify_block = next(
                (
                    b
                    for b in response.content
                    if b.type == "tool_use" and b.name == "identify_book"
                ),
                None,
            )
            if identify_block is not None:
                raw = response.to_dict()
                raw["grounding"] = grounding
                return AIIdentificationResult.from_tool_input(identify_block.input), raw

            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "pause_turn":
                continue  # server tool hit its internal limit — resume the turn

            # end_turn / max_tokens / a stray tool_use: the model finished (or
            # stalled) without committing. Force the structured answer now,
            # keeping the searched-up context.
            messages.append(
                {
                    "role": "user",
                    "content": "Now call identify_book with your final answer.",
                }
            )
            result, raw = await self._identify_forced(prompt, messages=messages)
            raw["grounding"] = grounding
            return result, raw

        # Loop exhausted without a committed answer — fall back to a plain pass.
        result, raw = await self._identify_forced(prompt)
        raw["grounding"] = {**grounding, "fell_back": "max_iterations"}
        return result, raw

    async def resolve_book_request(self, text: str) -> AIBookRequestResult:
        response = await self._client.messages.create(
            model=self.model_name,
            max_tokens=512,
            tools=[RESOLVE_BOOK_REQUEST_TOOL],
            tool_choice={"type": "tool", "name": "resolve_book_request"},
            messages=[
                {
                    "role": "user",
                    "content": f"A book the user wants to add to their wishlist:\n\n{text}",
                }
            ],
        )
        if response.stop_reason == "refusal":
            raise AIIdentificationError("model declined to resolve this request")
        for block in response.content:
            if block.type == "tool_use" and block.name == "resolve_book_request":
                return AIBookRequestResult.from_tool_input(block.input)
        raise AIIdentificationError("model did not return the resolve_book_request tool call")

    async def describe(self, title: str, author: str | None) -> str | None:
        """A short back-cover-style blurb from the model's own knowledge, or
        None if it doesn't recognise the book. Used only to fill descriptions
        that neither the EPUB nor any metadata provider had."""
        who = f'"{title}" by {author}' if author else f'"{title}"'
        prompt = (
            f"Write a 2-3 sentence back-cover blurb for the book {who}, using only "
            "what you actually know about this specific book — no invented plot "
            "points. If you don't recognise it, or aren't confident it's a real "
            "published book, reply with exactly: UNKNOWN"
        )
        response = await self._client.messages.create(
            model=self.model_name,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        if response.stop_reason == "refusal":
            return None
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        if not text or text.upper().startswith("UNKNOWN"):
            return None
        return text

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

    async def audit_book_identity(self, prompt: str) -> AIAuditResult:
        response = await self._client.messages.create(
            model=self.model_name,
            max_tokens=1024,
            tools=[AUDIT_BOOK_IDENTITY_TOOL],
            tool_choice={"type": "tool", "name": "audit_book_identity"},
            messages=[{"role": "user", "content": prompt}],
        )
        if response.stop_reason == "refusal":
            raise AIIdentificationError("model declined to audit this book")
        for block in response.content:
            if block.type == "tool_use" and block.name == "audit_book_identity":
                return AIAuditResult.from_tool_input(block.input)
        raise AIIdentificationError("model did not return the audit_book_identity tool call")

    async def propose_series_merge(self, prompt: str) -> AISeriesMergeResult:
        response = await self._client.messages.create(
            model=self.model_name,
            max_tokens=1024,
            tools=[PROPOSE_SERIES_MERGE_TOOL],
            tool_choice={"type": "tool", "name": "propose_series_merge"},
            messages=[{"role": "user", "content": prompt}],
        )
        if response.stop_reason == "refusal":
            raise AIIdentificationError("model declined to compare these series")
        for block in response.content:
            if block.type == "tool_use" and block.name == "propose_series_merge":
                return AISeriesMergeResult.from_tool_input(block.input)
        raise AIIdentificationError("model did not return the propose_series_merge tool call")
