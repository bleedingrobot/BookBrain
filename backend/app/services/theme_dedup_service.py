"""prompts/47 Phase A.3 — collapse the LLM's many phrasings of the same theme
("loss and grief" / "grief and loss", "moral dilemma" / "moral dilemmas")
into one canonical label per idea, so themes can finally be compared across
books instead of being excluded everywhere (content_recs_service,
embedding_service) as noise.

Two-tier Union-Find over MiniLM embeddings of every distinct theme string
(the same vendored model `embedding_service` uses — no new dependency):

- cosine >= `AUTO_MERGE` (0.95): merge unconditionally. On the real data
  (553 books, 2026-09-19) every pair up here is a genuine duplicate, mostly
  word-order swaps.
- `LEXICAL_MERGE` <= cosine < `AUTO_MERGE`: merge only when both strings
  reduce to the same set of content-word stems (see `content_stems`).

The middle tier's gate deliberately departs from prompts/39's libtrails
recipe, which gated it on the two themes co-occurring on a shared book.
That rule assumes paraphrases pile up on the same book — true at libtrails'
~117 topics/book, not here: the reduce step emits ~10 themes per book and
already dedups within a book, so real paraphrases essentially never share
one. Measured on the live data it rejected 321 band pairs and accepted 3,
and the rejections were the genuine duplicates ("refugee crisis"/"refugee
crises", "consequences of war"/"war and its consequences"), while one of
the 3 accepted was a wrong merge ("sacrifice" into "love and sacrifice" —
the model listed both on one book precisely *because* it saw them as
different). Ungated, the band is too loose ("father-daughter relationships"
~ "father-son relationships", bare "X" ~ narrower "X and Y"); the stem gate
keeps exactly the rephrasings.

Results land in each done book's `llm_tags_json.full.themesCanonical`
(order-preserving, deduped). Nothing downstream reads it yet — that's
Phase B. Re-run as a whole-library pass: after each book's reduce step
(llm_tagging_service.tick) and on demand via
`POST /api/library/themes/refresh`. Canonical labels are the member used by
the most books, so a label can shift as the library grows; recomputing
everything each time keeps every book consistent with the current vocab.
"""

import asyncio
import logging
import re
from collections import Counter, defaultdict
from collections.abc import Callable

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import Book
from app.services.embedding_service import embed_texts

logger = logging.getLogger(__name__)

AUTO_MERGE = 0.95
LEXICAL_MERGE = 0.85

_STOPWORDS = frozenset(
    "a an and or of the in on to for with vs versus its their his her one "
    "against between within through from by as at into amid".split()
)
_STEM_PREFIX = 7

# prompts/47 A.2 — bare abstract nouns that fit almost any novel, adapted
# from libtrails' ~30-word stoplist plus the bare words BookBrain's own
# output leans on hardest ("survival" alone was on 215 of 553 books). They
# become meaningless hubs the moment themes are compared across books.
# Only an exact bare match is dropped: "survival against supernatural
# threats" is specific and stays; so do specific single words ("revenge",
# "colonialism", "espionage") that genuinely tell books apart.
GENERIC_THEMES = frozenset(
    {
        "power", "love", "identity", "conflict", "survival", "freedom",
        "control", "trust", "fear", "growth", "time", "people", "world",
        "change", "future", "hope", "courage", "family", "friendship",
        "loyalty", "betrayal", "sacrifice", "redemption", "resilience",
        "isolation", "belonging", "transformation", "self-discovery",
        "morality", "justice", "truth", "faith", "memory", "legacy", "loss",
        "grief", "trauma", "humanity", "life", "death", "destiny", "fate",
        "relationships", "community", "strength", "perseverance",
        "determination", "adventure", "ambition", "responsibility",
        "personal growth", "coming of age",
    }
)

EmbedFn = Callable[[list[str]], np.ndarray]

# Theme strings are short and repeat across runs, and the whole-library pass
# runs after every reduce — cache vectors in-process so each run only embeds
# the handful of strings the newest book introduced.
_vector_cache: dict[str, np.ndarray] = {}


