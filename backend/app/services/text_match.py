import re

_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")
_LEADING_ARTICLE_RE = re.compile(r"^(the|a|an)\s+")
_SUBTITLE_SEPARATOR_RE = re.compile(r"\s*[:;].*$")
_TRAILING_PARENS_RE = re.compile(r"\s*\([^()]*\)\s*$")
_WORD_RE = re.compile(r"[a-z0-9]+")


def normalize(text: str | None) -> str:
    if not text:
        return ""
    return _NORMALIZE_RE.sub("", text.lower())


def _strip_trailing_parens(core_title: str) -> str:
    while True:
        trimmed = _TRAILING_PARENS_RE.sub("", core_title).strip()
        if trimmed == core_title:
            return core_title
        core_title = trimmed


def normalize_title(text: str | None) -> str:
    """Like normalize(), but also drops a leading article ('the'/'a'/'an')
    and any colon/semicolon or trailing-parenthetical series/subtitle
    suffix. Sources routinely embed series info in the title field in
    incompatible ways for the same book — "Title : Series Name",
    "Title (Series Name 03)", "Title (Series Name Book 3)" — none of which
    is a real disagreement, and none should be scored as one.

    This is the *loose* comparator — right for confidence scoring and
    provider corroboration, where a false match only moves a number. It is
    the WRONG choice for deciding whether two books are the same row: see
    normalize_title_strict and its use in book_repository.resolve_book."""
    if not text:
        return ""
    core_title = _SUBTITLE_SEPARATOR_RE.sub("", text.strip())
    core_title = _strip_trailing_parens(core_title)
    stripped = _LEADING_ARTICLE_RE.sub("", core_title.lower())
    return _NORMALIZE_RE.sub("", stripped)


def normalize_title_strict(text: str | None) -> str:
    """Like normalize_title(), but keeps the full title — it does NOT strip
    a ':'/';' subtitle. Case, punctuation and a leading article are still
    folded, and a trailing "(Series Name 3)" parenthetical is still dropped
    (the distinguishing part of "Heir to the Empire (Thrawn 1)" vs "Dark
    Force Rising (Thrawn 2)" is *before* the parens; only the colon form
    hides it *after* the separator).

    Use this for row-identity decisions ("is this the same Book row?").
    "Mistborn: The Final Empire" and "Mistborn: The Well of Ascension" are
    genuinely different books and must stay distinct rows; the loose
    normalize_title collapses both to "mistborn"."""
    if not text:
        return ""
    core_title = _strip_trailing_parens(text.strip())
    stripped = _LEADING_ARTICLE_RE.sub("", core_title.lower())
    return _NORMALIZE_RE.sub("", stripped)


def normalize_words(text: str | None) -> frozenset[str]:
    """Order-independent, case/punctuation-insensitive word set. Series (and
    author) names commonly show up as the same words in a different
    arrangement across sources/AI calls — "Cirque Du Freak (The Saga of
    Darren Shan)" vs "The Saga of Darren Shan (Cirque Du Freak)" vs the same
    without parens — which normalize()'s order-preserving concatenation
    doesn't catch, but a plain word set does."""
    if not text:
        return frozenset()
    return frozenset(_WORD_RE.findall(text.lower()))


def texts_match(a: str | None, b: str | None) -> bool:
    na, nb = normalize(a), normalize(b)
    return bool(na) and na == nb


def titles_match(a: str | None, b: str | None) -> bool:
    na, nb = normalize_title(a), normalize_title(b)
    return bool(na) and na == nb
