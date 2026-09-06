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

    # prompts/15 Stage A — web-search grounding for the identify call. When on
    # (default), the AI identification path may call the Anthropic web_search
    # server tool to verify title/author/series/first-publication year against
    # the live web before answering — directly targeting the post-training-cutoff
    # "invented a plausible series" failure. Gated per-call by
    # identification_service.should_ground so a clean multi-provider match with
    # an ISBN doesn't pay for a search. Turn off to keep identification fully
    # offline (tests never hit the network regardless).
    ai_web_search_enabled: bool = True
    ai_web_search_max_uses: int = 3

    frontend_origin: str = "http://localhost:5173"

    # EPUB safe-parsing limits (SPEC.md §1)
    epub_max_entry_bytes: int = 100 * 1024 * 1024
    epub_max_total_bytes: int = 500 * 1024 * 1024
    epub_max_entries: int = 10_000
    epub_parse_timeout_seconds: int = 10

    # Confidence thresholds (SPEC.md §5)
    confidence_auto_organize: int = 95
    confidence_auto_flagged: int = 85

    # AI spend guard rails (finding 11). Per-call figures are padded
    # estimates in the same spirit as reident_audit_service's
    # ~1.5k in + 0.4k out; they exist to show the user a "~$X" before a
    # click, not to bill anything.
    ai_description_cap: int = 200  # max model-written blurbs per backfill run
    ai_description_cost_usd: float = 0.01  # describe(): ~150 in + ~400 out
    # a full identify pass. Padded upward from 0.03 for prompts/15 Stage A:
    # most AI-path identifies now ground (web_search, ~$0.01/search x up to 3,
    # plus the larger grounded prompt + result tokens).
    ai_identify_cost_usd: float = 0.06

    # Calibre CLI conversion (mobi/rtf/txt -> epub before processing)
    ebook_convert_binary: str = "ebook-convert"
    ebook_convert_timeout_seconds: int = 120

    # 7-Zip CLI, used only to read .cbr (RAR) comic archives — .cbr is kept
    # as-is like .cbz, never converted. Empty = auto-detect: PATH (7z / 7za /
    # 7zz), then the standard Windows install dir.
    seven_zip_binary: str = ""
    seven_zip_timeout_seconds: int = 60

    # Local folder watched for new ebooks (e.g. a torrents download dir) to
    # offer copying into the Drive inbox
    torrents_watch_folder: str = r"D:\Torrents"


@lru_cache
def get_settings() -> Settings:
    return Settings()
