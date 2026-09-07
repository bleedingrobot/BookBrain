"""Turn a pile of independent TruthClaims into a per-field ground-truth answer.

A field is accepted only when >=2 independent signals agree on it (after the
same normalisation the harness scores with). "Agree" is exact-after-normalise
for title/author/number, and word-set overlap for series names (sources phrase
series differently — "Gentleman Bastard" vs "Gentleman Bastard Sequence").

Signals: wikidata, web_claude_identify, web_claude_verify, plus the EPUB's own
embedded metadata and any pipeline provider candidate (raw inputs, not
identification output).

Provenance per field:
  "consensus"  — >=2 agree and at least one is not a Claude web-search call
  "weak"       — >=2 agree but all of them are Claude calls
  "unresolved" — not enough agreement; the harness does not score this field
"""

from __future__ import annotations

from app.services.text_match import normalize, normalize_title_strict, normalize_words
from tests.corpus_harness import series_key, series_matches
from tests.truth.types import TriangulatedAnswer, TruthClaim

_CLAUDE = {"web_claude_identify", "web_claude_verify"}
# Sources that are genuinely independent of the identification pipeline. The
# EPUB's own metadata and a provider candidate are raw *inputs* — two of them
# agreeing (and provider data is often copied from the EPUB) is not independent
# confirmation, so a field backed only by {epub, provider} stays unresolved.
_INDEPENDENT = {"wikidata", *_CLAUDE}
def _norm(fieldname: str, value) -> object | None:
    if value is None or value == "":
        return None
    if fieldname == "title":
        return normalize_title_strict(str(value))
    if fieldname == "author":
        first = str(value).replace(";", ",").replace("&", ",").split(",")[0]
        return normalize(first)
    if fieldname == "series":
        return series_key(str(value)) or None
    if fieldname == "series_number":
        try:
            return f"{float(value):g}"
        except (TypeError, ValueError):
            return None
    raise ValueError(fieldname)


def _agree(fieldname: str, a, b) -> bool:
    if a is None or b is None:
        return False
    if fieldname != "series":
        return a == b
    # a and b are already series_key() frozensets; guard against a giant junk
    # string ("Forgotten Realms.Avatar Series...2 of 3._.ICB Best") swallowing a
    # real short name on a 1-word overlap.
    small, big = sorted((a, b), key=len)
    return bool(small) and small <= big and len(big) <= len(small) + 2


def _pick(fieldname: str, claims: list[TruthClaim]):
    """(raw_value, [agreeing_sources]) for the value >=2 sources back, else None."""
    ops = [(c.source, c.get(fieldname), _norm(fieldname, c.get(fieldname))) for c in claims]
    ops = [o for o in ops if o[2] is not None]
    if len(ops) < 2:
        return None
    groups: list[list[tuple]] = []
    for op in ops:
        for g in groups:
            if _agree(fieldname, g[0][2], op[2]):
                g.append(op)
                break
        else:
            groups.append([op])
    groups.sort(key=len, reverse=True)
    top = groups[0]
    if len(top) < 2:
        return None
    # store the cleanest representative string: a curated source over a raw one.
    order = {"wikidata": 0, "web_claude_verify": 1, "web_claude_identify": 2, "provider": 3, "epub": 4}
    top.sort(key=lambda o: order.get(o[0], 9))
    return top[0][1], [o[0] for o in top]


def _prov(sources: list[str]) -> str:
    if not (set(sources) & _INDEPENDENT):
        return "unresolved"  # only raw inputs (epub/provider) agreed — not confirmation
    return "consensus" if any(s not in _CLAUDE for s in sources) else "weak"


