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


class AgreementLine(BaseModel):
    """A field where >=2 stored candidates agree, for the reviewer's
    reference — see app.services.provider_agreement. Purely informational;
    it doesn't reflect or affect the computed confidence score."""

    field: str
    value: str
    provider_count: int
    sources: list[str]


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
    agreement: list[AgreementLine]


class CorrectReviewRequest(BaseModel):
    title: str
    author: str | None = None
    series: str | None = None
    series_number: float | None = None
    apply_to_similar: bool = False
