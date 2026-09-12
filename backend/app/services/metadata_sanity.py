"""Deterministic sanity clamps applied to identification output before it is
trusted — the "third defensive layer" the 2026-09-06 review kept asking for.

The AI (and, less often, an EPUB's own metadata or a Calibre placeholder)
routinely emits a `series_number` that is obviously junk: a scrape artefact
like 301, a negative value, a row index. `claude-opus-5` frequently *reasons*
that the number is bogus and then emits it anyway. None of the reactive tools
(`/correct`, series merge, the Re-identify Audit) stop such a value from
auto-organising silently in the first place; this does.
"""

import logging
import re

logger = logging.getLogger(__name__)

# Deliberately generous — real series longer than this exist but are rare, and
# a human can `/correct` the edge case. The point is to catch 3-digit scrape
# artefacts ("Alexis Carew #301"), not to police long-runners.
MAX_SERIES_NUMBER = 50


def sane_series_number(series: str | None, series_number: float | None) -> float | None:
    """Return a cleaned `series_number` for the resolved `(series, number)` pair.

    - No series → there is nothing for a number to refer to → ``None``.
    - Negative, zero-or-below, or absurdly large (> ``MAX_SERIES_NUMBER``) →
      ``None`` (the *number* is junk; the caller keeps the series *name*).
    - A fractional number (``3.5``, novella-style) is legitimate and kept.
    """
    if series_number is None:
        return None
    if not series:
        return None
    if series_number <= 0 or series_number > MAX_SERIES_NUMBER:
        return None
    return series_number


# prompts/15 Stage E — placeholder / junk metadata detection.
#
# EPUB metadata is frequently a stub: "Unknown", "Calibre", "book1", a bare
# number, the publisher's name in the author field. Such a value must never
# take the deterministic fast path and must never auto-organise on the EPUB's
# own say-so.

_PLACEHOLDER_TITLES = frozenset(
    {
        "unknown", "unknown title", "untitled", "no title", "title", "titlepage",
        "title page", "calibre", "epub", "ebook", "book", "novel", "cover",
        "cover page", "document", "default", "none", "n a", "tbd", "draft",
        "new document", "microsoft word", "sample",
    }
)
_PLACEHOLDER_AUTHORS = frozenset(
    {
        "unknown", "unknown author", "author unknown", "anonymous", "anon",
        "author", "various", "various authors", "multiple authors", "none",
        "n a", "self", "unnamed", "no author", "admin", "user", "calibre",
    }
)
# A publisher name sitting in the author field is a classic Calibre-import
# artefact. Small, high-signal list — extend as real cases turn up.
_PUBLISHER_NAMES = frozenset(
    {
        "tor", "tor books", "orbit", "orbit books", "penguin", "penguin books",
        "random house", "harpercollins", "harper collins", "harper", "hachette",
        "simon schuster", "simon and schuster", "macmillan", "st martins press",
        "gollancz", "baen", "baen books", "del rey", "bantam", "vintage",
        "harper voyager", "voyager", "angry robot", "subterranean press",
        "smashwords", "draft2digital", "kindle direct publishing", "createspace",
        "audible studios", "brilliance audio",
    }
)
_BOOK_N_RE = re.compile(r"^(?:book|bk|volume|vol|part|pt|chapter|ch|no)\.?\s*#?\s*\d+$")
_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _fold(text: str) -> str:
    return _ALNUM_RE.sub(" ", text.strip().lower()).strip()


def _squash(text: str) -> str:
    return _ALNUM_RE.sub("", text.strip().lower())


_PLACEHOLDER_TITLE_KEYS = {_squash(t) for t in _PLACEHOLDER_TITLES}
_PLACEHOLDER_AUTHOR_KEYS = {_squash(a) for a in _PLACEHOLDER_AUTHORS}
_PUBLISHER_KEYS = {_squash(p) for p in _PUBLISHER_NAMES}


def looks_like_placeholder_title(
    title: str | None, *, corroborated: bool = False
) -> bool:
    """``corroborated`` = an ISBN or a provider/AI match backs this string, in
    which case a genuinely short real title ("It", "V.", "S.") is fine."""
    if title is None or not title.strip():
        return True
    folded, squashed = _fold(title), _squash(title)
    if not squashed:
        return True
    if squashed in _PLACEHOLDER_TITLE_KEYS:
        return True
    if _BOOK_N_RE.match(folded):
        return True
    # All-digits ("12345") or very short ("It", "V.") are only real when an
    # ISBN or a provider/AI match backs them ("1984", "S.").
    if not corroborated and (squashed.isdigit() or len(squashed) <= 2):
        return True
    return False


def looks_like_placeholder_author(author: str | None) -> bool:
    if author is None or not author.strip():
        return True
    squashed = _squash(author)
    if not squashed:
        return True
    if squashed in _PLACEHOLDER_AUTHOR_KEYS:
        return True
    if squashed in _PUBLISHER_KEYS:
        return True
    if squashed.isdigit():
        return True
    return False


def has_placeholder_metadata(title: str | None, author: str | None, *, corroborated: bool = False) -> bool:
    return looks_like_placeholder_title(title, corroborated=corroborated) or looks_like_placeholder_author(author)


def clamp_series_number(
    series: str | None, series_number: float | None, raw_response: dict
) -> float | None:
    """Apply :func:`sane_series_number` and, when it changes the value, record
    the original under ``raw_response["series_number_clamped"]`` (so the
    Re-identify Audit and Activity trail can show it) and log at INFO."""
    cleaned = sane_series_number(series, series_number)
    if cleaned != series_number:
        raw_response["series_number_clamped"] = series_number
        logger.info(
            "series_number clamped from %r to %r (series=%r)",
            series_number,
            cleaned,
            series,
        )
    return cleaned
