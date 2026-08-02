from pydantic import BaseModel


class DuplicateGroup(BaseModel):
    duplicate_file_id: int
    duplicate_filename: str
    quality_score: int | None
    primary_file_id: int | None
    primary_filename: str | None
    sha256: str
