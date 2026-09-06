import re
from difflib import SequenceMatcher

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


# prompts/15 Stage J — author-name canonicalisation.
#
# "J.R.R. Tolkien" / "J. R. R. Tolkien" / "Tolkien, J.R.R." must resolve to one
# Author row, not three. Multi-word surname particles keep "Le Guin, Ursula K."
# reading as Last-comma-First rather than a two-author list.
_SURNAME_PARTICLES = frozenset(
    {"de", "del", "della", "der", "den", "van", "von", "le", "la", "du", "di",
     "da", "dos", "das", "st", "saint", "mac", "mc", "o", "ter", "ten", "af",
     "av", "bin", "ibn", "al", "ap"}
)
_COAUTHOR_SPLIT_RE = re.compile(r"\s*(?:;|&|\band\b|\bwith\b|\bet\b|/)\s*", re.IGNORECASE)
_INITIAL_RE = re.compile(r"^[a-z]$")


def primary_author_name(name: str) -> str:
    """The first credited author from a "A & B" / "A; B" / "A and B" list, or a
    "Last, First" pair reordered to "First Last". A bare "First Last" is
    returned unchanged. Used as the stored *display* name so an Author row reads
    "Ursula K. Le Guin", not "Le Guin, Ursula K." or "Le Guin, Ursula K. & …"."""
    name = name.strip()
    first = _COAUTHOR_SPLIT_RE.split(name)[0].strip()
    if first.count(",") == 1:
        last, given = (p.strip() for p in first.split(","))
        last_tokens = last.lower().split()
        given_tokens = given.split()
        # "Weis, Margaret" (1-token surname) / "Le Guin, Ursula K." (surname
        # starts with a particle) / "Tolkien, J.R.R." is Last, First.
        if last and given and (len(last_tokens) == 1 or last_tokens[0] in _SURNAME_PARTICLES):
            return f"{given} {last}"
        # otherwise the comma separates two authors ("Margaret Weis, Tracy
        # Hickman") — keep only the first, unless the second half is just an
        # initial trail belonging to the first ("King, S." already handled above).
        if len(given_tokens) >= 2 or (given_tokens and len(given_tokens[0]) > 2):
            return last
    return first


def normalize_person_name(name: str | None) -> str:
    """Canonical match key for an author. Folds case/punctuation, reorders
    "Last, First", takes the first of a co-author list, joins a run of initials
    ("J. R. R." -> "jrr"), and drops a lone interior middle initial so
    "Iain M. Banks" matches "Iain Banks"."""
    if not name or not name.strip():
        return ""
    tokens = [t for t in re.split(r"[^a-z0-9]+", primary_author_name(name).lower()) if t]
    if not tokens:
        return ""
    # Join a run of initials: [j, r, r, tolkien] -> [jrr, tolkien].
    merged: list[str] = []
    from_initials: list[bool] = []
    for tok in tokens:
        if len(tok) == 1 and merged and from_initials[-1]:
            merged[-1] += tok
        else:
            merged.append(tok)
            from_initials.append(len(tok) == 1)
    # Drop a lone interior middle initial ("iain m banks" -> "iain banks");
    # keep a leading one and keep joined runs ("jrr tolkien").
    if len(merged) >= 3:
        merged = [merged[0]] + [t for t in merged[1:-1] if len(t) > 1] + [merged[-1]]
    return " ".join(merged)


def person_sort_name(name: str | None) -> str:
    """"Brandon Sanderson" -> "Sanderson, Brandon" for Author.sort_name. Leaves
    an already-"Last, First" string and a co-authored credit alone."""
    if not name or not name.strip():
        return ""
    name = name.strip()
    if "," in name or _COAUTHOR_SPLIT_RE.search(name):
        return name
    tokens = name.split()
    if len(tokens) < 2:
        return name
    # pull any surname particles ("Le", "van", "de la") into the surname
    cut = len(tokens) - 1
    while cut > 1 and tokens[cut - 1].lower().strip(".") in _SURNAME_PARTICLES:
        cut -= 1
    return f"{' '.join(tokens[cut:])}, {' '.join(tokens[:cut])}"


def texts_match(a: str | None, b: str | None) -> bool:
    na, nb = normalize(a), normalize(b)
    return bool(na) and na == nb


def titles_match(a: str | None, b: str | None) -> bool:
    na, nb = normalize_title(a), normalize_title(b)
    return bool(na) and na == nb


def title_similarity(a: str | None, b: str | None) -> float:
    """0..1 character-level similarity (difflib ratio) of two titles, compared
    with the *strict* normaliser — leading article folded, but the subtitle
    kept. Used where ``titles_match``'s "strip everything after a colon" is too
    loose to trust: "Mistborn: The Final Empire" and "Mistborn: The Well of
    Ascension" both pass ``titles_match`` but score ~0.6 here."""
    na, nb = normalize_title_strict(a), normalize_title_strict(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()
