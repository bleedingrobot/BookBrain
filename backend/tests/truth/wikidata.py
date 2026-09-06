"""Wikidata as an independent ground-truth voice.

Structured, community-curated, and not touched by the identification pipeline
(which uses Google Books + Open Library). Gives title (label), author (P50),
series (P179) and the series ordinal (P1545 qualifier on P179).
"""

from __future__ import annotations

import asyncio

import httpx

from app.services.text_match import normalize_words
from tests.truth.types import TruthClaim

_UA = "BookBrain-identification-eval/0.1 (https://github.com/bleedingrobot; giantjamez@gmail.com)"
_API = "https://www.wikidata.org/w/api.php"
_ENTITY = "https://www.wikidata.org/wiki/Special:EntityData/{}.json"

# instance-of values that mean "this really is a book/novel/short story", not a
# film / album / disambiguation page that shares the title.
_WORK_TYPES = {
    "Q571",       # book
    "Q7725634",   # literary work
    "Q47461344",  # written work
    "Q8261",      # novel
    "Q49084",     # short story
    "Q1279564",   # short story collection
    "Q25379",     # play — rare but harmless
    "Q1004",      # comic — for the graphic-novel entries
    "Q1760610",   # comic book
    "Q725377",    # graphic novel
    "Q149537",    # novella
    "Q112983",    # omnibus edition
}


async def lookup(
    client: httpx.AsyncClient, *, title: str | None, author: str | None, isbn: str | None
) -> TruthClaim | None:
    if not title:
        return None
    qid = await _best_entity(client, title, author)
    if qid is None:
        return None
    claim = await _claim_from_entity(client, qid)
    if claim is None or not claim.title:
        return None
    # sanity 1: the entity must share words with the title we searched for
    # (guards against "Dragonsong" -> "user interface" on junk EPUB metadata).
    if not (normalize_words(claim.title) & normalize_words(title)):
        return None
    # sanity 2: if we had a real author to go on and Wikidata's author shares
    # no surname with it, we probably landed on a different book with the same
    # title ("Playing with Fire" -> Conan Doyle, "City of Bones" -> Clare).
    if author and claim.author:
        want = {w for w in normalize_words(author) if len(w) > 1}
        got = {w for w in normalize_words(claim.author) if len(w) > 1}
        if want and got and not (want & got):
            return None
    return claim


async def _get_json(client: httpx.AsyncClient, url: str, params: dict | None = None) -> dict | None:
    for attempt in range(3):
        try:
            r = await client.get(url, params=params, headers={"User-Agent": _UA}, timeout=25)
            if r.status_code == 429:
                await asyncio.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError):
            if attempt == 2:
                return None
            await asyncio.sleep(1.5 * (attempt + 1))
    return None


async def _best_entity(client: httpx.AsyncClient, title: str, author: str | None) -> str | None:
    data = await _get_json(
        client,
        _API,
        {
            "action": "wbsearchentities",
            "search": title[:200],
            "language": "en",
            "type": "item",
            "format": "json",
            "limit": 8,
        },
    )
    if not data:
        return None
    hits = data.get("search", [])
    if not hits:
        return None

    author_last = (author or "").split()[-1].lower() if author else ""
    for hit in hits:
        desc = (hit.get("description") or "").lower()
        # cheap disambiguation: description usually reads "2013 novel by Scott Lynch"
        if author_last and author_last in desc:
            return hit["id"]
        if any(w in desc for w in ("novel", "book", "short story", "novella", "comic")):
            return hit["id"]
    return hits[0]["id"]  # fall back to the top hit


async def _claim_from_entity(client: httpx.AsyncClient, qid: str) -> TruthClaim | None:
    data = await _get_json(client, _ENTITY.format(qid))
    if not data:
        return None
    ent = data.get("entities", {}).get(qid)
    if not ent:
        return None
    claims = ent.get("claims", {})

    def item_ids(pid: str) -> list[str]:
        out = []
        for c in claims.get(pid, []):
            try:
                out.append(c["mainsnak"]["datavalue"]["value"]["id"])
            except (KeyError, TypeError):
                pass
        return out

    instance_of = set(item_ids("P31"))
    if not (instance_of & _WORK_TYPES):
        return None  # not confirmably a book/story — a film/album/concept sharing the title

    title = (ent.get("labels", {}).get("en") or {}).get("value")

    series_qids = item_ids("P179")
    series_number: float | None = None
    for c in claims.get("P179", []):
        q = c.get("qualifiers", {}).get("P1545")
        if q:
            try:
                series_number = float(str(q[0]["datavalue"]["value"]).lstrip("0") or "0")
            except (KeyError, TypeError, ValueError):
                pass

    label_ids = item_ids("P50") + series_qids
    labels = await _labels(client, label_ids) if label_ids else {}
    authors = [labels[q] for q in item_ids("P50") if q in labels]
    series = next((labels[q] for q in series_qids if q in labels), None)

    return TruthClaim(
        source="wikidata",
        title=title,
        author=authors[0] if authors else None,
        series=series,
        series_number=series_number,
        url=f"https://www.wikidata.org/wiki/{qid}",
    )


async def _labels(client: httpx.AsyncClient, ids: list[str]) -> dict[str, str]:
    data = await _get_json(
        client,
        _API,
        {
            "action": "wbgetentities",
            "ids": "|".join(sorted(set(ids))[:50]),
            "props": "labels",
            "languages": "en",
            "format": "json",
        },
    )
    if not data:
        return {}
    return {
        qid: (e.get("labels", {}).get("en") or {}).get("value")
        for qid, e in data.get("entities", {}).items()
        if (e.get("labels", {}).get("en") or {}).get("value")
    }
