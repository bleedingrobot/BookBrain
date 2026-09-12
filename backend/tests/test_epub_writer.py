import io
import os
import tempfile
import zipfile

import pytest
from ebooklib import epub as ebooklib_epub
from PIL import Image

from app.providers.epub.errors import EpubWriteError
from app.providers.epub.parser import extract_cover, parse_epub
from app.providers.epub.writer import EpubMetadata, write_metadata
from tests.epub_fixtures import build_epub

_LIMITS = {"max_entry_bytes": 10_000_000, "max_total_bytes": 50_000_000, "max_entries": 1000}


def _parse(data: bytes):
    return parse_epub(data, **_LIMITS)


def _png(color: str = "red", size: tuple[int, int] = (400, 600)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="PNG")
    return out.getvalue()


def _jpg(color: str = "blue", size: tuple[int, int] = (400, 600)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="JPEG")
    return out.getvalue()


def _opens_in_ebooklib(data: bytes) -> ebooklib_epub.EpubBook:
    fd, path = tempfile.mkstemp(suffix=".epub")
    os.write(fd, data)
    os.close(fd)
    try:
        return ebooklib_epub.read_epub(path)
    finally:
        try:
            os.unlink(path)
        except PermissionError:
            pass  # ebooklib can keep the zip handle open on Windows


# --------------------------------------------------------------------------
# round-trip / structure
# --------------------------------------------------------------------------


def test_round_trips_title_author_series() -> None:
    original = build_epub(
        title="queen of shadows - unknown", authors=("unknown",), series="Wrong", series_number=9
    )
    out = write_metadata(
        original,
        EpubMetadata(title="Queen of Shadows", author="Sarah J. Maas", series="Throne of Glass", series_number=4.0),
    )

    ev = _parse(out)
    assert ev.title == "Queen of Shadows"
    assert ev.authors == ["Sarah J. Maas"]
    assert ev.series == "Throne of Glass"
    assert ev.series_number == 4.0

    book = _opens_in_ebooklib(out)
    assert book.get_metadata("DC", "title")[0][0] == "Queen of Shadows"
    assert book.get_metadata("DC", "creator")[0][0] == "Sarah J. Maas"


def test_mimetype_stays_first_and_stored() -> None:
    out = write_metadata(build_epub(), EpubMetadata(title="T", author="A"))
    zf = zipfile.ZipFile(io.BytesIO(out))
    assert zf.namelist()[0] == "mimetype"
    assert zf.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
    assert zf.read("mimetype") == b"application/epub+zip"


def test_content_entries_are_byte_identical() -> None:
    original = build_epub(chapter_text="<html><body><p>Untouched prose.</p></body></html>")
    out = write_metadata(original, EpubMetadata(title="New", author="New"))
    before = zipfile.ZipFile(io.BytesIO(original))
    after = zipfile.ZipFile(io.BytesIO(out))
    for name in before.namelist():
        if name == "OEBPS/content.opf":
            continue
        assert after.read(name) == before.read(name), name


def test_writes_both_epub3_and_legacy_series_tags() -> None:
    out = write_metadata(
        build_epub(), EpubMetadata(title="T", author="A", series="Mistborn", series_number=2.0)
    )
    opf = zipfile.ZipFile(io.BytesIO(out)).read("OEBPS/content.opf").decode()
    assert 'name="calibre:series" content="Mistborn"' in opf
    assert 'name="calibre:series_index" content="2"' in opf
    assert 'property="belongs-to-collection"' in opf
    assert ">Mistborn<" in opf
    assert 'property="collection-type"' in opf


def test_existing_wrong_calibre_series_is_replaced_not_duplicated() -> None:
    original = build_epub(series="Old Wrong Series", series_number=7)
    out = write_metadata(
        original, EpubMetadata(title="T", author="A", series="Right", series_number=1.0)
    )
    opf = zipfile.ZipFile(io.BytesIO(out)).read("OEBPS/content.opf").decode()
    assert opf.count('name="calibre:series"') == 1
    assert "Old Wrong Series" not in opf
    assert _parse(out).series == "Right"


def test_no_series_writes_no_series_tags() -> None:
    original = build_epub(series="Had A Series", series_number=3)
    out = write_metadata(original, EpubMetadata(title="Standalone", author="A"))
    opf = zipfile.ZipFile(io.BytesIO(out)).read("OEBPS/content.opf").decode()
    assert "calibre:series" not in opf
    assert "belongs-to-collection" not in opf
    assert _parse(out).series is None


