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

    def list(self, **kwargs) -> _FakeExecutable:
        self.list_calls.append(kwargs)
        return _FakeExecutable(self._list_pages.pop(0))

    def create(self, **kwargs) -> _FakeExecutable:
        self.create_calls.append(kwargs)
        return _FakeExecutable({"id": "new-folder-id", "name": kwargs["body"]["name"]})

    def get(self, **kwargs) -> _FakeExecutable:
        return _FakeExecutable({"id": kwargs["fileId"], "name": "Some Folder"})


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
