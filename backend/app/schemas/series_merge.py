from pydantic import BaseModel


class SeriesMergeBookInfo(BaseModel):
    id: int
    canonical_title: str
    series_number: float | None
    author_name: str | None
    file_count: int


class SeriesMergeSeriesInfo(BaseModel):
    id: int
    name: str
    books: list[SeriesMergeBookInfo]


class SeriesMergePlannedMove(BaseModel):
    book_title: str
    from_series_name: str
    current_filename: str
    new_filename: str
    new_folder_path: str


class SeriesMergePlan(BaseModel):
    # Exactly what apply_series_merge will do if every file succeeds —
    # computed with the same pure build_target_path used at apply time, so
    # this can never drift from what actually happens. Shown next to the
    # Apply fix button so the effect is visible before clicking, not just
    # described in prose.
    moves: list[SeriesMergePlannedMove]
    series_to_delete: list[str]


class SeriesMergeProposal(BaseModel):
    is_same_series: bool
    canonical_series_name: str
    # Names (from `series`) that should be left untouched — a cluster can
    # have more than two members, and not all of them are necessarily the
    # same series as canonical_series_name.
    excluded_series_names: list[str]
    confidence: int
    explanation: str
    warnings: list[str]
    series: list[SeriesMergeSeriesInfo]
    plan: SeriesMergePlan


class SeriesMergeFileFailure(BaseModel):
    file_id: int
    filename: str
    reason: str


class SeriesMergeBookSkip(BaseModel):
    book_id: int
    canonical_title: str
    reason: str


class SeriesMergeResult(BaseModel):
    canonical_series_id: int
    canonical_series_name: str
    moved_files: int
    already_in_place_files: int
    failed_files: list[SeriesMergeFileFailure]
    repointed_books: int
    skipped_books: list[SeriesMergeBookSkip]
    deleted_series_ids: list[int]


class SeriesMergeRequest(BaseModel):
    series_ids: list[int]


class SeriesMergeApplyRequest(BaseModel):
    series_ids: list[int]
    canonical_series_name: str
    # Names to leave untouched — echoed back from investigate's
    # excluded_series_names. A cluster can have more than two members; only
    # series_ids minus canonical minus this list actually get merged.
    excluded_series_names: list[str] = []
    # Must echo back investigate's is_same_series=True. A frontend bug once
    # let "Apply fix" render even when Claude had said these are two
    # genuinely different series — this makes that acknowledgement an
    # explicit, required part of the API contract rather than a UI-only
    # gate, so the same mistake can't silently reoccur.
    confirm_same_series: bool
