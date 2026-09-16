from pydantic import BaseModel


class LlmTaggingSettings(BaseModel):
    enabled: bool


class LlmTaggingCurrent(BaseModel):
    title: str
    author: str | None
    status: str  # "mapping" | "reducing"
    chunks_done: int
    chunks_total: int


class LlmTaggingRecent(BaseModel):
    title: str
    author: str | None
    generated_at: str | None
    genres: list[str]


class LlmTaggingError(BaseModel):
    title: str
    author: str | None
    error: str
    failed_at: str | None


class LlmTaggingStatus(BaseModel):
    enabled: bool
    configured: bool  # whether ollama_host is set — enabled with this False does nothing
    full_done: int
    full_pending: int
    current: LlmTaggingCurrent | None = None  # book mid-map/reduce right now, if any
    recent: list[LlmTaggingRecent] = []  # most recently completed, newest first
    last_error: LlmTaggingError | None = None
