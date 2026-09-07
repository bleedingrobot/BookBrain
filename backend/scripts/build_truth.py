"""Triangulate an independent ground-truth answer for each corpus book.

prompts/15 Stage 0, rebuilt to need no human verification: instead of James
eyeballing every answer key, the answer is whatever >=2 independent sources
agree on — Wikidata, a web-search-grounded Claude "identify" call, a
web-search-grounded Claude "refute this" call, plus the EPUB's own metadata and
the pipeline's provider candidates as corroborating (not deciding) votes.

Fields where the sources don't reach agreement are marked "unresolved" and the
harness simply doesn't score them — an honest gap beats a wrong answer key.

    python scripts/build_truth.py --only adrian-tchaikovsky-children-of-memory
    python scripts/build_truth.py --limit 10            # first 10 uncached, dry run
    python scripts/build_truth.py --all --write         # do everything, update fixtures
    python scripts/build_truth.py --all --write --refresh   # ignore the cache

Every source call is cached under tests/identification_corpus/_truth_cache/ so
runs are resumable and cheap to repeat. Costs Anthropic credits (2 grounded
calls per book) + free Wikidata hits.
"""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import httpx  # noqa: E402

from tests.truth import consensus, web_claude, wikidata  # noqa: E402
from tests.truth.types import TruthClaim  # noqa: E402

CORPUS_DIR = _BACKEND / "tests" / "identification_corpus"
CACHE_DIR = CORPUS_DIR / "_truth_cache"


def _fixtures() -> list[Path]:
    return [p for p in sorted(CORPUS_DIR.glob("*.json")) if not p.name.startswith("_")]


def _cache_path(entry_id: str, source: str) -> Path:
    return CACHE_DIR / f"{entry_id}__{source}.json"


def _load_cached(entry_id: str, source: str) -> TruthClaim | None | str:
    p = _cache_path(entry_id, source)
    if not p.is_file():
        return "MISS"
    raw = json.loads(p.read_text(encoding="utf-8"))
    if raw is None:
        return None
    return TruthClaim(**raw)


def _save_cached(entry_id: str, source: str, claim: TruthClaim | None) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = None if claim is None else claim.__dict__
    _cache_path(entry_id, source).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


_PLACEHOLDER_TITLES = {"input", "unknown", "untitled", "calibre", "epub", "none", "book", ""}


def _best_seed(fixture: dict) -> tuple[str | None, str | None]:
    """Title/author to seed the Wikidata search with — the current best guess
    (fixture answer), falling back to non-placeholder EPUB metadata."""
    ev, ans = fixture["evidence"], fixture.get("answer", {})
    title = ans.get("title") or ev.get("title")
    if title and title.strip().lower() in _PLACEHOLDER_TITLES:
        title = ev.get("title") if (ev.get("title") or "").strip().lower() not in _PLACEHOLDER_TITLES else None
    author = ans.get("author") or (ev.get("authors") or [None])[0]
    return title, author


async def _wikidata_claim(entry_id: str, fixture: dict, refresh: bool) -> TruthClaim | None:
    if not refresh:
        c = _load_cached(entry_id, "wikidata")
        if c != "MISS":
            return c
    title, author = _best_seed(fixture)
    async with httpx.AsyncClient() as client:
        claim = await wikidata.lookup(
            client,
            title=title,
            author=author,
            isbn=fixture["evidence"].get("isbn13") or fixture["evidence"].get("isbn10"),
        )
    _save_cached(entry_id, "wikidata", claim)
    return claim


_AI_DISABLED = {"v": False}  # flipped when the API reports no credit


def _claude_claim(entry_id: str, kind: str, fixture: dict, refresh: bool) -> TruthClaim | None:
    source = f"web_claude_{kind}"
    if not refresh:
        c = _load_cached(entry_id, source)
        if c != "MISS":
            return c
    if _AI_DISABLED["v"]:
        return None
    ev = fixture["evidence"]
    seed_title, seed_author = _best_seed(fixture)
    try:
        if kind == "identify":
            claim = web_claude.identify(
                filename=fixture["filename"],
                title=ev.get("title") or seed_title,
                author=(ev.get("authors") or [None])[0] or seed_author,
                isbn=ev.get("isbn13") or ev.get("isbn10"),
                snippet=ev.get("text_snippet"),
            )
        else:
            claim = web_claude.verify(
                proposed=fixture.get("answer", {}),
                filename=fixture["filename"],
                title=ev.get("title"),
                author=(ev.get("authors") or [None])[0],
                isbn=ev.get("isbn13") or ev.get("isbn10"),
            )
    except web_claude.OutOfCredit:
        _AI_DISABLED["v"] = True
        print("  !! Anthropic credit exhausted — finishing with Wikidata + EPUB + provider only.")
        return None
    _save_cached(entry_id, source, claim)  # only cache real results, not credit failures
    return claim


