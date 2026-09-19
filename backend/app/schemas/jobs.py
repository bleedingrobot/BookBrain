from pydantic import BaseModel, Field


class NightlyRunInfo(BaseModel):
    status: str  # running | success | failed
    trigger: str  # scheduler | cli | manual
    started_at: str
    finished_at: str | None = None
    summary: str | None = None
    error: str | None = None


class NightlySettings(BaseModel):
    enabled: bool
    hour: int = Field(ge=0, le=23)
    last_run: NightlyRunInfo | None = None


class BackupSettings(BaseModel):
    enabled: bool
    hour: int = Field(ge=0, le=23)
    last_run: NightlyRunInfo | None = None
