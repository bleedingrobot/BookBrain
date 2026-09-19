import pytest

from app.providers.acquisition.exceptions import AcquisitionError
from app.services import inbox_upload_service
from tests.epub_fixtures import build_epub


class _FakeDriveProvider:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, bytes, str, str]] = []

    def upload_new_file(self, *, name: str, data: bytes, parent_id: str, mime_type: str) -> dict:
        self.uploads.append((name, data, parent_id, mime_type))
        return {"id": f"drive-{name}", "name": name, "parents": [parent_id]}


async def test_uploads_a_valid_epub(tmp_path):
    path = tmp_path / "book.epub"
    data = build_epub(title="The Final Empire")
    path.write_bytes(data)
    provider = _FakeDriveProvider()

    result = await inbox_upload_service.upload_local_file_to_inbox(path, "book.epub", provider, "inbox-1")

    assert result["filename"] == "book.epub"
    assert result["drive_file_id"] == "drive-book.epub"
    assert result["size_bytes"] == len(data)
    assert provider.uploads == [("book.epub", data, "inbox-1", "application/epub+zip")]
    assert path.exists()  # never deletes the source file itself


async def test_rejects_empty_file(tmp_path):
    path = tmp_path / "book.epub"
    path.write_bytes(b"")
    provider = _FakeDriveProvider()

    with pytest.raises(AcquisitionError, match="empty"):
        await inbox_upload_service.upload_local_file_to_inbox(path, "book.epub", provider, "inbox-1")
    assert provider.uploads == []


async def test_rejects_implausibly_large_file(tmp_path, monkeypatch):
    monkeypatch.setattr(inbox_upload_service, "_MAX_BYTES", 10)
    path = tmp_path / "book.epub"
    path.write_bytes(build_epub())
    provider = _FakeDriveProvider()

    with pytest.raises(AcquisitionError, match="implausibly large"):
        await inbox_upload_service.upload_local_file_to_inbox(path, "book.epub", provider, "inbox-1")
    assert provider.uploads == []


async def test_rejects_unsupported_format(tmp_path):
    path = tmp_path / "book.pdf"
    path.write_bytes(b"%PDF-1.4 not really a pdf but non-empty")
    provider = _FakeDriveProvider()

    with pytest.raises(AcquisitionError, match="isn't a format"):
        await inbox_upload_service.upload_local_file_to_inbox(path, "book.pdf", provider, "inbox-1")
    assert provider.uploads == []


async def test_rejects_a_file_with_epub_extension_but_bad_zip_contents(tmp_path):
    path = tmp_path / "book.epub"
    path.write_bytes(b"this is not a zip file")
    provider = _FakeDriveProvider()

    with pytest.raises(AcquisitionError, match="corrupt or not actually an EPUB"):
        await inbox_upload_service.upload_local_file_to_inbox(path, "book.epub", provider, "inbox-1")
    assert provider.uploads == []


async def test_sanitises_a_path_traversal_filename(tmp_path):
    path = tmp_path / "book.epub"
    path.write_bytes(build_epub())
    provider = _FakeDriveProvider()

    result = await inbox_upload_service.upload_local_file_to_inbox(
        path, "../../etc/pas:swd.epub", provider, "inbox-1"
    )
    assert result["filename"] == "pas_swd.epub"
