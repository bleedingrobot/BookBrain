"""Structured parsing of an inbound ebook filename.

prompts/15 Stage C, finding F2. Tracker / libgen / Anna's-Archive / Calibre
filenames are information-dense — "Sanderson, Brandon - Mistborn 01 - The Final
Empire (2006).epub" carries author, series, position, title and year — but the
pipeline currently drops the raw string into the AI prompt unstructured and
never turns it into a candidate.

This is a *deterministic, conservative* parser. It never calls out anywhere, and
``FilenameGuess.confidence`` (0..1) is deliberately low unless several fields
parsed cleanly, so a shaky guess can be gated out before it influences
identification (``FilenameGuess.usable`` / ``FILENAME_GUESS_MIN_CONFIDENCE``).

Deliberately NOT treated as a series number: a trailing ``_1234`` on a Calibre
export ("The Final Empire - Brandon Sanderson_1234.epub") is Calibre's internal
book id, not volume 1234 — that bug has bitten before (the "Alexis Carew #301"
placeholder). The id is stripped and never surfaces as ``series_number``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Feeds identification only when at least this confident. Two cleanly parsed
# fields (title + author, or title + a numbered series) clears it; a bare title
# does not.
FILENAME_GUESS_MIN_CONFIDENCE = 0.5

# Mirror ``metadata_sanity.MAX_SERIES_NUMBER`` — anything larger in a filename is
# a Calibre id / placeholder, never a real volume number.
_MAX_SERIES_NUMBER = 50

_EXT_RE = re.compile(
    r"\.(epub|kepub|kpub|mobi|azw3?|prc|fb2|djvu|pdf|txt|rtf|lit|cbz|cbr)$", re.IGNORECASE
)

# Distribution-site / release-group cruft that shows up bracketed or parenthesised.
_SITE_TAG_RE = re.compile(
    r"""\s*[\(\[\{]\s*
        (?:
            z-?lib(?:\.org|rary)?|
            libgen(?:\.\w+)?|
            anna['’]s\s+archive|annas?\s+archive|
            readli\.net|bookfi(?:\.\w+)?|b-ok(?:\.\w+)?|
            oceanofpdf(?:\.\w+)?|pdfdrive|epubify|
            the\s+eye|myanonamouse|mam|
            retail|e?arc|proper|dedrm|calibre
        )
        [^\)\]\}]*[\)\]\}]""",
    re.IGNORECASE | re.VERBOSE,
)
_TRAILING_BRACKETS_RE = re.compile(r"\s*\[[^\]]*\]\s*$")
# Only an *enclosed* year — a bare "1984" / "2001" is very often the title.
_YEAR_RE = re.compile(r"[\(\[]\s*(1[5-9]\d{2}|20\d{2})\s*[\)\]]")
_CALIBRE_ID_RE = re.compile(r"_\d{2,7}$")
_SEP_RE = re.compile(r"\s+-\s+|\s+–\s+|\s+—\s+")

_SERIES_WITH_NUMBER_RE = re.compile(
    r"""^(?P<name>.*?)
        [\s,]*\(?
        (?:\#|book\s+|bk\.?\s*|vol(?:ume)?\.?\s*|part\s+|no\.?\s*)?
        (?P<num>\d{1,3}(?:\.\d+)?)
        \)?$""",
    re.IGNORECASE | re.VERBOSE,
)
# a trailing "(Series Name 3)" / "(Series Name, #3)" / "(Series Name Book 3)"
_TRAILING_SERIES_PAREN_RE = re.compile(
    r"""\s*\(
        (?P<name>[^()]+?)
        [\s,]+
        (?:\#|book\s+|bk\.?\s*|vol(?:ume)?\.?\s*|part\s+)?
        (?P<num>\d{1,3}(?:\.\d+)?)
        \)\s*$""",
    re.IGNORECASE | re.VERBOSE,
)

_NAME_STOPWORDS = frozenset(
    {"the", "a", "an", "of", "and", "or", "in", "on", "to", "for", "with", "his",
     "her", "their", "at", "from", "into", "how", "why", "what", "no"}
)


@dataclass
class FilenameGuess:
    title: str | None = None
    author: str | None = None
    series: str | None = None
    series_number: float | None = None
    year: int | None = None
    confidence: float = 0.0

    @property
    def usable(self) -> bool:
        return self.confidence >= FILENAME_GUESS_MIN_CONFIDENCE

    def as_prompt_line(self) -> str:
        bits: list[str] = []
        if self.title:
            bits.append(f"title={self.title!r}")
        if self.author:
            bits.append(f"author={self.author!r}")
        if self.series:
            s = self.series
            if self.series_number is not None:
                s += f" #{_fmt_num(self.series_number)}"
            bits.append(f"series={s!r}")
        if self.year:
            bits.append(f"year={self.year}")
        bits.append(f"confidence={self.confidence:.2f}")
        return ", ".join(bits)


def _fmt_num(n: float) -> str:
    return str(int(n) if float(n).is_integer() else n)


def _looks_like_person_name(text: str) -> bool:
    """A short, mostly-capitalised token with no title-ish stopwords and no
    colon. "Brandon Sanderson", "J. R. R. Tolkien", "Le Guin, Ursula K." pass;
    "The Way of Kings", "A Memory Called Empire" don't."""
    text = (text or "").strip()
    if not text or ":" in text or len(text) > 45:
        return False
    has_comma = "," in text
    core = text.split(",", 1)[0] if has_comma else text
    words = [w for w in re.split(r"\s+", core) if w]
    # A single bare word ("Neuromancer", "Foundation") is too weak to call a
    # name — real author strings are "First Last" or "Last, First".
    if not ((2 <= len(words) <= 4) or (has_comma and 1 <= len(words) <= 2)):
        return False
    for w in words:
        lw = w.strip(".").lower()
        if lw and lw in _NAME_STOPWORDS:
            return False
        if w[:1].islower():
            return False
    return has_comma or any(len(w.strip(".")) > 2 for w in words)


def _plausible_author(text: str) -> bool:
    """Looser than :func:`_looks_like_person_name` — accepts a single surname
    ("King", "Asimov"). Used for keeping the parsed author field and for
    confidence, not for deciding which side of "A - B" is the author."""
    text = (text or "").strip()
    if not text or ":" in text or len(text) > 45:
        return False
    core = text.split(",", 1)[0] if "," in text else text
    words = [w for w in re.split(r"\s+", core) if w]
    if not 1 <= len(words) <= 4:
        return False
    return all(
        w[:1].isupper() and w.strip(".").lower() not in _NAME_STOPWORDS for w in words
    )


def _strip_site_tags(name: str) -> str:
    prev = None
    while prev != name:
        prev = name
        name = _SITE_TAG_RE.sub(" ", name)
        name = _TRAILING_BRACKETS_RE.sub("", name)
    return re.sub(r"\s{2,}", " ", name).strip(" -_")


def _sane_number(num: float | None) -> float | None:
    if num is None or num <= 0 or num > _MAX_SERIES_NUMBER:
        return None
    return num


def _clean_series_name(name: str) -> str | None:
    name = name.strip(" ,-")
    if not name or name.isdigit() or "(" in name or ")" in name:
        return None
    return name


def _split_series_number(text: str) -> tuple[str, float | None]:
    m = _SERIES_WITH_NUMBER_RE.match(text.strip())
    if not m:
        return text.strip(" ,-"), None
    name = m.group("name").strip(" ,-")
    num = _sane_number(float(m.group("num")))
    if not name or num is None:
        return text.strip(" ,-"), None
    return name, num


def _pull_trailing_series(title: str) -> tuple[str, str | None, float | None]:
    m = _TRAILING_SERIES_PAREN_RE.search(title)
    if not m:
        return title, None, None
    name = _clean_series_name(m.group("name"))
    num = _sane_number(float(m.group("num")))
    if not name or num is None:
        # strip the junk parenthetical off the title anyway (it's a placeholder)
        return title[: m.start()].strip(" ,-") or title, None, None
    return title[: m.start()].strip(" ,-"), name, num


def _clean_token(text: str) -> str:
    text = text.strip(" ._-")
    # "Sanderson, Brandon" -> "Brandon Sanderson" only for a clean two-part name
    if text.count(",") == 1:
        last, first = (p.strip() for p in text.split(","))
        if last and first and " " not in last and len(first.split()) <= 3:
            return f"{first} {last}"
    return text


def parse_book_filename(name: str) -> FilenameGuess:
    guess = FilenameGuess()
    if not name:
        return guess

    stem = _EXT_RE.sub("", name.strip())
    stem = _CALIBRE_ID_RE.sub("", stem)  # drop a trailing Calibre book id
    if "_" in stem and " " not in stem:
        stem = stem.replace("_", " ")
    # all-lowercase tracker names ("brandon sanderson - the final empire") —
    # title-case so the name heuristics and the stored value are usable.
    if stem and not any(c.isupper() for c in stem):
        stem = re.sub(r"[A-Za-z][a-z']*", lambda m: m.group(0).capitalize(), stem)
    stem = _strip_site_tags(stem)

    ym = _YEAR_RE.search(stem)
    if ym:
        guess.year = int(ym.group(1))
        stem = (stem[: ym.start()] + " " + stem[ym.end():]).strip(" ()[]-_")

    stem = re.sub(r"\s{2,}", " ", stem).strip(" -_")
    if not stem:
        return guess

    # A trailing "(Series NN)" anywhere is unambiguous — pull it before the
    # author/title split so it can't be mistaken for part of a name.
    stem, paren_series, paren_num = _pull_trailing_series(stem)
    if paren_series:
        guess.series, guess.series_number = paren_series, paren_num

    parts = [p.strip() for p in _SEP_RE.split(stem) if p.strip()]

    def _set_series(name: str | None, num: float | None) -> None:
        if name:
            guess.series, guess.series_number = name, num

    if len(parts) == 1:
        title, s, n = _pull_trailing_series(parts[0])
        guess.title = _clean_token(title) or None
        _set_series(s, n)
    elif len(parts) == 2:
        a, b = parts
        a_name, b_name = _looks_like_person_name(a), _looks_like_person_name(b)
        a_series, a_num = _split_series_number(a)
        b_series, b_num = _split_series_number(b)
        if b_name and not a_name:
            # "The Final Empire - Brandon Sanderson"
            guess.title, guess.author = _clean_token(a), _clean_token(b)
        elif a_num is not None and not a_name:
            # "Mistborn 01 - The Final Empire"
            _set_series(a_series, a_num)
            guess.title = _clean_token(b)
        else:
            # default, and "Brandon Sanderson - Mistborn 01"
            guess.author = _clean_token(a)
            if b_num is not None and not b_name:
                _set_series(b_series, b_num)
            else:
                guess.title = _clean_token(b)
        if guess.title:
            guess.title, s, n = _pull_trailing_series(guess.title)
            _set_series(s, n)
    else:
        # 3+ parts. Common: "Author - Series NN - Title" (+ maybe more).
        author, middle, title = parts[0], parts[1], parts[-1]
        m_series, m_num = _split_series_number(middle)
        guess.author = _clean_token(author)
        guess.title = _clean_token(title)
        if m_num is not None:
            _set_series(m_series, m_num)
        t, s, n = _pull_trailing_series(guess.title or "")
        if s:
            guess.title = t
            _set_series(s, n)

    guess.title = (guess.title or None) if not (guess.title or "").isdigit() else None
    if guess.author and not _plausible_author(guess.author):
        # not even plausibly a name — drop it rather than feed the pipeline a
        # title-as-author.
        guess.author = None

    guess.confidence = _confidence(guess, len(parts))
    return guess


def _confidence(g: FilenameGuess, part_count: int) -> float:
    if not g.title and not g.series:
        return 0.0
    score = 0.0
    if g.title:
        score += 0.35
    if g.author and _looks_like_person_name(g.author):
        score += 0.30
    elif g.author and _plausible_author(g.author):
        score += 0.18
    if g.series and g.series_number is not None:
        score += 0.20
    elif g.series:
        score += 0.08
    if g.year:
        score += 0.08
    if part_count >= 2:
        score += 0.10
    if part_count == 1 and not g.series:
        score = min(score, 0.25)  # a bare title is a weak guess
    return round(min(score, 1.0), 2)
