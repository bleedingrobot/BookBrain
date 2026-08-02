import re

_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


def normalize(text: str | None) -> str:
    if not text:
        return ""
    return _NORMALIZE_RE.sub("", text.lower())


def texts_match(a: str | None, b: str | None) -> bool:
    na, nb = normalize(a), normalize(b)
    return bool(na) and na == nb
