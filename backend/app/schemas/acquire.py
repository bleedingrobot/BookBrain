from pydantic import BaseModel


class AcquireStatus(BaseModel):
    enabled: bool


class AcquireSearchRequest(BaseModel):
    query: str


class AcquireBook(BaseModel):
    server: str
    author: str
    title: str
    format: str
    size: str
    full: str


class AcquireSearchResponse(BaseModel):
    results: list[AcquireBook]
    parse_errors: int = 0
    message: str | None = None


class AcquireDownloadRequest(BaseModel):
    full: str
    filename: str | None = None


class AcquireDownloadResponse(BaseModel):
    filename: str
    drive_file_id: str | None = None
    size_bytes: int
