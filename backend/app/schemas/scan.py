import enum

from pydantic import BaseModel


class ScanJobState(str, enum.Enum):
    running = "running"
    done = "done"
    failed = "failed"


class ScanFailure(BaseModel):
    filename: str
    reason: str


class ScanJobStatus(BaseModel):
    job_id: str
    status: ScanJobState
    detail: str | None = None
    failures: list[ScanFailure] = []
