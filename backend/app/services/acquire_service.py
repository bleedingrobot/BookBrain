"""Glue between an AcquisitionProvider and the Drive inbox.

The one non-trivial step: after a provider drops a file on local disk, push
it into the Drive inbox folder so the existing scan -> identify -> organize
pipeline picks it up on the next run. Nothing here identifies or renames the
book — that's the pipeline's job. Validation + upload itself lives in
inbox_upload_service (shared with local_scan_service).
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.providers.acquisition.base import AcquisitionProvider
from app.providers.drive.provider import DriveProvider
from app.services import inbox_upload_service

logger = logging.getLogger(__name__)


async def acquire_to_inbox(
    provider: AcquisitionProvider,
    handle: str,
    filename: str | None,
    drive_provider: DriveProvider,
    inbox_folder_id: str,
) -> dict:
    """Download `handle` via `provider` and upload the resulting file to the
    Drive inbox. Returns a small summary; raises AcquisitionError on any
    failure (including a downloaded file that doesn't look usable)."""
    path: Path = await provider.download(handle)

    result = await inbox_upload_service.upload_local_file_to_inbox(
        path, filename, drive_provider, inbox_folder_id
    )

    # The file is safely in Drive now; clear the local copy so the
    # provider's download dir doesn't grow unbounded. Best effort, and only
    # on success — a failed upload deliberately leaves the file on disk for
    # inspection/retry.
    try:
        path.unlink()
    except OSError:
        logger.warning("acquire: could not delete local file %s", path)

    return result