def _assemble(path: Path, wd: TruthClaim | None, ci: TruthClaim | None,
              cv: TruthClaim | None, *, write: bool) -> dict:
    fixture = json.loads(path.read_text(encoding="utf-8"))
    ev = fixture["evidence"]

    claims: list[TruthClaim] = [consensus.epub_claim(ev)]
    prov = consensus.provider_claim(fixture.get("candidates", []))
    if prov:
        claims.append(prov)
    claims += [c for c in (wd, ci, cv) if c is not None]

    answer = consensus.triangulate(claims)
    block = answer.to_answer_block()

    if write:
        fixture["answer"] = block
        fixture["truth_claims"] = answer.claims
        fixture["truth_disagreements"] = answer.disagreements
        path.write_text(json.dumps(fixture, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return {
        "id": fixture["id"],
        "provenance": answer.provenance,
        "answer": {k: block[k] for k in ("title", "author", "series", "series_number")},
        "disagreements": answer.disagreements,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", action="append", default=[], help="fixture id(s) to (re)build")
    ap.add_argument("--limit", type=int, help="process at most N fixtures")
    ap.add_argument("--all", action="store_true", help="process every fixture")
    ap.add_argument("--write", action="store_true", help="update the fixture answer blocks (default: dry run)")
    ap.add_argument("--refresh", action="store_true", help="ignore the source cache")
    ap.add_argument("--workers", type=int, default=6, help="parallel grounded-Claude calls")
    args = ap.parse_args()

    fixtures = _fixtures()
    if args.only:
        wanted = set(args.only)
        fixtures = [p for p in fixtures if p.stem in wanted]
    elif not args.all:
        # default: fixtures with no cached grounded call yet
        fixtures = [p for p in fixtures if not _cache_path(p.stem, "web_claude_identify").is_file()]
    if args.limit:
        fixtures = fixtures[: args.limit]

    if not fixtures:
        print("nothing to do (everything cached — use --all or --refresh)")
        return 0

    print(f"{len(fixtures)} fixture(s); write={args.write} refresh={args.refresh} workers={args.workers}")

    # Wikidata first (free, fast, async).
    async def _all_wd():
        out = {}
        for p in fixtures:
            fx = json.loads(p.read_text(encoding="utf-8"))
            out[p.stem] = await _wikidata_claim(p.stem, fx, args.refresh)
        return out

    wd_claims = asyncio.run(_all_wd())

    # Then fan every grounded Claude call across the pool at once.
    cov = {f: {"consensus": 0, "weak": 0, "unresolved": 0} for f in ("title", "author", "series", "series_number")}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs: dict[tuple[str, str], concurrent.futures.Future] = {}
        for p in fixtures:
            fx = json.loads(p.read_text(encoding="utf-8"))
            for kind in ("identify", "verify"):
                futs[(p.stem, kind)] = pool.submit(_claude_claim, p.stem, kind, fx, args.refresh)

        for i, p in enumerate(fixtures, 1):
            ci = futs[(p.stem, "identify")].result()
            cv = futs[(p.stem, "verify")].result()
            r = _assemble(p, wd_claims.get(p.stem), ci, cv, write=args.write)
            for f, prov in r["provenance"].items():
                cov[f][prov] = cov[f].get(prov, 0) + 1
            flag = "  DISAGREE: " + "; ".join(r["disagreements"]) if r["disagreements"] else ""
            print(f"  [{i}/{len(fixtures)}] {r['id']:50} "
                  f"{','.join(k[0] + ':' + r['provenance'][k] for k in ('title', 'author', 'series', 'series_number'))}{flag}")

    print("\ncoverage:")
    for f, c in cov.items():
        print(f"  {f:<15} consensus={c['consensus']:3}  weak={c['weak']:3}  unresolved={c['unresolved']:3}")
    if not args.write:
        print("\n(dry run — pass --write to update fixture answer blocks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
