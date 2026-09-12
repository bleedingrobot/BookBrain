from pydantic import BaseModel


class RecentlyOrganizedItem(BaseModel):
    """One file auto-organized inside the requested window (prompts/15 Stage I).
    `confidence` is the identification confidence it was moved at;
    `evidence_summary` is a one-line "what this was based on"; `current_status`
    is where the file sits now (`organised`, or `inbox`/`review` if a
    correction has since pulled it back)."""

    file_id: int
    operation_id: int
    organized_at: str
    filename: str
    title: str | None
    author: str | None
    series: str | None
    series_number: float | None
    confidence: int | None
    current_status: str
    evidence_summary: str
    confirmed: bool


class HeldFileItem(BaseModel):
    """A file that has cleared the confidence bar but is waiting out
    `settings.organize_hold_hours` before the organize pass will move it.
    Only ever present when the hold is > 0."""

    file_id: int
    filename: str
    title: str | None
    author: str | None
    series: str | None
    series_number: float | None
    confidence: int | None
    evidence_summary: str
    held_since: str
    eligible_at: str


class RecentlyOrganizedResponse(BaseModel):
    since_hours: int
    hold_hours: int
    organized: list[RecentlyOrganizedItem]
    held: list[HeldFileItem]
