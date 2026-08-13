import enum

from pydantic import BaseModel


class OrganizeJobState(str, enum.Enum):
    running = "running"
    done = "done"
    failed = "failed"


class OrganizeFailure(BaseModel):
    filename: str
    reason: str


class OrganizeJobStatus(BaseModel):
    job_id: str
    status: OrganizeJobState
    detail: str | None = None
    failures: list[OrganizeFailure] = []


class OrganizeSettings(BaseModel):
    dry_run: bool
