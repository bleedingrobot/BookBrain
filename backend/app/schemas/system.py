from pydantic import BaseModel


class SystemStatus(BaseModel):
    anthropic_configured: bool
    google_books_configured: bool
    confidence_auto_organize: int
    confidence_auto_flagged: int
