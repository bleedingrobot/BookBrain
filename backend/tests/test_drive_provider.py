from app.providers.drive.provider import DriveProvider


class _FakeExecutable:
    def __init__(self, data: dict) -> None:
        self._data = data

    def execute(self) -> dict:
        return self._data


class _FakeFiles:
    def __init__(self, list_pages: list[dict]) -> None:
        self._list_pages = list(list_pages)
        self.list_calls: list[dict] = []
        self.create_calls: list[dict] = []
        self.update_calls: list[dict] = []

    def list(self, **kwargs) -> _FakeExecutable:
        self.list_calls.append(kwargs)
        return _FakeExecutable(self._list_pages.pop(0))

    def create(self, **kwargs) -> _FakeExecutable:
        self.create_calls.append(kwargs)
        return _FakeExecutable({"id": "new-folder-id", "name": kwargs["body"]["name"]})

    def get(self, **kwargs) -> _FakeExecutable:
        return _FakeExecutable({"id": kwargs["fileId"], "name": "Some Folder"})

    def update(self, **kwargs) -> _FakeExecutable:
        self.update_calls.append(kwargs)
        return _FakeExecutable({"id": kwargs["fileId"], **kwargs.get("body", {})})


class _FakeService:
    def __init__(self, files: _FakeFiles) -> None:
        self._files = files

    def files(self) -> _FakeFiles:
        return self._files


def test_list_epub_files_paginates() -> None:
    files = _FakeFiles(
        [
            {"files": [{"id": "1", "name": "a.epub", "parents": ["p"]}], "nextPageToken": "tok"},
            {"files": [{"id": "2", "name": "b.epub", "parents": ["p"]}]},
        ]
    )
    provider = DriveProvider(_FakeService(files))

    results = provider.list_epub_files("p")

    assert [f["id"] for f in results] == ["1", "2"]
    assert len(files.list_calls) == 2
    assert files.list_calls[1]["pageToken"] == "tok"


def test_list_epub_files_filters_by_extension_not_mimetype() -> None:
    files = _FakeFiles(
        [
            {
                "files": [
                    {"id": "1", "name": "a.epub", "parents": ["p"]},
                    {"id": "2", "name": "b.kpub", "parents": ["p"]},
                    {"id": "3", "name": "cover.jpg", "parents": ["p"]},
                    {"id": "4", "name": "notes.txt", "parents": ["p"]},
                ]
            }
        ]
    )
    provider = DriveProvider(_FakeService(files))

    results = provider.list_epub_files("p")

    assert {f["id"] for f in results} == {"1", "2"}


def test_list_files_in_folder_returns_every_file_type() -> None:
    files = _FakeFiles(
        [
            {
                "files": [
                    {"id": "1", "name": "a.epub", "parents": ["p"]},
                    {"id": "2", "name": "cover.jpg", "parents": ["p"]},
                ]
            }
        ]
    )
    provider = DriveProvider(_FakeService(files))

    results = provider.list_files_in_folder("p")

    assert {f["id"] for f in results} == {"1", "2"}


def test_create_folder_sets_parent_when_given() -> None:
    files = _FakeFiles([])
    provider = DriveProvider(_FakeService(files))

    provider.create_folder("Inbox", parent_id="root-id")

    assert files.create_calls[0]["body"] == {
        "name": "Inbox",
        "mimeType": "application/vnd.google-apps.folder",
        "parents": ["root-id"],
    }


def test_create_folder_without_parent_omits_parents_key() -> None:
    files = _FakeFiles([])
    provider = DriveProvider(_FakeService(files))

    provider.create_folder("Inbox")

    assert "parents" not in files.create_calls[0]["body"]


def test_trash_file_sets_trashed_true() -> None:
    files = _FakeFiles([])
    provider = DriveProvider(_FakeService(files))

    result = provider.trash_file("f1")

    assert files.update_calls[0] == {"fileId": "f1", "body": {"trashed": True}, "fields": "id,trashed"}
    assert result == {"id": "f1", "trashed": True}


