from pydantic import BaseModel


class LibraryExportResult(BaseModel):
    name: str
    url: str


class RebuildEstimate(BaseModel):
    files_to_identify: int
    estimated_cost_usd: float
    # False when we couldn't list the Drive tree (no creds / no folder / API
    # error) — the frontend then degrades to "couldn't estimate, proceed?".
    estimated: bool
