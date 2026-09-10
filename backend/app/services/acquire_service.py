"""Glue between the OpenBooks client and the Drive inbox.

The one non-trivial step: after OpenBooks drops a file on local disk, push it
into the Drive inbox folder so the existing scan -> identify -> organize
pipeline picks it up on the next run. Nothing here identifies or renames the
book — that's the pipeline's job.
"""

from __future__ import annotations

import logging
import mimetypes
import re
import zipfile
from pathlib import Path

from app.providers.convert.calibre import is_convertible
from app.providers.drive.classify import is_supported_ebook
from app.providers.drive.provider import DriveProvider
from app.services.openbooks_service import OpenBooksError, download

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


async def acquire_to_inbox(
    full: str,
    filename: str | None,
    provider: DriveProvider,
    inbox_folder_id: str,
) -> dict:
    """Download `full` via OpenBooks and upload the resulting file to the
    Drive inbox. Returns a small summary; raises OpenBooksError on any
    failure (including a downloaded file that doesn't look usable)."""
    path = await download(full)

    size = path.stat().st_size
    if size == 0:
        raise OpenBooksError("OpenBooks returned an empty file")
    if size > _MAX_BYTES:
        raise OpenBooksError(f"downloaded file is implausibly large ({size / 1_048_576:.0f} MB) — skipping")

    name = _safe_name(filename or path.name)
    if not (is_supported_ebook(name) or is_convertible(name)):
        raise OpenBooksError(
            f"'{name}' isn't a format BookBrain ingests (epub/kpub/cbz/cbr/mobi/rtf/txt)"
        )
    if name.lower().endswith(".epub") and not _looks_like_epub(path):
        raise OpenBooksError("the downloaded .epub is corrupt or not actually an EPUB")

    data = path.read_bytes()
    mime_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    uploaded = provider.upload_new_file(
        name=name, data=data, parent_id=inbox_folder_id, mime_type=mime_type
    )

    # The file is safely in Drive now; clear the local copy so
    # openbooks-dl/ doesn't grow unbounded. Best effort.
    try:
        path.unlink()
    except OSError:
        logger.warning("acquire: could not delete local file %s", path)

    logger.info("acquire: uploaded %s (%d bytes) to inbox %s", name, size, inbox_folder_id)
    return {
        "filename": name,
        "drive_file_id": uploaded.get("id"),
        "size_bytes": size,
    }