def _cached_embed(texts: list[str]) -> np.ndarray:
    missing = [t for t in texts if t not in _vector_cache]
    if missing:
        for text, vec in zip(missing, embed_texts(missing), strict=True):
            _vector_cache[text] = vec
    return np.stack([_vector_cache[t] for t in texts]) if texts else np.zeros((0, 0))


def normalize_theme(theme: str) -> str:
    return " ".join(theme.lower().split())


def is_generic_theme(theme: str) -> bool:
    return normalize_theme(theme).strip(" .!") in GENERIC_THEMES


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        word = word[:-3] + "y"
    elif len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    # A crude prefix cut is enough to line up "technology"/"technological",
    # "colonial"/"colonialism" — collisions ("communism"/"community") are
    # harmless because the cosine floor still has to be met too.
    return word[:_STEM_PREFIX]


def content_stems(theme: str) -> frozenset[str]:
    """The set of content-word stems, ignoring order, joiners ("and", "vs."),
    possessives, hyphenation and plurals — "war and its consequences" and
    "consequences of war" both give {"consequ", "war"}."""
    text = normalize_theme(theme).replace("'s", "")
    words = re.split(r"[^a-z0-9]+", text)
    return frozenset(_stem(w) for w in words if w and w not in _STOPWORDS)


def cluster_themes(book_themes: dict[int, list[str]], embed_fn: EmbedFn = _cached_embed) -> dict[str, str]:
    """Map every normalized theme in `book_themes` to its canonical label."""
    books_per_theme: Counter[str] = Counter()
    for themes in book_themes.values():
        books_per_theme.update({normalize_theme(t) for t in themes if t.strip() and not is_generic_theme(t)})
    themes = sorted(books_per_theme)
    if not themes:
        return {}

    vectors = np.asarray(embed_fn(themes), dtype=np.float32)
    sims = np.triu(vectors @ vectors.T, 1)
    stems = [content_stems(t) for t in themes]

    parent = list(range(len(themes)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, j in zip(*np.nonzero(sims >= LEXICAL_MERGE), strict=True):
        if sims[i, j] >= AUTO_MERGE or stems[i] == stems[j]:
            parent[find(i)] = find(j)

    members: dict[int, list[str]] = defaultdict(list)
    for i, theme in enumerate(themes):
        members[find(i)].append(theme)
    mapping: dict[str, str] = {}
    for group in members.values():
        label = min(group, key=lambda t: (-books_per_theme[t], len(t), t))
        for theme in group:
            mapping[theme] = label
    return mapping


def canonical_themes(themes: list[str], mapping: dict[str, str]) -> list[str]:
    out: list[str] = []
    for theme in themes:
        key = normalize_theme(theme)
        if not key or is_generic_theme(key):
            continue
        label = mapping.get(key, key)
        if label not in out:
            out.append(label)
    return out


async def refresh_theme_canon(session: AsyncSession, *, embed_fn: EmbedFn = _cached_embed) -> dict:
    """Recompute canonical themes across every book with a finished full-text
    pass and write any that changed. Embedding + clustering run in a worker
    thread so the event loop stays free."""
    books = (
        (await session.execute(select(Book).where(Book.llm_tags_json.is_not(None)))).scalars().all()
    )
    done: dict[int, tuple[Book, dict]] = {}
    for book in books:
        full = (book.llm_tags_json or {}).get("full")
        if isinstance(full, dict) and full.get("status") == "done":
            done[book.id] = (book, full)

    book_themes = {bid: list(full.get("themes") or []) for bid, (_, full) in done.items()}
    mapping = await asyncio.to_thread(cluster_themes, book_themes, embed_fn)

    updated = 0
    for bid, (book, full) in done.items():
        canon = canonical_themes(book_themes[bid], mapping)
        if full.get("themesCanonical") == canon:
            continue
        book.llm_tags_json = {**book.llm_tags_json, "full": {**full, "themesCanonical": canon}}
        updated += 1
    await session.commit()

    result = {
        "books": len(done),
        "themes": len(mapping),
        "canonical": len(set(mapping.values())),
        "booksUpdated": updated,
    }
    logger.info("theme dedup: %s", result)
    return result
