import io
import zipfile

from PIL import Image

from app.providers.epub.parser import extract_cover
from app.services.cover_service import _thumbnail

_LIMITS = {"max_entry_bytes": 10_000_000, "max_total_bytes": 50_000_000, "max_entries": 1000}


def _png(color: str = "red", size: tuple[int, int] = (600, 900)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="PNG")
    return out.getvalue()


def _epub(opf: str, files: dict[str, bytes]) -> bytes:
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
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


_OPF_EPUB3 = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>T</dc:title></metadata>
  <manifest>
    <item id="cov" href="cover.png" media-type="image/png" properties="cover-image"/>
    <item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="c1"/></spine>
</package>"""

_OPF_EPUB2 = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>T</dc:title><meta name="cover" content="cov"/>
  </metadata>
  <manifest>
    <item id="cov" href="images/big-cover.jpeg" media-type="image/jpeg"/>
    <item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="c1"/></spine>
</package>"""

_OPF_NOCOVER = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>T</dc:title></metadata>
  <manifest><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/></manifest>
  <spine><itemref idref="c1"/></spine>
</package>"""


def test_extract_cover_epub3_properties() -> None:
    epub = _epub(_OPF_EPUB3, {"OEBPS/cover.png": _png(), "OEBPS/c1.xhtml": b"<html/>"})
    raw = extract_cover(epub, **_LIMITS)
    assert raw is not None
    assert Image.open(io.BytesIO(raw)).size == (600, 900)


def test_extract_cover_epub2_meta() -> None:
    jpg = io.BytesIO()
    Image.new("RGB", (400, 640), "blue").save(jpg, format="JPEG")
    epub = _epub(_OPF_EPUB2, {"OEBPS/images/big-cover.jpeg": jpg.getvalue(), "OEBPS/c1.xhtml": b"x"})
    raw = extract_cover(epub, **_LIMITS)
    assert raw is not None
    assert Image.open(io.BytesIO(raw)).size == (400, 640)


def test_extract_cover_none_when_absent() -> None:
    epub = _epub(_OPF_NOCOVER, {"OEBPS/c1.xhtml": b"x"})
    assert extract_cover(epub, **_LIMITS) is None


def test_extract_cover_survives_garbage() -> None:
    assert extract_cover(b"not a zip", **_LIMITS) is None


def test_thumbnail_shrinks_and_reencodes() -> None:
    result = _thumbnail(_png(size=(1200, 1800)))
    assert result is not None
    thumb, phash = result
    img = Image.open(io.BytesIO(thumb))
    assert img.format == "JPEG"
    assert max(img.size) <= 320
    assert len(phash) == 16 and int(phash, 16) >= 0


def test_thumbnail_none_on_bad_bytes() -> None:
    assert _thumbnail(b"nope") is None


class _FakeCoverProvider:
    def __init__(self, epub_bytes: bytes) -> None:
        self._epub = epub_bytes
        self.uploaded: list[tuple[str, int]] = []

    def download_file(self, _id: str) -> bytes:
        return self._epub

    def upload_new_file(self, *, name: str, data: bytes, parent_id: str, mime_type: str) -> dict:
        self.uploaded.append((name, len(data)))
        return {"id": name, "name": name}


def test_make_one_writes_a_jpg_when_the_epub_has_a_cover() -> None:
    from app.services.cover_service import _make_one

    epub = _epub(_OPF_EPUB3, {"OEBPS/cover.png": _png(), "OEBPS/c1.xhtml": b"x"})
    p = _FakeCoverProvider(epub)
    status, phash = _make_one(p, "covers-folder", "drive-1", "book.epub")
    assert status == "done"
    assert len(phash) == 16
    assert p.uploaded == [("drive-1.jpg", p.uploaded[0][1])]
    assert p.uploaded[0][1] > 0


def test_make_one_writes_a_nocover_marker_when_there_is_no_cover() -> None:
    from app.services.cover_service import _make_one

    epub = _epub(_OPF_NOCOVER, {"OEBPS/c1.xhtml": b"x"})
    p = _FakeCoverProvider(epub)
    assert _make_one(p, "covers-folder", "drive-2", "book.epub") == ("nocover", None)
    assert p.uploaded == [("drive-2.nocover", 0)]


def test_make_one_pulls_first_page_as_cover_for_a_cbz() -> None:
    import io
    import zipfile

    from app.services.cover_service import _make_one

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("000.jpg", _png("blue"))
        zf.writestr("001.jpg", _png("green"))
    p = _FakeCoverProvider(buf.getvalue())
    status, phash = _make_one(p, "covers-folder", "drive-3", "Batman 001.cbz")
    assert status == "done"
    assert len(phash) == 16
    assert p.uploaded == [("drive-3.jpg", p.uploaded[0][1])]
    assert p.uploaded[0][1] > 0
