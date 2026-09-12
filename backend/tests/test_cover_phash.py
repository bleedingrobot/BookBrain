import io
import zipfile

import pytest
from PIL import Image
from sqlalchemy import select

import app.services.cover_service as cs
from app.data.models import Author, Book, File, FileStatus


@pytest.fixture(autouse=True)
def _route_db(db_session, monkeypatch):
    class _CM:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(cs, "async_session_factory", lambda: _CM())


def _png_bytes(color: str, size: tuple[int, int] = (300, 450)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="PNG")
    return out.getvalue()


def _epub_with_cover(color: str) -> bytes:
    opf = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>T</dc:title></metadata>
  <manifest>
    <item id="cov" href="cover.png" media-type="image/png" properties="cover-image"/>
    <item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="c1"/></spine>
</package>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"'
            ' version="1.0"><rootfiles><rootfile full-path="OEBPS/content.opf"'
            ' media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/cover.png", _png_bytes(color))
        zf.writestr("OEBPS/c1.xhtml", b"<html/>")
    return buf.getvalue()


def _epub_without_cover() -> bytes:
    opf = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>T</dc:title></metadata>
  <manifest><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/></manifest>
  <spine><itemref idref="c1"/></spine>
</package>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"'
            ' version="1.0"><rootfiles><rootfile full-path="OEBPS/content.opf"'
            ' media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/c1.xhtml", b"<html/>")
    return buf.getvalue()


_EXISTING_JPG = cs._thumbnail(_png_bytes("navy"))[0]


class _FakeProvider:
    uploaded: list[str] = []

    def __init__(self, *args) -> None:
        pass

    def list_folders(self, parent_id):
        return [{"id": "covers-folder", "name": cs.COVERS_FOLDER_NAME}]

    def create_folder(self, name, parent_id=None):
        return {"id": "covers-folder"}

    def list_files_in_folder(self, folder_id):
        # "f2" already has a rendered thumbnail; "f1" has none yet.
        return [{"id": "f2-jpg-id", "name": "f2.jpg"}]

    def download_file(self, file_id):
        if file_id == "f2-jpg-id":
            return _EXISTING_JPG
        if file_id == "f1":
            return _epub_with_cover("darkred")
        raise AssertionError(f"unexpected download {file_id}")

    def upload_new_file(self, *, name, data, parent_id, mime_type):
        _FakeProvider.uploaded.append(name)
        return {"id": name, "name": name}


async def test_regenerate_covers_writes_and_backfills_phash(db_session, monkeypatch) -> None:
    _FakeProvider.uploaded = []
    monkeypatch.setattr(cs, "build_drive_service", lambda creds: None)
    monkeypatch.setattr(cs, "DriveProvider", _FakeProvider)

    author = Author(name="A")
    db_session.add(author)
    await db_session.flush()
    b1 = Book(canonical_title="One", author_id=author.id)
    b2 = Book(canonical_title="Two", author_id=author.id)
    db_session.add_all([b1, b2])
    await db_session.flush()
    db_session.add_all(
        [
            File(
                drive_file_id="f1",
                filename="f1.epub",
                sha256="f1" * 8,
                size_bytes=1,
                status=FileStatus.organised,
                book_id=b1.id,
            ),
            File(
                drive_file_id="f2",
                filename="f2.epub",
                sha256="f2" * 8,
                size_bytes=1,
                status=FileStatus.organised,
                book_id=b2.id,
                cover_phash=None,
            ),
        ]
    )
    await db_session.commit()

    counts = await cs.regenerate_covers(object(), "library-root")

    assert counts["done"] == 1  # f1 rendered from scratch
    assert counts["rehashed"] == 1  # f2 re-hashed from its existing .jpg
    assert _FakeProvider.uploaded == ["f1.jpg"]  # f2's cover was not re-uploaded

    rows = {
        r[0]: r[1]
        for r in (await db_session.execute(select(File.drive_file_id, File.cover_phash))).all()
    }
    assert rows["f1"] is not None and len(rows["f1"]) == 16
    assert rows["f2"] is not None and len(rows["f2"]) == 16


# --- regenerate_covers orchestration edge paths (REVIEW-2026-09-08 F5) -------

async def _seed_organised(db_session, *drive_ids: str) -> None:
    author = Author(name="A")
    db_session.add(author)
    await db_session.flush()
    for did in drive_ids:
        book = Book(canonical_title=did, author_id=author.id)
        db_session.add(book)
        await db_session.flush()
        db_session.add(
            File(
                drive_file_id=did,
                filename=f"{did}.epub",
                sha256=did.ljust(16, "x")[:16],
                size_bytes=1,
                status=FileStatus.organised,
                book_id=book.id,
            )
        )
    await db_session.commit()


class _EmptyCoversProvider:
    """No covers/ folder yet; one book has a real cover, one is coverless, one
    fails to download."""

    created: list[tuple[str, str | None]] = []
    uploaded: list[str] = []

    def __init__(self, *args) -> None:
        pass

    def list_folders(self, parent_id):
        return []  # covers/ doesn't exist yet

    def create_folder(self, name, parent_id=None):
        _EmptyCoversProvider.created.append((name, parent_id))
        return {"id": "new-covers-folder", "name": name}

    def list_files_in_folder(self, folder_id):
        return []

    def download_file(self, file_id):
        if file_id == "boom":
            raise RuntimeError("drive 500")
        if file_id.startswith("hascover"):
            return _epub_with_cover("teal")
        return _epub_without_cover()

    def upload_new_file(self, *, name, data, parent_id, mime_type):
        _EmptyCoversProvider.uploaded.append(name)
        return {"id": name}


async def test_regenerate_covers_creates_folder_marks_nocover_and_isolates_failures(
    db_session, monkeypatch
) -> None:
    _EmptyCoversProvider.created = []
    _EmptyCoversProvider.uploaded = []
    monkeypatch.setattr(cs, "build_drive_service", lambda creds: None)
    monkeypatch.setattr(cs, "DriveProvider", _EmptyCoversProvider)

    await _seed_organised(db_session, "hascover", "nocov", "boom")

    counts = await cs.regenerate_covers(object(), "library-root")

    assert _EmptyCoversProvider.created == [(cs.COVERS_FOLDER_NAME, "library-root")]
    assert counts["done"] == 1
    assert counts["nocover"] == 1
    assert counts["failed"] == 1  # "boom" failed but didn't sink the pass
    assert "hascover.jpg" in _EmptyCoversProvider.uploaded
    assert "nocov.nocover" in _EmptyCoversProvider.uploaded


async def test_regenerate_covers_respects_the_limit(db_session, monkeypatch) -> None:
    monkeypatch.setattr(cs, "build_drive_service", lambda creds: None)
    monkeypatch.setattr(cs, "DriveProvider", _EmptyCoversProvider)
    _EmptyCoversProvider.uploaded = []

    await _seed_organised(db_session, "hascover", "hascover2", "hascover3")

    counts = await cs.regenerate_covers(object(), "library-root", limit=2)

    assert counts["done"] + counts["nocover"] + counts["failed"] == 2
    assert counts["remaining"] == 1
