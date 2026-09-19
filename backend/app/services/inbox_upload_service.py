"""Shared "validate a local file, then push it to the Drive inbox" tail,
used by both acquire_service (an acquisition provider's download) and
local_scan_service (a file the user already has locally, e.g. from a
torrent client). Previously each had its own copy of this logic; the
local_scan_service one was weaker (no size cap, no format check, no EPUB
zip-sanity check) — this closes that gap for both callers at once.

Deliberately does NOT delete or otherwise touch `path` — an acquisition
provider's temp download and the user's own torrents-folder file need
different cleanup semantics, and that decision belongs to the caller."""

from __future__ import annotations

import logging
import mimetypes
import re
import zipfile
from pathlib import Path

from app.providers.acquisition.exceptions import AcquisitionError
from app.providers.convert.calibre import is_convertible
from app.providers.drive.classify import is_supported_ebook
from app.providers.drive.provider import DriveProvider

logger = logging.getLogger(__name__)

_MAX_BYTES = 200 * 1024 * 1024  # a sane ceiling; real ebooks are a few MB


def _safe_name(name: str) -> str:
    """Drop any path components and characters Drive/Windows dislike."""
    name = Path(name.replace("\\", "/")).name.strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    return name or "download"


def _looks_like_epub(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as zf:
            return zf.read("mimetype").strip() == b"application/epub+zip"
    except (zipfile.BadZipFile, KeyError, OSError):
        return False


async def upload_local_file_to_inbox(
    path: Path,
    filename: str | None,
    drive_provider: DriveProvider,
    inbox_folder_id: str,
) -> dict:
    """Validate `path` (size, format, EPUB zip-sanity) and upload it to the
    Drive inbox. Returns a small summary; raises AcquisitionError on any
    failure (including a file that doesn't look usable). Does not touch
    `path` on disk either way — see module docstring."""
    size = path.stat().st_size
    if size == 0:
        raise AcquisitionError("downloaded file is empty")
    if size > _MAX_BYTES:
        raise AcquisitionError(f"downloaded file is implausibly large ({size / 1_048_576:.0f} MB) — skipping")

    name = _safe_name(filename or path.name)
    if not (is_supported_ebook(name) or is_convertible(name)):
        raise AcquisitionError(f"'{name}' isn't a format BookBrain ingests (epub/kpub/cbz/cbr/mobi/rtf/txt)")
    if name.lower().endswith(".epub") and not _looks_like_epub(path):
        raise AcquisitionError("the .epub is corrupt or not actually an EPUB")

    data = path.read_bytes()
    mime_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    uploaded = drive_provider.upload_new_file(
        name=name, data=data, parent_id=inbox_folder_id, mime_type=mime_type
    )

    logger.info("inbox_upload: uploaded %s (%d bytes) to inbox %s", name, size, inbox_folder_id)
    return {
        "filename": name,
        "drive_file_id": uploaded.get("id"),
        "size_bytes": size,
    }
