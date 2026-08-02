from pydantic import BaseModel


class EvidenceItem(BaseModel):
    field_name: str
    value: str
    source: str


class CandidateItem(BaseModel):
    title: str | None
    author: str | None
    series: str | None
    series_number: float | None
    source: str


class ReviewSummary(BaseModel):
    id: int
    file_id: int
    filename: str
    status: str
    status_reason: str | None
    proposed_title: str | None
    proposed_author: str | None
    computed_confidence: int | None


class ReviewDetail(ReviewSummary):
    proposed_json: dict
    correction_json: dict | None
    reasoning_summary: str | None
    evidence: list[EvidenceItem]
    candidates: list[CandidateItem]


class CorrectReviewRequest(BaseModel):
    title: str
    author: str | None = None
    series: str | None = None
    series_number: float | None = None
    apply_to_similar: bool = False
