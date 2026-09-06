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
    # the live web before answering — targeting the post-training-cutoff
    # "invented a plausible series" failure. It is billed per search, so
    # identification_service.should_ground only turns it on for books with a
    # recent-year signal (filename or provider pub date within ~2 years) — a
    # few percent of AI-path calls, not all of them. Set False to disable
    # entirely (tests never hit the network regardless).
    ai_web_search_enabled: bool = True
    ai_web_search_max_uses: int = 2

    # prompts/15 Stage H — a second, adversarial AI call ("confirm this exactly
    # or correct it") for AI-path identifications that land in the uncertain
    # band (70 <= computed_confidence < confidence_auto_organize). OFF by
    # default: it is one extra ~$0.03 model call per uncertain new book, and
    # James is hard budget-limited. Turn on only when the review queue is
    # noisier than the spend. When on: an agreeing verifier lifts confidence a
    # little (double-checked); a disagreeing one takes the correction AND forces
    # the review queue (two AI opinions differed — a human should look).
    ai_verify_enabled: bool = False
    ai_verify_cost_usd: float = 0.03

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
    # a full forced-tool identify pass. Only a few percent of these ground
    # (prompts/15 Stage A — recent books only), so the blended figure is
    # barely above the un-grounded ~0.03.
    ai_identify_cost_usd: float = 0.035

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
