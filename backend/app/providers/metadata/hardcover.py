"""Hardcover (https://hardcover.app) as a third metadata provider.

Hardcover's catalogue is curated by human librarians, so its `book_series`
data — real positions, canonical de-duplication — is markedly better than
what Google Books or Open Library expose. That directly targets BookBrain's
weakest identification field (series).

Constraints baked in here (from their API docs):
- Backend only. The token is a static secret (`HARDCOVER_API_TOKEN`), never
  shipped to the viewer.
- Free tier: 5,000 req/day, 60/min, burst 10 — a small instance-level token
  bucket keeps us well under, and a 429 backs off once then gives up.
- Beta and explicitly unstable ("anything you build could break"). Every
  failure path here returns `[]` so identification falls back to the other
  providers + AI, exactly like the wishlist's Open Library fallback.
- Only catalogue data is read (books / editions / series / authors) — never
  user data (reviews, ratings, lists), which their licence forbids a
  public-facing product from using.
"""

import asyncio
import logging
import time

import httpx

from app.providers.metadata.base import BookMetadataProvider
from app.providers.metadata.types import MetadataCandidate, split_series_and_number
from app.services.text_match import normalize_person_name

logger = logging.getLogger(__name__)

ENDPOINT = "https://api.hardcover.app/v1/graphql"
_USER_AGENT = "BookBrain (+https://github.com/bleedingrobot/BookBrain)"


class HardcoverRateLimited(Exception):
    """The **daily** quota is exhausted (resets ~midnight UTC). A bulk refresh
    should stop the whole run the moment it sees this — otherwise every
    remaining call 429s, each burning a `Retry-After` sleep, and (worse) a
    provider that can't tell "call failed" from "no result" downgrades good
    data. Distinct from a burst 429, which `hardcover_graphql` retries."""


class HardcoverUnavailable(Exception):
    """A single call failed for a transient reason (network, HTTP 5xx, a
    GraphQL error, a burst 429 that outlived its one retry). The caller
    should leave whatever it already has untouched and try again next run —
    NOT record a negative result."""

# `editions -> book -> book_series -> series` is depth 4. Works today; their
# docs list a max query depth of 3 as a *roadmap* item. If it lands this query
# starts returning an `errors` array and the provider degrades to []. The fix
# would be to split into two calls (edition -> book_id, then book by id).
_ISBN_QUERY = """
query BookBrainEditionByIsbn($isbn: String!) {
  editions(
    where: {_or: [{isbn_13: {_eq: $isbn}}, {isbn_10: {_eq: $isbn}}]}
    order_by: {users_count: desc}
    limit: 3
  ) {
    isbn_13
    isbn_10
    book {
      id
      title
      subtitle
      description
      release_year
      contributions { author { name } }
      book_series(order_by: {featured: desc}, limit: 1) {
        position
        series { name }
      }
    }
  }
}
"""

_SEARCH_QUERY = """
query BookBrainBookSearch($q: String!) {
  search(query: $q, query_type: "Book", per_page: 5) {
    results
  }
}
"""

# prompts/28 — the same-named author row(s) plus the two hops that resolve one
# person: `canonical` (Hardcover's own duplicate-row pointer) and `alias` (pen
# name → real identity). Ordered by book count so row 0 is the real one.
_AUTHOR_IDENTITY = """
query BookBrainAuthorIdentity($name: String!) {
  authors(where: {name: {_eq: $name}}, order_by: {books_count: desc_nulls_last}, limit: 5) {
    id
    name
    alternate_names
    alias { id name }
    canonical { id name alias { id name } }
  }
}
"""


def resolve_person_id(rows: object, queried_name: str) -> tuple[int, str] | None:
    """Walk Hardcover's author graph for `queried_name` to a stable "person
    id": the best-matching row (most books), then its `canonical` row
    (Hardcover's own dedup), then that row's `alias` (pen name → real
    identity).

    Guarded — returns None unless the matched row's `name` or one of its
    `alternate_names` normalises to `queried_name`, so a fuzzy Hardcover hit
    for a different person is never trusted.

    Verified live 2026-09-09: `Iain M. Banks` / `Iain Banks` → 95997;
    `Robert Galbraith` → 80626 (J.K. Rowling); `Richard A. Knaak` → 191045;
    a plain house pseudonym (`Richard Awlinson`) → its own id, no hops."""
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return None
    best = rows[0]  # ordered by books_count desc
    key = normalize_person_name(queried_name)
    if not key:
        return None
    names = [best.get("name"), *(best.get("alternate_names") or [])]
    if key not in {normalize_person_name(n) for n in names if isinstance(n, str)}:
        return None
    node = best.get("canonical") if isinstance(best.get("canonical"), dict) else best
    alias = node.get("alias") if isinstance(node.get("alias"), list) else []
    person = alias[0] if alias and isinstance(alias[0], dict) else node
    pid = person.get("id")
    if not isinstance(pid, int):
        return None
    name = person.get("name")
    is_str = isinstance(name, str) and name.strip()
    return pid, name if is_str else str(best.get("name") or queried_name)


