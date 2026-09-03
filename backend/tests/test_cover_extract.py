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
    thumb = _thumbnail(_png(size=(1200, 1800)))
    assert thumb is not None
    img = Image.open(io.BytesIO(thumb))
    assert img.format == "JPEG"
    assert max(img.size) <= 320


def test_thumbnail_none_on_bad_bytes() -> None:
    assert _thumbnail(b"nope") is None
