from pathlib import Path

import pytest

from app.services import acquire_service
from app.services.openbooks_service import OpenBooksError
from tests.epub_fixtures import build_epub


class _FakeProvider:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, bytes, str, str]] = []

    def upload_new_file(self, *, name: str, data: bytes, parent_id: str, mime_type: str) -> dict:
        self.uploads.append((name, data, parent_id, mime_type))
        return {"id": f"drive-{name}", "name": name, "parents": [parent_id]}


@pytest.fixture
def fake_download(monkeypatch):
    def _install(path: Path):
        async def _fake(full: str) -> Path:
            return path

        monkeypatch.setattr(acquire_service, "download", _fake)

    return _install


async def test_acquire_uploads_epub_and_cleans_local(tmp_path, fake_download):
    local = tmp_path / "Brandon Sanderson - The Final Empire.epub"
    local.write_bytes(build_epub(title="The Final Empire"))
    fake_download(local)
    provider = _FakeProvider()

    result = await acquire_service.acquire_to_inbox(
        "!Bsk x.epub", "Brandon Sanderson - The Final Empire.epub", provider, "inbox-1"
    )

    assert result["filename"] == "Brandon Sanderson - The Final Empire.epub"
    assert result["drive_file_id"] == "drive-Brandon Sanderson - The Final Empire.epub"
    assert len(provider.uploads) == 1
    assert provider.uploads[0][2] == "inbox-1"
    assert not local.exists()  # cleaned up after a successful upload


async def test_acquire_rejects_unsupported_format(tmp_path, fake_download):
    local = tmp_path / "book.pdf"
    local.write_bytes(b"%PDF-1.4 ...")
    fake_download(local)
    provider = _FakeProvider()

    with pytest.raises(OpenBooksError, match="isn't a format"):
        await acquire_service.acquire_to_inbox("!Bsk x.pdf", "book.pdf", provider, "inbox-1")
    assert provider.uploads == []
    assert local.exists()  # left in place on failure


async def test_acquire_rejects_corrupt_epub(tmp_path, fake_download):
    local = tmp_path / "book.epub"
    local.write_bytes(b"this is not a zip file")
    fake_download(local)
    provider = _FakeProvider()

    with pytest.raises(OpenBooksError, match="corrupt or not actually an EPUB"):
        await acquire_service.acquire_to_inbox("!Bsk x.epub", "book.epub", provider, "inbox-1")
    assert provider.uploads == []


async def test_acquire_sanitises_filename(tmp_path, fake_download):
    local = tmp_path / "dl.epub"
    local.write_bytes(build_epub())
    fake_download(local)
    provider = _FakeProvider()

    result = await acquire_service.acquire_to_inbox(
        "!Bsk x.epub", "../../etc/pas:swd.epub", provider, "inbox-1"
    )
    assert result["filename"] == "pas_swd.epub"
    assert provider.uploads[0][0] == "pas_swd.epub"
