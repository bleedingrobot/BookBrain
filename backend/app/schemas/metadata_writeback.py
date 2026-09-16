import enum

from pydantic import BaseModel


class MetadataWritebackJobState(str, enum.Enum):
    running = "running"
    done = "done"
    failed = "failed"


class MetadataWritebackJobStatus(BaseModel):
    job_id: str
    status: MetadataWritebackJobState
    dry_run: bool = False
    # Files whose embedded OPF metadata was (or, on a dry run, would be) rewritten.
    updated: int = 0
    # Already stamped with the current resolved metadata — nothing to do.
    skipped: int = 0
    failed: int = 0
    remaining: int = 0
