import enum

from pydantic import BaseModel


class OrganizeJobState(str, enum.Enum):
    running = "running"
    done = "done"
    failed = "failed"


class OrganizeJobStatus(BaseModel):
    job_id: str
    status: OrganizeJobState
    detail: str | None = None


class OrganizeSettings(BaseModel):
    dry_run: bool
