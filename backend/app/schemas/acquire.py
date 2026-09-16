from pydantic import BaseModel


class AcquireProviderStatus(BaseModel):
    name: str
    enabled: bool
    requires_process: bool  # true only for OpenBooks — has a child process to start/stop


class AcquireStatus(BaseModel):
    enabled: bool
    providers: list[AcquireProviderStatus] = []


class OpenBooksServerStatus(BaseModel):
    installed: bool
    running: bool
    managed: bool
    pid: int | None = None


class AcquireSearchRequest(BaseModel):
    query: str


class AcquireBook(BaseModel):
    server: str | None = None
    author: str
    title: str
    format: str
    size: str
    full: str
    provider: str = "openbooks"


class AcquireSearchResponse(BaseModel):
    results: list[AcquireBook]
    parse_errors: int = 0
    message: str | None = None


class AcquireDownloadRequest(BaseModel):
    full: str
    filename: str | None = None
    provider: str = "openbooks"


class AcquireDownloadResponse(BaseModel):
    filename: str
    drive_file_id: str | None = None
    size_bytes: int


class RequestCandidate(BaseModel):
    # None for a torrent request still in flight (or failed before a file
    # ever existed) — submit_tick sets candidate_provider/title/author at
    # submission time with no file to point `full` at yet.
    full: str | None
    title: str | None = None
    author: str | None = None
    format: str | None = None
    size: str | None = None
    server: str | None = None
    score: float | None = None
    provider: str = "openbooks"


class OpenRequest(BaseModel):
    request_id: str
    source: str = "wishlist"
    title: str
    author: str | None = None
    requested_by: str | None = None
    cover: str | None = None
    status: str
    candidate: RequestCandidate | None = None
    alternatives: list[RequestCandidate] = []
    score: float | None = None
    message: str | None = None
    resolved_at: str | None = None


class RequestRefreshJob(BaseModel):
    job_id: str
    status: str
    searched: int = 0
    total: int = 0
    with_candidates: int = 0
    outstanding: int = 0
    detail: str | None = None


class ApproveRequestBody(BaseModel):
    full: str | None = None  # pick a specific alternative; omit to take the best


class AutoGetSettings(BaseModel):
    enabled: bool


class AcquireSuggestion(BaseModel):
    title: str
    author: str | None = None
    isbn13: str | None = None
    from_list: str | None = None


class AcquireSuggestions(BaseModel):
    want_to_read: list[AcquireSuggestion] = []
    from_lists: list[AcquireSuggestion] = []
