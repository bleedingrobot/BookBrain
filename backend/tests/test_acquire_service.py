from pathlib import Path

import pytest

from app.providers.acquisition.exceptions import AcquisitionError
from app.services import acquire_service
from tests.epub_fixtures import build_epub


class _FakeProvider:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, bytes, str, str]] = []

    def upload_new_file(self, *, name: str, data: bytes, parent_id: str, mime_type: str) -> dict:
        self.uploads.append((name, data, parent_id, mime_type))
        return {"id": f"drive-{name}", "name": name, "parents": [parent_id]}


class _FakeAcquisitionProvider:
    """Stand-in for an AcquisitionProvider — returns a fixed local path from download()."""

    name = "fake"

    def __init__(self, path: Path) -> None:
        self._path = path

    def is_enabled(self) -> bool:
        return True

    async def search(self, query: str):
        return []

    async def download(self, handle: str) -> Path:
        return self._path


async def test_acquire_uploads_epub_and_cleans_local(tmp_path):
    local = tmp_path / "Brandon Sanderson - The Final Empire.epub"
    local.write_bytes(build_epub(title="The Final Empire"))
    provider = _FakeProvider()

    result = await acquire_service.acquire_to_inbox(
        _FakeAcquisitionProvider(local),
        "!Bsk x.epub",
        "Brandon Sanderson - The Final Empire.epub",
        provider,
        "inbox-1",
    )

    assert result["filename"] == "Brandon Sanderson - The Final Empire.epub"
    assert result["drive_file_id"] == "drive-Brandon Sanderson - The Final Empire.epub"
    assert len(provider.uploads) == 1
    assert provider.uploads[0][2] == "inbox-1"
    assert not local.exists()  # cleaned up after a successful upload


async def test_acquire_rejects_unsupported_format(tmp_path):
    local = tmp_path / "book.pdf"
    local.write_bytes(b"%PDF-1.4 ...")
    provider = _FakeProvider()

    with pytest.raises(AcquisitionError, match="isn't a format"):
        await acquire_service.acquire_to_inbox(
            _FakeAcquisitionProvider(local), "!Bsk x.pdf", "book.pdf", provider, "inbox-1"
        )
    assert provider.uploads == []
    assert local.exists()  # left in place on failure


async def test_acquire_rejects_corrupt_epub(tmp_path):
    local = tmp_path / "book.epub"
    local.write_bytes(b"this is not a zip file")
    provider = _FakeProvider()

    with pytest.raises(AcquisitionError, match="corrupt or not actually an EPUB"):
        await acquire_service.acquire_to_inbox(
            _FakeAcquisitionProvider(local), "!Bsk x.epub", "book.epub", provider, "inbox-1"
        )
    assert provider.uploads == []


async def test_acquire_sanitises_filename(tmp_path):
    local = tmp_path / "dl.epub"
    local.write_bytes(build_epub())
    provider = _FakeProvider()

    result = await acquire_service.acquire_to_inbox(
        _FakeAcquisitionProvider(local),
        "!Bsk x.epub",
        "../../etc/pas:swd.epub",
        provider,
        "inbox-1",
    )
    assert result["filename"] == "pas_swd.epub"
    assert provider.uploads[0][0] == "pas_swd.epub"
