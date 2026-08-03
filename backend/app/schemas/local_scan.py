from pydantic import BaseModel


class LocalFileSummary(BaseModel):
    id: int
    filename: str
    path: str
    size_bytes: int


class FileIdsRequest(BaseModel):
    file_ids: list[int]


class CopyResult(BaseModel):
    copied: int
    failed: int


class DismissResult(BaseModel):
    dismissed: int
