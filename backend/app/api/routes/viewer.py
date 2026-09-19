"""The sibling-facing surface: passcode login + a thin Drive proxy behind it.

Google-login viewers (James/Tess) never hit this file — their browser talks
straight to Drive with their own OAuth token, unchanged. A passcode login has
no Google token of its own, so instead it gets a session cookie here
(/session) and every Drive-shaped call the viewer needs — list the library
tree, read/write a JSON sidecar, fetch raw bytes (a cover, an epub, a
download), copy/trash a file for the Kobo screen — goes through one of the
generic /drive/* routes below using *this backend's own* stored Drive
credential (see require_viewer_session).

Deliberately generic rather than one endpoint per feature: the viewer has a
couple dozen small Drive-sidecar files (wishlist, activity log, dashboard,
news, reading, prompts, lists, kobo devices, …) that all follow the same
"list a named file in the library folder, download it, JSON.parse it" shape
client-side (library-viewer/src/lib/drive.ts's findSidecarMeta +
fetchDriveBytes). Mirroring that shape here means every sidecar — including
ones added after this was written — works for passcode users with zero new
backend routes.

Trust note: the passcode is a household gate, not a per-feature ACL — like
Google sign-in already grants full personal Drive access (see
library-viewer/src/lib/googleAuth.ts's SCOPE_FULL comment), anyone who knows
the passcode can, through these routes, read or write anything this
backend's own Drive credential can reach, not just files under the library
folder. That mirrors the existing trust model ("no roles in this app" — see
wishlist.ts), just extended to a shared secret instead of a Google account.
"""

import json

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_viewer_session
from app.core.config import get_settings
from app.data.db import get_db
from app.data.repositories.settings_repository import SettingsRepository
from app.providers.drive.provider import DriveProvider
from app.schemas.viewer import (
    CopyRequest,
    DriveFileOut,
    PasscodeRequest,
    SidecarMetaOut,
    SidecarWriteResult,
    ViewerSessionStatus,
)
from app.services import viewer_session_service
from app.services.drive_service import DriveService

router = APIRouter(prefix="/viewer", tags=["viewer"])


def _cookie_kwargs() -> dict:
    settings = get_settings()
    secure = settings.viewer_cookie_secure
    return {
        "httponly": True,
        "secure": secure,
        # SameSite=None is required for the cookie to be sent from the
        # viewer's GitHub Pages origin to this backend's own origin — but
        # browsers reject SameSite=None without Secure, so local http-only
        # dev (viewer_cookie_secure=False) has to fall back to Lax instead.
        "samesite": "none" if secure else "lax",
        "max_age": settings.viewer_session_days * 86400,
        "path": "/api/viewer",
    }


@router.post("/session", status_code=204)
async def start_session(body: PasscodeRequest, response: Response) -> None:
    if not viewer_session_service.check_passcode(body.passcode):
        raise HTTPException(status_code=401, detail="wrong passcode")
    response.set_cookie(
        viewer_session_service.COOKIE_NAME,
        viewer_session_service.create_session_token(),
        **_cookie_kwargs(),
    )


@router.get("/session", response_model=ViewerSessionStatus)
async def session_status(request: Request) -> ViewerSessionStatus:
    token = request.cookies.get(viewer_session_service.COOKIE_NAME)
    return ViewerSessionStatus(authenticated=viewer_session_service.verify_session_token(token))


@router.delete("/session", status_code=204)
async def end_session(response: Response) -> None:
    response.delete_cookie(viewer_session_service.COOKIE_NAME, path="/api/viewer")


async def _library_folder_id(db: AsyncSession) -> str:
    library = await DriveService.get_library_folder_config(SettingsRepository(db))
    if library is None:
        raise HTTPException(status_code=400, detail="no library folder configured yet")
    return library.folder_id


@router.get("/drive/tree", response_model=list[DriveFileOut])
async def drive_tree(
    db: AsyncSession = Depends(get_db),
    provider: DriveProvider = Depends(require_viewer_session),
) -> list[DriveFileOut]:
    """Every supported ebook under the library folder, recursively — mirrors
    library-viewer/src/lib/drive.ts's listLibraryTree. The passcode path has
    no equivalent to Drive's changes.list feed, so unlike the Google-login
    path (librarySync.ts's incremental sync) this is always a full walk;
    fine at household scale."""
    folder_id = await _library_folder_id(db)
    files = provider.list_epub_files_recursive(folder_id)
    return [DriveFileOut(id=f["id"], name=f["name"]) for f in files]


