from dataclasses import dataclass, field


@dataclass
class MetadataCandidate:
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    series: str | None = None
    series_number: float | None = None
    description: str | None = None
    language: str | None = None
    first_published: str | None = None
    isbn13: str | None = None
    isbn10: str | None = None
    source: str = ""
