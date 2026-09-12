"""Web-search-grounded Claude as a ground-truth voice.

Same model the pipeline uses, but a very different job: it is told today's date,
given a web_search tool, and instructed to verify every field against the live
web (publisher pages, Wikipedia, reliable bibliographies) before answering — the
"identify" framing from scratch, and an adversarial "verify this proposed
answer" framing. Two grounded calls that agree, plus Wikidata, is the corpus's
independent-consensus bar.

Not part of the pipeline. Runs only in scripts/build_truth.py; the result is
baked into the committed fixtures.
"""

from __future__ import annotations

import json

import anthropic

from app.core.config import get_settings
from tests.truth.types import TruthClaim

_MODEL = "claude-opus-5"
_TODAY_HINT = (
    "Today's date is 2026-09-06. Some books in this library were published after "
    "your training cutoff — do not answer from memory alone; search."
)

_SUBMIT_TOOL = {
    "name": "submit_identification",
    "strict": True,
    "description": "Report the verified bibliographic identity of the book.",
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "title": {"type": "string", "description": "The work's canonical title, no series/subtitle padding."},
            "author": {"type": ["string", "null"]},
            "series": {
                "type": ["string", "null"],
                "description": "ONE series name (1-5 words, no parentheticals, no 'aka', no "
                "alternates, no publication-order notes), or null if it is a genuine standalone. "
                "Only name a series you verified from a source in this search. If the book sits in "
                "both a tight sub-series and a looser universe, give the tight sub-series.",
            },
            "series_number": {
                "type": ["number", "null"],
                "description": "This book's position in that series if it is a numbered volume; null otherwise.",
            },
            "confidence": {"type": "number", "description": "0-1, how sure you are after searching."},
            "sources": {"type": "array", "items": {"type": "string"}, "description": "URLs you actually used."},
            "notes": {"type": "string"},
        },
        "required": ["title", "author", "series", "series_number", "confidence", "sources", "notes"],
    },
}


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=get_settings().anthropic_api_key)


class OutOfCredit(RuntimeError):
    """API credit exhausted — the caller should stop making grounded calls."""


def _run(prompt: str, *, source: str, max_rounds: int = 6) -> TruthClaim | None:
    client = _client()
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 5}, _SUBMIT_TOOL]
    messages: list[dict] = [{"role": "user", "content": prompt}]

    for _ in range(max_rounds):
        try:
            resp = client.messages.create(model=_MODEL, max_tokens=2500, tools=tools, messages=messages)
        except anthropic.BadRequestError as exc:
            if "credit balance is too low" in str(exc):
                raise OutOfCredit(str(exc)) from exc
            return None
        except anthropic.APIStatusError:
            return None
        if resp.stop_reason == "refusal":
            return None
        messages.append({"role": "assistant", "content": resp.content})
        submit = next(
            (b for b in resp.content if b.type == "tool_use" and b.name == "submit_identification"),
            None,
        )
        if submit is not None:
            data = submit.input if isinstance(submit.input, dict) else json.loads(submit.input)
            return _claim(source, data)
        if resp.stop_reason in ("pause_turn", "tool_use"):
            continue  # server tool results are already in resp.content
        return None  # ended without submitting
    return None


def _clean_series(s: str | None) -> str | None:
    if not s:
        return None
    # keep only the first clause — models still sometimes append "; also ..." or
    # "(publication order)" despite the schema note.
    s = s.split(";")[0].split(" aka ")[0].split(" (")[0].strip().strip(",")
    return s or None


def _claim(source: str, data: dict) -> TruthClaim:
    conf = data.get("confidence")
    num = data.get("series_number")
    return TruthClaim(
        source=source,
        title=(data.get("title") or None),
        author=(data.get("author") or None),
        series=_clean_series(data.get("series")),
        series_number=float(num) if isinstance(num, (int, float)) else None,
        url=(data.get("sources") or [None])[0],
        note=(f"conf={conf}; " + (data.get("notes") or "")).strip(),
    )


def identify(*, filename: str, title: str | None, author: str | None,
             isbn: str | None, snippet: str | None) -> TruthClaim | None:
    prompt = (
        f"{_TODAY_HINT}\n\n"
        "Identify this ebook precisely by searching the web. Verify the exact title, "
        "the author, and — carefully — whether it is a standalone or part of a numbered "
        "series, and which number. A wrongly-assigned series is the single most common "
        "mistake, so only report a series you can confirm from a source you found.\n\n"
        f"Filename: {filename}\n"
        f"EPUB title metadata: {title or '(none)'}\n"
        f"EPUB author metadata: {author or '(none)'}\n"
        f"EPUB ISBN: {isbn or '(none)'}\n"
        f"First text in the file: {(snippet or '')[:400] or '(none)'}\n\n"
        "Search, then call submit_identification."
    )
    return _run(prompt, source="web_claude_identify")


def verify(*, proposed: dict, filename: str, title: str | None, author: str | None,
           isbn: str | None) -> TruthClaim | None:
    prompt = (
        f"{_TODAY_HINT}\n\n"
        "An automated library tool proposes the identification below. Your job is to "
        "REFUTE it if you can. Search the web and check every field. Pay special "
        "attention to the series: if the proposed series is not a real published series "
        "this exact book belongs to, report series as null. If a field is right, keep it; "
        "if wrong, correct it.\n\n"
        f"Proposed: title={proposed.get('title')!r} author={proposed.get('author')!r} "
        f"series={proposed.get('series')!r} number={proposed.get('series_number')!r}\n\n"
        f"Filename: {filename}\n"
        f"EPUB title metadata: {title or '(none)'}\n"
        f"EPUB author metadata: {author or '(none)'}\n"
        f"EPUB ISBN: {isbn or '(none)'}\n\n"
        "Search, then call submit_identification with the verified/corrected answer."
    )
    return _run(prompt, source="web_claude_verify")
