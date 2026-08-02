import re

_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")
_LEADING_ARTICLE_RE = re.compile(r"^(the|a|an)\s+")
_SUBTITLE_SEPARATOR_RE = re.compile(r"\s*[:;].*$")
_TRAILING_PARENS_RE = re.compile(r"\s*\([^()]*\)\s*$")


def normalize(text: str | None) -> str:
    if not text:
        return ""
    return _NORMALIZE_RE.sub("", text.lower())


def normalize_title(text: str | None) -> str:
    """Like normalize(), but also drops a leading article ('the'/'a'/'an')
    and any colon/semicolon or trailing-parenthetical series/subtitle
    suffix. Sources routinely embed series info in the title field in
    incompatible ways for the same book — "Title : Series Name",
    "Title (Series Name 03)", "Title (Series Name Book 3)" — none of which
    is a real disagreement, and none should be scored as one."""
    if not text:
        return ""
    core_title = _SUBTITLE_SEPARATOR_RE.sub("", text.strip())
    while True:
        trimmed = _TRAILING_PARENS_RE.sub("", core_title).strip()
        if trimmed == core_title:
            break
        core_title = trimmed
    stripped = _LEADING_ARTICLE_RE.sub("", core_title.lower())
    return _NORMALIZE_RE.sub("", stripped)


def texts_match(a: str | None, b: str | None) -> bool:
    na, nb = normalize(a), normalize(b)
    return bool(na) and na == nb


def titles_match(a: str | None, b: str | None) -> bool:
    na, nb = normalize_title(a), normalize_title(b)
    return bool(na) and na == nb
