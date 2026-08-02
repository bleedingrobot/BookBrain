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
