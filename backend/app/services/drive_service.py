from google.oauth2.credentials import Credentials

from app.core.settings_keys import (
    DRIVE_INBOX_FOLDER_CREATED_BY_APP,
    DRIVE_INBOX_FOLDER_ID,
    DRIVE_INBOX_FOLDER_NAME,
    DRIVE_LIBRARY_FOLDER_CREATED_BY_APP,
    DRIVE_LIBRARY_FOLDER_ID,
    DRIVE_LIBRARY_FOLDER_NAME,
)
from app.data.repositories.settings_repository import SettingsRepository
from app.providers.drive.classify import classify_file
from app.providers.drive.client import build_drive_service
from app.providers.drive.provider import DriveProvider
from app.schemas.drive import DriveFileListing, DriveFolder, FolderConfig


class SharedDriveError(Exception):
    """Raised when a folder pick resolves to a Shared Drive. SPEC.md §2:
    v1 is My Drive only, rejected explicitly rather than failing later on
    missing supportsAllDrives/includeItemsFromAllDrives params."""


class DriveService:
    def __init__(self, creds: Credentials) -> None:
        self._provider = DriveProvider(build_drive_service(creds))

    def list_folders(self, parent_id: str | None) -> list[DriveFolder]:
        return [DriveFolder(id=f["id"], name=f["name"]) for f in self._provider.list_folders(parent_id)]

    def create_folder(self, name: str, parent_id: str | None = None) -> DriveFolder:
        folder = self._provider.create_folder(name, parent_id)
        return DriveFolder(id=folder["id"], name=folder["name"])

    def list_epub_files(self, folder_id: str) -> list[DriveFileListing]:
        return [
            DriveFileListing(
                id=raw["id"],
                name=raw["name"],
                size_bytes=int(raw.get("size", 0)),
                status_reason=classify_file(raw),
            )
            for raw in self._provider.list_epub_files(folder_id)
        ]

    def _assert_not_shared_drive(self, folder_id: str) -> dict:
        meta = self._provider.get_folder(folder_id)
        if meta.get("driveId"):
            raise SharedDriveError(
                "That folder is on a Shared Drive. Shared Drives aren't supported "
                "yet — pick a folder under My Drive."
            )
        return meta

    async def _select_existing(
        self,
        folder_id: str,
        settings_repo: SettingsRepository,
        *,
        id_key: str,
        name_key: str,
        created_key: str,
    ) -> FolderConfig:
        meta = self._assert_not_shared_drive(folder_id)
        await settings_repo.set(id_key, folder_id)
        await settings_repo.set(name_key, meta["name"])
        await settings_repo.set(created_key, "false")
        return FolderConfig(folder_id=folder_id, folder_name=meta["name"], created_by_app=False)

    async def _create_and_select(
        self,
        name: str,
        settings_repo: SettingsRepository,
        *,
        id_key: str,
        name_key: str,
        created_key: str,
    ) -> FolderConfig:
        folder = self._provider.create_folder(name)
        await settings_repo.set(id_key, folder["id"])
        await settings_repo.set(name_key, folder["name"])
        await settings_repo.set(created_key, "true")
        return FolderConfig(folder_id=folder["id"], folder_name=folder["name"], created_by_app=True)

    @staticmethod
    async def _get_config(
        settings_repo: SettingsRepository, *, id_key: str, name_key: str, created_key: str
    ) -> FolderConfig | None:
        folder_id = await settings_repo.get(id_key)
        if folder_id is None:
            return None
        folder_name = await settings_repo.get(name_key) or ""
        created_by_app = (await settings_repo.get(created_key)) == "true"
        return FolderConfig(folder_id=folder_id, folder_name=folder_name, created_by_app=created_by_app)

    async def select_existing_folder(
        self, folder_id: str, settings_repo: SettingsRepository
    ) -> FolderConfig:
        return await self._select_existing(
            folder_id,
            settings_repo,
            id_key=DRIVE_INBOX_FOLDER_ID,
            name_key=DRIVE_INBOX_FOLDER_NAME,
            created_key=DRIVE_INBOX_FOLDER_CREATED_BY_APP,
        )

    async def create_and_select_inbox_folder(
        self, name: str, settings_repo: SettingsRepository
    ) -> FolderConfig:
        return await self._create_and_select(
            name,
            settings_repo,
            id_key=DRIVE_INBOX_FOLDER_ID,
            name_key=DRIVE_INBOX_FOLDER_NAME,
            created_key=DRIVE_INBOX_FOLDER_CREATED_BY_APP,
        )

    @staticmethod
    async def get_inbox_folder_config(settings_repo: SettingsRepository) -> FolderConfig | None:
        return await DriveService._get_config(
            settings_repo,
            id_key=DRIVE_INBOX_FOLDER_ID,
            name_key=DRIVE_INBOX_FOLDER_NAME,
            created_key=DRIVE_INBOX_FOLDER_CREATED_BY_APP,
        )

    async def select_existing_library_folder(
        self, folder_id: str, settings_repo: SettingsRepository
    ) -> FolderConfig:
        return await self._select_existing(
            folder_id,
            settings_repo,
            id_key=DRIVE_LIBRARY_FOLDER_ID,
            name_key=DRIVE_LIBRARY_FOLDER_NAME,
            created_key=DRIVE_LIBRARY_FOLDER_CREATED_BY_APP,
        )

    async def create_and_select_library_folder(
        self, name: str, settings_repo: SettingsRepository
    ) -> FolderConfig:
        return await self._create_and_select(
            name,
            settings_repo,
            id_key=DRIVE_LIBRARY_FOLDER_ID,
            name_key=DRIVE_LIBRARY_FOLDER_NAME,
            created_key=DRIVE_LIBRARY_FOLDER_CREATED_BY_APP,
        )

    @staticmethod
    async def get_library_folder_config(settings_repo: SettingsRepository) -> FolderConfig | None:
        return await DriveService._get_config(
            settings_repo,
            id_key=DRIVE_LIBRARY_FOLDER_ID,
            name_key=DRIVE_LIBRARY_FOLDER_NAME,
            created_key=DRIVE_LIBRARY_FOLDER_CREATED_BY_APP,
        )
