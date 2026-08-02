from pydantic import BaseModel


class DuplicateGroup(BaseModel):
    duplicate_file_id: int
    duplicate_filename: str
    quality_score: int | None
    primary_file_id: int | None
    primary_filename: str | None
    status_reason: str | None
    sha256: str


class ClearDuplicatesResult(BaseModel):
    cleared: int
    failed: int
