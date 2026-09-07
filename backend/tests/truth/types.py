from __future__ import annotations

from dataclasses import dataclass, field

# Field-name -> how confident we are in the triangulated answer for it.
#   "consensus"  — >=2 independent signals agree, at least one not a Claude call
#   "weak"       — >=2 signals agree but all of them are Claude web-search calls
#   "unresolved" — signals disagree or too few had an opinion; do not score
Provenance = dict[str, str]


@dataclass
class TruthClaim:
    """One source's opinion about a book. Any field may be None ("no opinion")."""

    source: str  # "wikidata" | "web_claude_identify" | "web_claude_verify" | "epub" | "provider"
    title: str | None = None
    author: str | None = None
    series: str | None = None
    series_number: float | None = None
    url: str | None = None
    note: str | None = None

    def get(self, fieldname: str):
        return getattr(self, fieldname)


@dataclass
class TriangulatedAnswer:
    title: str | None = None
    author: str | None = None
    series: str | None = None
    series_number: float | None = None
    provenance: Provenance = field(default_factory=dict)
    claims: list[dict] = field(default_factory=list)  # raw, for the fixture audit trail
    disagreements: list[str] = field(default_factory=list)

    def to_answer_block(self) -> dict:
        return {
            "title": self.title,
            "author": self.author,
            "series": self.series,
            "series_number": self.series_number,
            "verified": True,  # triangulated, not hand-verified
            "source": "triangulated",
            "provenance": self.provenance,
        }