class _TokenBucket:
    """Instance-level, not a module singleton — the real scan service holds
    one provider for the process, which is where limiting matters, and this
    sidesteps the pytest event-loop rebind that module-level asyncio.Lock
    singletons need conftest resets for. The lock is created lazily so it
    binds to the loop that first uses it."""

    def __init__(self, rate_per_sec: float, burst: int) -> None:
        self._rate = rate_per_sec
        self._capacity = float(burst)
        self._tokens = float(burst)
        self._updated = time.monotonic()
        self._lock: asyncio.Lock | None = None

    async def take(self) -> None:
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            now = time.monotonic()
            self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._rate)
            self._updated = now
            if self._tokens < 1.0:
                await asyncio.sleep((1.0 - self._tokens) / self._rate)
                self._tokens = 0.0
                self._updated = time.monotonic()
            else:
                self._tokens -= 1.0


def _is_daily_limit(response: httpx.Response) -> bool:
    """A 429 for the *daily* quota (not the per-minute burst). Header forms
    seen live: `x-ratelimit-daily-remaining: 0` and
    `ratelimit: "daily";r=0;t=65817`."""
    if response.headers.get("x-ratelimit-daily-remaining") == "0":
        return True
    rl = response.headers.get("ratelimit", "").replace(" ", "")
    return '"daily"' in rl and "r=0" in rl


