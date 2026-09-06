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
    # prompts/15 Stage I soft-hold. 0 = today's behaviour (organize the moment
    # a file clears the confidence bar). > 0 delays an auto-eligible file that
    # many hours so a human can catch a rare miss in the "Recently
    # auto-organized" tray first. Clamped server-side to [0, 720].
    hold_hours: int = 0
