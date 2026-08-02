from pydantic import BaseModel


class OperationSummary(BaseModel):
    id: int
    timestamp: str
    file_id: int
    filename: str
    action: str
    original_name: str | None
    original_parent_id: str | None
    new_name: str | None
    new_parent_id: str | None
    confidence: int | None
    model: str | None
    reason: str | None
    status: str
    dry_run: bool
