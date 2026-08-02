from googleapiclient.discovery import Resource

from app.providers.drive.classify import is_supported_ebook
from app.providers.drive.client import FOLDER_MIME_TYPE


class DriveProvider:
    """Thin wrapper over the Drive v3 API. Returns raw API dicts — mapping to
    DTOs happens in the service layer, per the layering rule in SPEC.md §4."""

    def __init__(self, service: Resource) -> None:
        self._service = service

    def list_folders(self, parent_id: str | None) -> list[dict]:
        parent = parent_id or "root"
        query = f"'{parent}' in parents and mimeType='{FOLDER_MIME_TYPE}' and trashed=false"
        result = (
            self._service.files()
            .list(q=query, fields="files(id,name)", pageSize=200)
            .execute()
        )
        return result.get("files", [])

    def create_folder(self, name: str, parent_id: str | None = None) -> dict:
        body = {"name": name, "mimeType": FOLDER_MIME_TYPE}
        if parent_id:
            body["parents"] = [parent_id]
        return self._service.files().create(body=body, fields="id,name").execute()

    def get_folder(self, folder_id: str) -> dict:
        return (
            self._service.files()
            .get(fileId=folder_id, fields="id,name,driveId,trashed")
            .execute()
        )

    def download_file(self, file_id: str) -> bytes:
        return self._service.files().get_media(fileId=file_id).execute()

    def move_and_rename(
        self, file_id: str, *, old_parent_id: str | None, new_parent_id: str, new_name: str
    ) -> dict:
        return (
            self._service.files()
            .update(
                fileId=file_id,
                addParents=new_parent_id,
                removeParents=old_parent_id or "",
                body={"name": new_name},
                fields="id,name,parents",
            )
            .execute()
        )

    def list_files_in_folder(self, folder_id: str) -> list[dict]:
        """Every non-folder file directly in this folder, any type — the
        scan uses this (not list_epub_files) so it can find and remove
        clutter that isn't a supported ebook format."""
        query = f"'{folder_id}' in parents and trashed=false and mimeType!='{FOLDER_MIME_TYPE}'"
        files: list[dict] = []
        page_token: str | None = None
        while True:
            result = (
                self._service.files()
                .list(
                    q=query,
                    fields="nextPageToken, files(id,name,size,parents,trashed)",
                    pageSize=200,
                    pageToken=page_token,
                )
                .execute()
            )
            files.extend(result.get("files", []))
            page_token = result.get("nextPageToken")
            if not page_token:
                break
        return files

    def list_epub_files(self, folder_id: str) -> list[dict]:
        """Supported ebooks (.epub, .kpub) directly in this folder — filtered
        by filename extension rather than Drive's mimeType, since Drive has
        no reliable mimeType for .kpub."""
        return [f for f in self.list_files_in_folder(folder_id) if is_supported_ebook(f["name"])]

    def list_epub_files_recursive(self, root_folder_id: str) -> list[dict]:
        """Same as list_epub_files, but walks every subfolder too — for
        rebuilding from an organized library tree (Author/Series nesting),
        where files aren't direct children of the root."""
        files: list[dict] = []
        stack = [root_folder_id]
        while stack:
            current = stack.pop()
            files.extend(self.list_epub_files(current))
            stack.extend(f["id"] for f in self.list_folders(current))
        return files

    def trash_file(self, file_id: str) -> dict:
        return (
            self._service.files()
            .update(fileId=file_id, body={"trashed": True}, fields="id,trashed")
            .execute()
        )
