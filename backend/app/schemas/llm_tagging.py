from pydantic import BaseModel


class LlmTaggingSettings(BaseModel):
    enabled: bool


class LlmTaggingStatus(BaseModel):
    enabled: bool
    configured: bool  # whether ollama_host is set — enabled with this False does nothing
    full_done: int
    full_pending: int