@router.get("/drive/folder", response_model=list[DriveFileOut])
async def drive_folder(
    folder_id: str,
    provider: DriveProvider = Depends(require_viewer_session),
) -> list[DriveFileOut]:
    """Non-recursive listing of any folder this backend's Drive credential
    can see — used for the covers/ manifest (library-viewer/src/lib/covers.ts),
    keyed by the coversFolder id the client already has from the library
    index. Not scoped to the library subtree (see module docstring)."""
    files = provider.list_files_in_folder(folder_id)
    return [DriveFileOut(id=f["id"], name=f["name"]) for f in files]


@router.get("/drive/sidecar-meta", response_model=SidecarMetaOut | None)
async def sidecar_meta(
    name: str,
    db: AsyncSession = Depends(get_db),
    provider: DriveProvider = Depends(require_viewer_session),
) -> SidecarMetaOut | None:
    """id + modifiedTime for a named file directly in the library folder, or
    null if it doesn't exist yet — the "is my cache stale" half of every
    sidecar read. Pair with GET /drive/blob/{id} for the content."""
    folder_id = await _library_folder_id(db)
    found = provider.find_file_by_name(folder_id, name)
    if found is None:
        return None
    return SidecarMetaOut(id=found["id"], modifiedTime=found["modifiedTime"])


@router.put("/drive/sidecar", response_model=SidecarWriteResult)
async def write_sidecar(
    name: str,
    content: dict | list = Body(...),
    db: AsyncSession = Depends(get_db),
    provider: DriveProvider = Depends(require_viewer_session),
) -> SidecarWriteResult:
    """Create-or-overwrite a named JSON file directly in the library folder
    — mirrors writeJsonFile. Looks the file up by name itself (rather than
    trusting a client-supplied file id, the way the Google-login path's
    writeJsonFile does) since a stateless proxy call has nothing else to
    trust as current."""
    folder_id = await _library_folder_id(db)
    data = json.dumps(content).encode()
    existing = provider.find_file_by_name(folder_id, name)
    if existing is not None:
        provider.update_file_content(
            existing["id"], new_name=name, data=data, mime_type="application/json"
        )
        return SidecarWriteResult(id=existing["id"])
    created = provider.upload_new_file(
        name=name, data=data, parent_id=folder_id, mime_type="application/json"
    )
    return SidecarWriteResult(id=created["id"])


@router.get("/drive/blob/{file_id}")
async def drive_blob(
    file_id: str,
    provider: DriveProvider = Depends(require_viewer_session),
) -> Response:
    """Raw bytes for any Drive file id this backend's credential can read —
    a cover thumbnail, an epub for the in-browser reader, an epub download,
    or a JSON/binary sidecar's content (paired with GET /drive/sidecar-meta
    or /drive/tree for the id). Mirrors fetchDriveBlob's `alt=media`."""
    try:
        data = provider.download_file(file_id)
    except Exception as exc:  # noqa: BLE001 - surfaced as a generic 404 below
        raise HTTPException(status_code=404, detail="file not found") from exc
    return Response(content=data, media_type="application/octet-stream")


@router.post("/drive/copy/{file_id}", status_code=204)
async def drive_copy(
    file_id: str,
    body: CopyRequest,
    provider: DriveProvider = Depends(require_viewer_session),
) -> None:
    """Mirrors copyFileToFolder — "Send to Kobo" copies a library file into
    a device's synced folder without touching the original."""
    provider.copy_file(file_id, body.destinationFolderId)


@router.post("/drive/trash/{file_id}", status_code=204)
async def drive_trash(
    file_id: str,
    provider: DriveProvider = Depends(require_viewer_session),
) -> None:
    """Mirrors trashFile — removing a book from a Kobo device's synced
    folder (DeviceLibrary.tsx), not from the library itself."""
    provider.trash_file(file_id)
