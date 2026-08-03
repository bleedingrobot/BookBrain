from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./epub_librarian.db"

    google_client_id: str = ""
    google_client_secret: str = ""
    google_oauth_redirect_uri: str = "http://localhost:8000/api/auth/callback"

    token_encryption_key: str = ""

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5"
    google_books_api_key: str = ""

    frontend_origin: str = "http://localhost:5173"

    # EPUB safe-parsing limits (SPEC.md §1)
    epub_max_entry_bytes: int = 100 * 1024 * 1024
    epub_max_total_bytes: int = 500 * 1024 * 1024
    epub_max_entries: int = 10_000
    epub_parse_timeout_seconds: int = 10

    # Confidence thresholds (SPEC.md §5)
    confidence_auto_organize: int = 95
    confidence_auto_flagged: int = 85

    # Calibre CLI conversion (mobi/rtf -> epub before processing)
    ebook_convert_binary: str = "ebook-convert"
    ebook_convert_timeout_seconds: int = 120


@lru_cache
def get_settings() -> Settings:
    return Settings()
