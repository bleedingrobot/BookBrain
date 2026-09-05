from dataclasses import dataclass


@dataclass
class AIIdentificationResult:
    title: str
    author: str
    series: str | None
    series_number: float | None
    ai_confidence: float
    reasoning_summary: str
    needs_human_review: bool

    @classmethod
    def from_tool_input(cls, data: dict) -> "AIIdentificationResult":
        return cls(
            title=data["title"],
            author=data["author"],
            series=data.get("series"),
            series_number=data.get("series_number"),
            ai_confidence=float(data["ai_confidence"]),
            reasoning_summary=data["reasoning_summary"],
            needs_human_review=bool(data["needs_human_review"]),
        )


@dataclass
class AIBookRequestResult:
    found: bool
    title: str | None
    author: str | None
    series: str | None
    series_number: float | None
    isbn13: str | None
    note: str | None

    @classmethod
    def from_tool_input(cls, data: dict) -> "AIBookRequestResult":
        return cls(
            found=bool(data["found"]),
            title=data.get("title"),
            author=data.get("author"),
            series=data.get("series"),
            series_number=data.get("series_number"),
            isbn13=data.get("isbn13"),
            note=data.get("note"),
        )


@dataclass
class AISeriesResult:
    series: str | None
    series_number: float | None

    @classmethod
    def from_tool_input(cls, data: dict) -> "AISeriesResult":
        return cls(series=data.get("series"), series_number=data.get("series_number"))


@dataclass
class AIAuditResult:
    verdict: str  # "stored_is_correct" | "stored_is_wrong" | "uncertain"
    series_is_real: bool
    corrected_title: str | None
    corrected_author: str | None
    corrected_series: str | None
    corrected_series_number: float | None
    explanation: str

    @classmethod
    def from_tool_input(cls, data: dict) -> "AIAuditResult":
        return cls(
            verdict=data["verdict"],
            series_is_real=bool(data["series_is_real"]),
            corrected_title=data.get("corrected_title"),
            corrected_author=data.get("corrected_author"),
            corrected_series=data.get("corrected_series"),
            corrected_series_number=data.get("corrected_series_number"),
            explanation=data["explanation"],
        )


@dataclass
class AISeriesMergeResult:
    is_same_series: bool
    canonical_series_name: str
    excluded_series_names: list[str]
    confidence: float
    explanation: str
    warnings: list[str]

    @classmethod
    def from_tool_input(cls, data: dict) -> "AISeriesMergeResult":
        return cls(
            is_same_series=bool(data["is_same_series"]),
            canonical_series_name=data["canonical_series_name"],
            excluded_series_names=list(data["excluded_series_names"]),
            confidence=float(data["confidence"]),
            explanation=data["explanation"],
            warnings=list(data["warnings"]),
        )
