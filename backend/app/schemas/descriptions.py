import enum

from pydantic import BaseModel


class DescriptionJobState(str, enum.Enum):
    running = "running"
    done = "done"
    failed = "failed"


class DescriptionJobStatus(BaseModel):
    job_id: str
    status: DescriptionJobState
    from_provider: int = 0
    from_ai: int = 0
    not_found: int = 0
    # prompts/29 follow-up: EPUB-fallback books whose embedded blurb was
    # already at least as good as anything a provider returned.
    kept_epub: int = 0
    remaining: int = 0


class DescriptionBackfillEstimate(BaseModel):
    books_missing: int
    will_process: int
    cap: int
    estimated_cost_usd: float
