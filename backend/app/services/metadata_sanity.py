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
