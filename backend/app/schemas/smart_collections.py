from datetime import datetime

from pydantic import BaseModel


class SmartCollectionCreate(BaseModel):
    name: str
    description: str | None = None
    rule: str


class SmartCollectionUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    rule: str | None = None


class SmartCollectionOut(BaseModel):
    id: int
    name: str
    description: str | None
    rule: str
    created_at: datetime
    updated_at: datetime


class RulePreviewRequest(BaseModel):
    rule: str


class RulePreviewResult(BaseModel):
    count: int
    sample: list[str]
