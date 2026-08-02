from google.oauth2.credentials import Credentials
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.db import get_db
from app.data.repositories.settings_repository import SettingsRepository
from app.schemas.drive import (
    CreateFolderRequest,
    DriveFileListing,
    DriveFolder,
    FolderConfig,
    SelectFolderRequest,
)
from app.services.auth_service import AuthService, get_auth_service
from app.services.drive_service import DriveService, SharedDriveError

router = APIRouter(prefix="/drive", tags=["drive"])


async def get_credentials(
    db: AsyncSession = Depends(get_db), auth: AuthService = Depends(get_auth_service)
) -> Credentials:
    creds = await auth.get_credentials(SettingsRepository(db))
    if creds is None:
        raise HTTPException(status_code=401, detail="not connected to Google Drive")
    return creds


@router.get("/folders", response_model=list[DriveFolder])
async def list_folders(
    parent_id: str | None = None, creds: Credentials = Depends(get_credentials)
) -> list[DriveFolder]:
    return DriveService(creds).list_folders(parent_id)


@router.post("/folders", response_model=DriveFolder)
async def create_folder(
    body: CreateFolderRequest, creds: Credentials = Depends(get_credentials)
) -> DriveFolder:
    return DriveService(creds).create_folder(body.name, body.parent_id)


@router.get("/inbox-folder", response_model=FolderConfig | None)
async def get_inbox_folder(db: AsyncSession = Depends(get_db)) -> FolderConfig | None:
    return await DriveService.get_inbox_folder_config(SettingsRepository(db))


@router.post("/inbox-folder/select", response_model=FolderConfig)
async def select_inbox_folder(
    body: SelectFolderRequest,
    db: AsyncSession = Depends(get_db),
    creds: Credentials = Depends(get_credentials),
) -> FolderConfig:
    try:
        return await DriveService(creds).select_existing_folder(
            body.folder_id, SettingsRepository(db)
        )
    except SharedDriveError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/inbox-folder/create", response_model=FolderConfig)
async def create_inbox_folder(
    body: CreateFolderRequest,
    db: AsyncSession = Depends(get_db),
    creds: Credentials = Depends(get_credentials),
) -> FolderConfig:
    return await DriveService(creds).create_and_select_inbox_folder(
        body.name, SettingsRepository(db)
    )


@router.get("/library-folder", response_model=FolderConfig | None)
async def get_library_folder(db: AsyncSession = Depends(get_db)) -> FolderConfig | None:
    return await DriveService.get_library_folder_config(SettingsRepository(db))


@router.post("/library-folder/select", response_model=FolderConfig)
async def select_library_folder(
    body: SelectFolderRequest,
    db: AsyncSession = Depends(get_db),
    creds: Credentials = Depends(get_credentials),
) -> FolderConfig:
    try:
        return await DriveService(creds).select_existing_library_folder(
            body.folder_id, SettingsRepository(db)
        )
    except SharedDriveError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/library-folder/create", response_model=FolderConfig)
async def create_library_folder(
    body: CreateFolderRequest,
    db: AsyncSession = Depends(get_db),
    creds: Credentials = Depends(get_credentials),
) -> FolderConfig:
    return await DriveService(creds).create_and_select_library_folder(
        body.name, SettingsRepository(db)
    )


@router.get("/files", response_model=list[DriveFileListing])
async def list_files(
    db: AsyncSession = Depends(get_db), creds: Credentials = Depends(get_credentials)
) -> list[DriveFileListing]:
    config = await DriveService.get_inbox_folder_config(SettingsRepository(db))
    if config is None:
        raise HTTPException(status_code=400, detail="no inbox folder configured yet")
    return DriveService(creds).list_epub_files(config.folder_id)
