import enum

from pydantic import BaseModel


class CoverJobState(str, enum.Enum):
    running = "running"
    done = "done"
    failed = "failed"


class CoverJobStatus(BaseModel):
    job_id: str
    status: CoverJobState
    generated: int = 0
    no_cover: int = 0
    failed: int = 0
    remaining: int = 0
