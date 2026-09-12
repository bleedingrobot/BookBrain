"""One-off: bulk-add books from a personal fantasy spreadsheet that aren't
already in the library onto the household wishlist (bookbrain-wishlist.json),
so the existing OpenBooks acquisition pipeline (requests/refresh + nightly +
autoget) slowly works through them. Box sets/omnibuses were pre-filtered out
(OpenBooks searches individual titles, not compilations) — see
wishlist_additions.json, built from a title+author match of the sheet against
the library DB.

Dedupes against whatever is already on the wishlist (normalised title+author,
same rule as the viewer's own alreadyListed()) so re-running is safe.

Run once, by hand: `python tools/bulk_add_wishlist.py <path to wishlist_additions.json>`
"""

import asyncio
import json
import re
import secrets
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from app.data.db import async_session_factory
from app.data.repositories.settings_repository import SettingsRepository
from app.providers.drive.client import build_drive_service
from app.providers.drive.provider import DriveProvider
from app.services.auth_service import get_auth_service
from app.services.drive_service import DriveService

WISHLIST_FILENAME = "bookbrain-wishlist.json"
REQUESTED_BY = "James"

_ARTICLE_RE = re.compile(r"^(the|a|an)\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_SUBTITLE_RE = re.compile(r"\s*[:;(].*$")
_WORD_RE = re.compile(r"[a-z0-9]+")


def norm_title(s: str | None) -> str:
    if not s:
        return ""
    s = s.lower()
    s = _SUBTITLE_RE.sub("", s)
    s = _ARTICLE_RE.sub("", s)
    return _NON_ALNUM_RE.sub("", s)


def norm_words(s: str | None) -> str:
    if not s:
        return ""
    return " ".join(sorted(_WORD_RE.findall(s.lower())))


def make_id() -> str:
    return f"{int(time.time() * 1000)}-{secrets.token_hex(3)}"


async def main(additions_path: Path) -> None:
    additions = json.loads(additions_path.read_text(encoding="utf-8"))

    async with async_session_factory() as session:
        settings_repo = SettingsRepository(session)
        creds = await get_auth_service().get_credentials(settings_repo)
        if creds is None:
            print("Not connected to Google Drive — connect in the admin Settings page first.")
            return
        library = await DriveService.get_library_folder_config(settings_repo)
        if library is None:
            print("No library folder configured yet.")
            return

    provider = DriveProvider(build_drive_service(creds))
    found = next(
        (f for f in provider.list_files_in_folder(library.folder_id) if f["name"] == WISHLIST_FILENAME),
        None,
    )
    if found is None:
        raw = {"version": 2, "items": []}
        file_id = None
    else:
        raw = json.loads(provider.download_file(found["id"]).decode("utf-8"))
        file_id = found["id"]

    existing_items = [i for i in (raw.get("items") or []) if isinstance(i, dict)]
    existing_keys = {
        (norm_title(i.get("title")), norm_words(i.get("author")) or None) for i in existing_items
    }

    now = datetime.now(UTC).isoformat()
    new_items = []
    seen_in_batch = set()
    skipped_dupe = 0

    for entry in additions:
        title = entry.get("title")
        if not title:
            continue
        author = entry.get("author")
        key = (norm_title(title), norm_words(author) or None)
        if key in existing_keys or key in seen_in_batch:
            skipped_dupe += 1
            continue
        seen_in_batch.add(key)
        new_items.append(
            {
                "id": make_id(),
                "title": title,
                "author": author,
                "series": entry.get("series"),
                "isbn13": None,
                "cover": None,
                "note": "",
                "requestedBy": REQUESTED_BY,
                "status": "wanted",
                "statusNote": "",
                "statusBy": None,
                "statusAt": None,
                "addedAt": now,
                "acquired": False,
                "acquiredAt": None,
            }
        )

    raw["version"] = 2
    raw["items"] = new_items + existing_items

    payload = json.dumps(raw, ensure_ascii=False).encode("utf-8")
    if file_id is None:
        provider.upload_new_file(
            name=WISHLIST_FILENAME,
            data=payload,
            parent_id=library.folder_id,
            mime_type="application/json",
        )
    else:
        provider.update_file_content(
            file_id,
            new_name=WISHLIST_FILENAME,
            data=payload,
            mime_type="application/json",
        )

    print(f"Added {len(new_items)} new wishlist entries (skipped {skipped_dupe} already on the list).")
    print(f"Wishlist now has {len(raw['items'])} total items.")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1])))