async def hardcover_graphql(
    client: httpx.AsyncClient,
    token: str,
    query: str,
    variables: dict,
    bucket: _TokenBucket,
) -> dict | None:
    """POST one GraphQL request; return the `data` object, or None on a
    transient failure (network, HTTP != 200, GraphQL `errors`, unparseable
    body, a burst 429 that outlived its one retry). Raises `HardcoverRateLimited`
    when the *daily* quota is gone — a bulk caller must stop, not grind on.
    Shared by HardcoverProvider and the refresh services."""
    if not token:
        return None
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": _USER_AGENT,
    }
    payload = {"query": query, "variables": variables}
    for attempt in range(2):
        await bucket.take()
        try:
            response = await client.post(ENDPOINT, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            logger.info("hardcover request failed: %s", exc)
            return None
        if response.status_code == 429:
            if _is_daily_limit(response):
                logger.warning("hardcover daily rate limit exhausted — aborting run")
                raise HardcoverRateLimited
            if attempt == 0:
                retry_after = _retry_after_seconds(response)
                logger.info("hardcover burst-limited, retrying in %ss", retry_after)
                await asyncio.sleep(retry_after)
                continue
            return None
        if response.status_code != 200:
            logger.info("hardcover HTTP %s", response.status_code)
            return None
        try:
            body = response.json()
        except ValueError:
            return None
        if body.get("errors"):
            logger.info("hardcover GraphQL errors: %s", body["errors"])
            return None
        return body.get("data")
    return None


class HardcoverProvider(BookMetadataProvider):
    name = "hardcover"

    def __init__(self, token: str, client: httpx.AsyncClient | None = None) -> None:
        self._token = (token or "").strip()
        self._client = client or httpx.AsyncClient(timeout=12.0)
        # 60/min with burst 10 upstream — aim for ~54/min, burst 8.
        self._bucket = _TokenBucket(rate_per_sec=0.9, burst=8)
        self._cache: dict[str, list[MetadataCandidate]] = {}
        # prompts/28 Phase 2 — name (lowercased) → resolved person id, or None
        # when Hardcover has no trustworthy row. One entry per distinct author
        # in a scan, so the whole batch costs at most one call per author.
        self._person_cache: dict[str, int | None] = {}

    async def resolve_person_id(self, name: str | None) -> int | None:
        """The canonical Hardcover "person id" for an author name — the id
        after following `canonical` then `alias` (see the module-level
        `resolve_person_id`). Lets scan-time author resolution reuse an
        existing row for a pen name or initial-variant. Any failure (no
        token, network, rate limit, no trustworthy row) → None, and the
        caller falls back to today's name-only match."""
        if not self._token or not name or not name.strip():
            return None
        key = name.strip().lower()
        if key in self._person_cache:
            return self._person_cache[key]
        data = await self._post(_AUTHOR_IDENTITY, {"name": name.strip()})
        resolved = resolve_person_id((data or {}).get("authors"), name)
        pid = resolved[0] if resolved else None
        self._person_cache[key] = pid
        return pid

    async def search_by_isbn(self, isbn: str) -> list[MetadataCandidate]:
        if not self._token or not isbn:
            return []
        key = f"isbn:{isbn}"
        if key in self._cache:
            return self._cache[key]
        data = await self._post(_ISBN_QUERY, {"isbn": isbn})
        editions = ((data or {}).get("editions")) or []
        seen_books: set[int] = set()
        out: list[MetadataCandidate] = []
        for edition in editions:
            book = edition.get("book") or {}
            book_id = book.get("id")
            if book_id in seen_books:
                continue
            seen_books.add(book_id)
            out.append(self._book_to_candidate(book, edition))
        self._cache[key] = out
        return out

    async def search_by_title_author(
        self, title: str, author: str | None
    ) -> list[MetadataCandidate]:
        if not self._token or not title:
            return []
        query = f"{title} {author}".strip() if author else title.strip()
        key = f"q:{query.lower()}"
        if key in self._cache:
            return self._cache[key]
        data = await self._post(_SEARCH_QUERY, {"q": query})
        results = ((data or {}).get("search") or {}).get("results") or {}
        hits = results.get("hits") if isinstance(results, dict) else None
        out: list[MetadataCandidate] = []
        for hit in (hits or [])[:5]:
            document = hit.get("document") if isinstance(hit, dict) else None
            if isinstance(document, dict):
                out.append(self._document_to_candidate(document))
        self._cache[key] = out
        return out

    async def _post(self, query: str, variables: dict) -> dict | None:
        try:
            return await hardcover_graphql(
                self._client, self._token, query, variables, self._bucket
            )
        except HardcoverRateLimited:
            # A live scan must degrade to the other providers, never fail.
            logger.info("hardcover daily limit — provider standing down for this scan")
            return None

    # -- mapping ----------------------------------------------------------

    def _book_to_candidate(self, book: dict, edition: dict) -> MetadataCandidate:
        series, series_number = _first_series(book.get("book_series"))
        year = book.get("release_year")
        return MetadataCandidate(
            title=book.get("title"),
            authors=_author_names(book.get("contributions")),
            series=series,
            series_number=series_number,
            description=book.get("description") or None,
            first_published=str(year) if year else None,
            isbn13=edition.get("isbn_13") or None,
            isbn10=edition.get("isbn_10") or None,
            source=self.name,
        )

    def _document_to_candidate(self, doc: dict) -> MetadataCandidate:
        series, series_number = _document_series(doc)
        year = doc.get("release_year")
        author_names = doc.get("author_names") or []
        return MetadataCandidate(
            title=doc.get("title"),
            authors=[a for a in author_names if isinstance(a, str)],
            series=series,
            series_number=series_number,
            description=doc.get("description") or None,
            first_published=str(year) if year else None,
            # A search hit's `isbns` is an unordered pile of *every* edition's
            # ISBN (dozens, many foreign) — picking one would be misleading.
            # The ISBN path is where a specific edition's ISBN comes from.
            isbn13=None,
            isbn10=None,
            source=self.name,
        )


def _retry_after_seconds(response: httpx.Response) -> float:
    raw = response.headers.get("Retry-After", "1")
    try:
        return max(0.0, min(30.0, float(raw)))
    except (TypeError, ValueError):
        return 1.0


def _author_names(contributions: object) -> list[str]:
    if not isinstance(contributions, list):
        return []
    names: list[str] = []
    for entry in contributions:
        author = entry.get("author") if isinstance(entry, dict) else None
        name = author.get("name") if isinstance(author, dict) else None
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
    return names


def _first_series(book_series: object) -> tuple[str | None, float | None]:
    if not isinstance(book_series, list) or not book_series:
        return None, None
    entry = book_series[0]
    if not isinstance(entry, dict):
        return None, None
    series = entry.get("series") if isinstance(entry.get("series"), dict) else {}
    name = series.get("name")
    if not isinstance(name, str) or not name.strip():
        return None, None
    position = entry.get("position")
    try:
        number = float(position) if position is not None else None
    except (TypeError, ValueError):
        number = None
    # The name is usually clean, but run it through the shared splitter anyway
    # so a stray "Mistborn #1" lands in the right two fields.
    parsed_name, parsed_number = split_series_and_number(name, number)
    return parsed_name, parsed_number


def _document_series(doc: dict) -> tuple[str | None, float | None]:
    # A search-hit document carries the series as a nested object:
    #   featured_series: { position, series: { name, ... } }
    # (may be {} for a book with no series). `series_names` is a flat string
    # array fallback — first entry, which Typesense weights as most relevant.
    featured = doc.get("featured_series")
    number = doc.get("featured_series_position")
    if number is None and isinstance(featured, dict):
        number = featured.get("position")
    try:
        number = float(number) if number is not None else None
    except (TypeError, ValueError):
        number = None
    name: str | None = None
    if isinstance(featured, dict):
        inner = featured.get("series")
        if isinstance(inner, dict):
            name = inner.get("name")
    if not name:
        series_names = doc.get("series_names") or []
        name = next((s for s in series_names if isinstance(s, str) and s.strip()), None)
    if not name:
        return None, None
    return split_series_and_number(name, number)