def triangulate(claims: list[TruthClaim]) -> TriangulatedAnswer:
    ans = TriangulatedAnswer(claims=[_claim_dict(c) for c in claims])

    for fieldname in ("title", "author"):
        picked = _pick(fieldname, claims)
        prov = _prov(picked[1]) if picked else "unresolved"
        if picked and prov != "unresolved":
            raw, srcs = picked
            setattr(ans, fieldname, raw)
            ans.provenance[fieldname] = prov
            _record_dissent(ans, fieldname, claims, srcs, raw)
        else:
            ans.provenance[fieldname] = "unresolved"
            _record_disagreement(ans, fieldname, claims)

    _resolve_series(ans, claims)
    return ans


def _resolve_series(ans: TriangulatedAnswer, claims: list[TruthClaim]) -> None:
    named = _pick("series", claims)
    # sources that actually examined the book and call it a standalone
    standalone_votes = {
        c.source
        for c in claims
        if c.source in ({"wikidata"} | _CLAUDE) and c.get("title") and c.get("series") in (None, "")
    }

    if named and _prov(named[1]) != "unresolved":
        raw, srcs = named
        ans.series = raw
        ans.provenance["series"] = _prov(srcs)
        _record_dissent(ans, "series", claims, srcs, raw)
        num = _pick("series_number", [c for c in claims if _norm("series", c.get("series")) is not None])
        if num and _prov(num[1]) != "unresolved":
            nraw, nsrcs = num
            try:
                ans.series_number = float(nraw)
                ans.provenance["series_number"] = _prov(nsrcs)
            except (TypeError, ValueError):
                ans.provenance["series_number"] = "unresolved"
        else:
            ans.provenance["series_number"] = "unresolved"
        return

    if len(standalone_votes & _CLAUDE) >= 2:
        prov = "consensus" if "wikidata" in standalone_votes else "weak"
        ans.provenance["series"] = prov
        ans.provenance["series_number"] = prov  # both None, confirmed standalone
        return

    ans.provenance["series"] = "unresolved"
    ans.provenance["series_number"] = "unresolved"
    _record_disagreement(ans, "series", claims)


def _record_disagreement(ans: TriangulatedAnswer, fieldname: str, claims: list[TruthClaim]) -> None:
    seen = [(c.source, c.get(fieldname)) for c in claims if c.get(fieldname) not in (None, "")]
    if len(seen) >= 2:
        ans.disagreements.append(
            f"{fieldname}: " + "; ".join(f"{s}={v!r}" for s, v in seen)
        )


def _record_dissent(ans: TriangulatedAnswer, fieldname: str, claims: list[TruthClaim],
                    agreeing: list[str], chosen) -> None:
    """A source that had an opinion on this field but was outvoted — worth
    seeing in the audit trail even though the field still counts."""
    for c in claims:
        v = c.get(fieldname)
        if c.source in agreeing or v in (None, "") or c.source in ("epub", "provider"):
            continue
        if not _agree(fieldname, _norm(fieldname, v), _norm(fieldname, chosen)):
            ans.disagreements.append(f"{fieldname}: chose {chosen!r}, {c.source} said {v!r}")


def _claim_dict(c: TruthClaim) -> dict:
    return {
        "source": c.source, "title": c.title, "author": c.author,
        "series": c.series, "series_number": c.series_number, "url": c.url, "note": c.note,
    }


# --- turning raw inputs into claims -------------------------------------------


def epub_claim(evidence: dict) -> TruthClaim:
    return TruthClaim(
        source="epub",
        title=evidence.get("title"),
        author=(evidence.get("authors") or [None])[0],
        series=evidence.get("series"),
        series_number=evidence.get("series_number"),
    )


def provider_claim(candidates: list[dict]) -> TruthClaim | None:
    """One synthetic 'provider' vote, only when the providers agree with each
    other on the title (a split vote shouldn't count as corroboration)."""
    titled = [c for c in candidates if c.get("title")]
    if not titled:
        return None
    if len({normalize_title_strict(c["title"]) for c in titled}) != 1:
        return None
    c = titled[0]
    return TruthClaim(
        source="provider",
        title=c.get("title"),
        author=(c.get("authors") or [None])[0],
        series=c.get("series"),
        series_number=c.get("series_number"),
    )
