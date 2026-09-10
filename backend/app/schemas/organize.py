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
    # The confidence at/above which a scanned file auto-organizes with no
    # review. Lower = more of the review queue flows straight to the library.
    # Clamped server-side to [0, 100].
    auto_organize_min_confidence: int = 85
    # What a scan trashes once it's detected duplicates: "off" | "exact"
    # (byte-identical re-uploads) | "all" (+ same-book different editions).
    auto_trash_duplicates: str = "exact"
