from pydantic import BaseModel


class AuthStartRequest(BaseModel):
    folder_mode: str  # "existing" | "app_created"


class AuthStartResponse(BaseModel):
    authorization_url: str


class AuthStatus(BaseModel):
    connected: bool
    scope_mode: str | None
