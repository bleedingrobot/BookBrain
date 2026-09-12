import re
from dataclasses import dataclass, field


@dataclass
class MetadataCandidate:
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    series: str | None = None
    series_number: float | None = None
    genre: str | None = None
    description: str | None = None
    language: str | None = None
    first_published: str | None = None
    isbn13: str | None = None
    isbn10: str | None = None
    source: str = ""


# Provider series strings are messy: "Mistborn", "Mistborn #1", "Mistborn, #1",
# "Mistborn (Book 1)", "The Wheel of Time (1)", "Discworld, Vol. 8". Split the
# trailing position off the name so the two land in the right fields; the name
# is still normalised the usual way (normalize_words) before any comparison.
_SERIES_NUMBER_TAIL_RE = re.compile(
    r"""[\s,;:]*         # optional separators
        \(?             # optional opening paren
        (?:\#|no\.?\s*|book\s+|bk\.?\s*|vol(?:ume)?\.?\s*|part\s+)?  # optional label
        (\d+(?:\.\d+)?)  # the number
        \)?             # optional closing paren
        \s*$""",
    re.IGNORECASE | re.VERBOSE,
)


def split_series_and_number(
    raw: str | None, fallback_number: float | None = None
) -> tuple[str | None, float | None]:
    """``"Mistborn #1"`` -> ``("Mistborn", 1.0)``. Returns ``(None, None)`` for
    an empty / number-only string. ``fallback_number`` is used when the name
    carries no trailing position (e.g. a provider that exposes the number in a
    separate field)."""
    if not raw or not raw.strip():
        return None, None
    text = raw.strip()
    number = fallback_number
    match = _SERIES_NUMBER_TAIL_RE.search(text)
    if match:
        name = text[: match.start()].strip(" ,;:()-")
        if not name:  # the whole string was just a position, e.g. "#1"
            return None, None
        text, number = name, float(match.group(1))
    text = text.strip(" ,;:()-")
    if not text or text.isdigit():
        return None, None
    return text, number
