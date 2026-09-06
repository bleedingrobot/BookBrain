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
