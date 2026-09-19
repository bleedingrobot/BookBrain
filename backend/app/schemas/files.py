from pydantic import BaseModel


class ConfirmBatchRequest(BaseModel):
    file_ids: list[int]


class ConfirmBatchResult(BaseModel):
    confirmed: int
    skipped: int


class FileSummary(BaseModel):
    id: int
    filename: str
    status: str
    status_reason: str | None
    book_title: str | None
    book_author: str | None
    book_series: str | None
    book_series_number: float | None
    computed_confidence: int | None
    ai_reasoning: str | None
    quality_score: int | None
    discovered_at: str
