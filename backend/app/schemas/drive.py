from pydantic import BaseModel


class DriveFolder(BaseModel):
    id: str
    name: str


class DriveFileListing(BaseModel):
    id: str
    name: str
    size_bytes: int
    status_reason: str | None  # None | "multi_parent" | "no_parent"


class FolderConfig(BaseModel):
    folder_id: str
    folder_name: str
    created_by_app: bool


class SelectFolderRequest(BaseModel):
    folder_id: str


class CreateFolderRequest(BaseModel):
    name: str
    parent_id: str | None = None
