import enum

from pydantic import BaseModel


class ReidentSignal(str, enum.Enum):
    """Which divergence check fired for a row. Multiple can fire for one book."""

    # Stored series isn't backed by the EPUB, any stored candidate, or any
    # provider now — and wasn't set by a human. The classic AI invention.
    series_unverified = "series_unverified"
    # Providers now agree on a title that isn't the stored one.
    title_disagrees = "title_disagrees"
    # Providers now agree on an author that isn't the stored one.
    author_disagrees = "author_disagrees"
    # The book's stored ISBN now resolves to a different work.
    isbn_points_elsewhere = "isbn_points_elsewhere"
    # App-computed confidence for this book is below confidence_auto_organize.
    below_auto_organize = "below_auto_organize"
    # Another organised book resolves to the same canonical identity.
    possible_duplicate = "possible_duplicate"


class ReidentDivergence(BaseModel):
    book_id: int
    file_id: int
    filename: str

    stored_title: str
    stored_author: str | None
    stored_series: str | None
    stored_series_number: float | None
    stored_confidence: int | None
    # Stored answer came from a human /correct or a library_rule — provider
    # disagreement is expected and the human-ruled fields aren't flagged.
    stored_from_human: bool

    signals: list[ReidentSignal]
    # Human-readable evidence lines, one per point — so the reason a row is
    # here is always visible (SPEC §1).
    evidence: list[str]
    recomputed_confidence: int | None
    duplicate_of_book_id: int | None = None

    # Filled in only by an opt-in deep re-check (costs API credits).
    deep_check_verdict: str | None = None
    deep_check_explanation: str | None = None
    deep_check_suggested_title: str | None = None
    deep_check_suggested_author: str | None = None
    deep_check_suggested_series: str | None = None
    deep_check_suggested_series_number: float | None = None


class ReidentReport(BaseModel):
    generated_at: str | None = None
    total_organised_books: int = 0
    checked: int = 0
    providers_unavailable: int = 0
    divergences: list[ReidentDivergence] = []


class ReidentRebuildJobState(str, enum.Enum):
    running = "running"
    done = "done"
    failed = "failed"


class ReidentRebuildJobStatus(BaseModel):
    job_id: str
    status: ReidentRebuildJobState
    checked: int = 0
    total: int = 0
    flagged: int = 0
    detail: str | None = None


class ReidentDismissRequest(BaseModel):
    book_id: int


class ReidentDismissedInfo(BaseModel):
    book_id: int
    created_at: str


class DeepCheckRequest(BaseModel):
    book_ids: list[int]


class DeepCheckEstimate(BaseModel):
    eligible: int
    will_check: int
    cap: int
    estimated_cost_usd: float


class DeepCheckRow(BaseModel):
    book_id: int
    verdict: str
    explanation: str
    suggested_title: str | None = None
    suggested_author: str | None = None
    suggested_series: str | None = None
    suggested_series_number: float | None = None


class DeepCheckResult(BaseModel):
    rechecked: int
    stored_is_wrong: int
    stored_is_correct: int
    uncertain: int
    failed: int
    rows: list[DeepCheckRow]