def test_none_author_leaves_creators_untouched() -> None:
    original = build_epub(authors=("Original Author",))
    out = write_metadata(original, EpubMetadata(title="T", author=None))
    assert _parse(out).authors == ["Original Author"]


def test_rerun_is_idempotent() -> None:
    meta = EpubMetadata(title="T", author="A", series="S", series_number=1.0)
    once = write_metadata(build_epub(), meta)
    twice = write_metadata(once, meta)
    opf = zipfile.ZipFile(io.BytesIO(twice)).read("OEBPS/content.opf").decode()
    assert opf.count('name="calibre:series"') == 1
    assert opf.count('property="belongs-to-collection"') == 1
    ev = _parse(twice)
    assert (ev.title, ev.authors, ev.series) == ("T", ["A"], "S")


# --------------------------------------------------------------------------
# rootfile location
# --------------------------------------------------------------------------


def _epub_with_rootfile_at(path: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"'
            f' version="1.0"><rootfiles><rootfile full-path="{path}"'
            ' media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        zf.writestr(
            path,
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0"'
            ' unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            "<dc:title>x</dc:title><dc:language>en</dc:language>"
            '<dc:identifier id="id">u</dc:identifier></metadata>'
            '<manifest><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
            '</manifest><spine><itemref idref="c1"/></spine></package>',
        )
        opf_dir = path.rsplit("/", 1)[0] if "/" in path else ""
        zf.writestr(
            f"{opf_dir}/c1.xhtml" if opf_dir else "c1.xhtml", "<html><body>hi</body></html>"
        )
    return buf.getvalue()


def test_rootfile_not_under_oebps() -> None:
    original = _epub_with_rootfile_at("pkg.opf")
    out = write_metadata(original, EpubMetadata(title="Moved", author="A"))
    assert zipfile.ZipFile(io.BytesIO(out)).namelist()[0] == "mimetype"
    assert _parse(out).title == "Moved"


# --------------------------------------------------------------------------
# cover
# --------------------------------------------------------------------------


def test_cover_injected_when_epub_has_none() -> None:
    original = _epub_with_rootfile_at("OEBPS/content.opf")
    assert extract_cover(original, **_LIMITS) is None
    out = write_metadata(original, EpubMetadata(title="T", author="A"), cover_bytes=_jpg())

    got = extract_cover(out, **_LIMITS)
    assert got is not None
    assert Image.open(io.BytesIO(got)).size == (400, 600)
    _opens_in_ebooklib(out)


def test_existing_cover_is_left_alone() -> None:
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0"'
        ' unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:title>x</dc:title><meta name="cover" content="cov"/></metadata>'
        '<manifest><item id="cov" href="images/real-cover.png" media-type="image/png"/>'
        '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/></manifest>'
        '<spine><itemref idref="c1"/></spine></package>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"'
            ' version="1.0"><rootfiles><rootfile full-path="OEBPS/content.opf"'
            ' media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/images/real-cover.png", _png("green"))
        zf.writestr("OEBPS/c1.xhtml", "<html><body/></html>")
    original = buf.getvalue()

    out = write_metadata(original, EpubMetadata(title="T", author="A"), cover_bytes=_jpg())
    names = zipfile.ZipFile(io.BytesIO(out)).namelist()
    assert not any("bookbrain-cover" in n for n in names)
    # original cover image still there, untouched
    assert (
        zipfile.ZipFile(io.BytesIO(out)).read("OEBPS/images/real-cover.png")
        == zipfile.ZipFile(io.BytesIO(original)).read("OEBPS/images/real-cover.png")
    )


def test_no_cover_and_no_cover_bytes_is_metadata_only() -> None:
    original = _epub_with_rootfile_at("OEBPS/content.opf")
    out = write_metadata(original, EpubMetadata(title="T", author="A"), cover_bytes=None)
    assert not any("bookbrain-cover" in n for n in zipfile.ZipFile(io.BytesIO(out)).namelist())


# --------------------------------------------------------------------------
# failure modes
# --------------------------------------------------------------------------


def test_raises_on_garbage_bytes() -> None:
    with pytest.raises(EpubWriteError):
        write_metadata(b"not a zip at all", EpubMetadata(title="T"))


def test_raises_when_container_missing() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("random.txt", "hi")
    with pytest.raises(EpubWriteError):
        write_metadata(buf.getvalue(), EpubMetadata(title="T"))
