import enum

from pydantic import BaseModel


class ScanJobState(str, enum.Enum):
    running = "running"
    done = "done"
    failed = "failed"


class ScanJobStatus(BaseModel):
    job_id: str
    status: ScanJobState
    detail: str | None = None
