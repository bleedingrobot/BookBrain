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
    # Per-phase timing breakdown (download/convert/parse/candidates/
    # ai_identify/db), each {"total_seconds", "count", "max_seconds"} —
    # see scan_service.PhaseTimings. Diagnostic only; not yet rendered by
    # the frontend, but queryable via this same job-status endpoint.
    phase_timings: dict[str, dict[str, float]] | None = None
    batch_wall_seconds: float | None = None