def test_update_file_content_uploads_new_data_and_name() -> None:
    files = _FakeFiles([])
    provider = DriveProvider(_FakeService(files))

    result = provider.update_file_content("f1", new_name="book.epub", data=b"epub bytes")

    call = files.update_calls[0]
    assert call["fileId"] == "f1"
    assert call["body"] == {"name": "book.epub"}
    assert call["media_body"] is not None
    assert result == {"id": "f1", "name": "book.epub"}


def test_upload_new_file_creates_file_with_media_and_parent() -> None:
    files = _FakeFiles([])
    provider = DriveProvider(_FakeService(files))

    result = provider.upload_new_file(
        name="book.epub", data=b"epub bytes", parent_id="inbox-id", mime_type="application/epub+zip"
    )

    call = files.create_calls[0]
    assert call["body"] == {"name": "book.epub", "parents": ["inbox-id"]}
    assert call["media_body"] is not None
    assert result == {"id": "new-folder-id", "name": "book.epub"}


def test_list_folders_paginates() -> None:
    # Regression: a parent with more than one page of children (e.g. a
    # library root with hundreds of author folders) was silently truncated
    # to the first page, which could make FolderPathCache think an existing
    # folder further down the list didn't exist and create a duplicate.
    files = _FakeFiles(
        [
            {"files": [{"id": "1", "name": "Author A"}], "nextPageToken": "tok"},
            {"files": [{"id": "2", "name": "Author B"}]},
        ]
    )
    provider = DriveProvider(_FakeService(files))

    results = provider.list_folders("p")

    assert [f["id"] for f in results] == ["1", "2"]
    assert len(files.list_calls) == 2
    assert files.list_calls[1]["pageToken"] == "tok"


def test_list_folders_escapes_single_quote_in_parent_id() -> None:
    files = _FakeFiles([{"files": []}])
    provider = DriveProvider(_FakeService(files))

    provider.list_folders("weird'id")

    assert files.list_calls[0]["q"] == (
        "'weird\\'id' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    )


def test_list_files_in_folder_escapes_single_quote_in_folder_id() -> None:
    files = _FakeFiles([{"files": []}])
    provider = DriveProvider(_FakeService(files))

    provider.list_files_in_folder("weird'id")

    assert files.list_calls[0]["q"] == (
        "'weird\\'id' in parents and trashed=false and mimeType!='application/vnd.google-apps.folder'"
    )


def test_create_spreadsheet_from_csv_sets_sheet_mimetype_and_parent() -> None:
    files = _FakeFiles([])
    provider = DriveProvider(_FakeService(files))

    provider.create_spreadsheet_from_csv(name="Export", csv_bytes=b"a,b\n1,2", parent_id="lib-id")

    call = files.create_calls[0]
    assert call["body"] == {
        "name": "Export",
        "mimeType": "application/vnd.google-apps.spreadsheet",
        "parents": ["lib-id"],
    }
    assert call["media_body"] is not None
    assert call["fields"] == "id,name,webViewLink"


def test_create_spreadsheet_from_csv_without_parent_omits_parents_key() -> None:
    files = _FakeFiles([])
    provider = DriveProvider(_FakeService(files))

    provider.create_spreadsheet_from_csv(name="Export", csv_bytes=b"a,b\n1,2")

    assert "parents" not in files.create_calls[0]["body"]


def test_list_epub_files_recursive_walks_subfolders() -> None:
    # root/: a.epub, subfolder "Author A"/; "Author A"/: b.epub, no subfolders
    files = _FakeFiles(
        [
            {"files": [{"id": "a", "name": "a.epub", "parents": ["root"]}]},
            {"files": [{"id": "authorA", "name": "Author A"}]},
            {"files": [{"id": "b", "name": "b.epub", "parents": ["authorA"]}]},
            {"files": []},
        ]
    )
    provider = DriveProvider(_FakeService(files))

    results = provider.list_epub_files_recursive("root")

    assert {f["id"] for f in results} == {"a", "b"}
