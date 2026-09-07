import io
import zipfile

import pytest

from app.providers.comic.archive import (
    _archive_kind,
    extract_comic_cover,
    is_comic_archive,
    parse_comic_archive,
)
from app.providers.comic.rar import _parse_listing, seven_zip_available
from app.providers.epub.errors import EpubParseError
from tests.comic_fixtures import make_stored_rar

_LIMITS = {"max_entry_bytes": 10_000_000, "max_total_bytes": 50_000_000, "max_entries": 1000}

_JPEG = b"\xff\xd8\xff\xe0 fake jpeg bytes"

needs_7zip = pytest.mark.skipif(not seven_zip_available(), reason="7-Zip not installed")


def _cbr(pages: list[str], *, comicinfo: str | None = None, extra: dict[str, bytes] | None = None) -> bytes:
    members: dict[str, bytes] = {name: _JPEG for name in pages}
    if comicinfo is not None:
        members["ComicInfo.xml"] = comicinfo.encode("utf-8")
    members.update(extra or {})
    return make_stored_rar(members)


def _cbz(pages: list[str], *, comicinfo: str | None = None, extra: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name in pages:
            zf.writestr(name, b"\xff\xd8\xff\xe0 fake jpeg bytes")
        if comicinfo is not None:
            zf.writestr("ComicInfo.xml", comicinfo)
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_is_comic_archive() -> None:
    assert is_comic_archive("Saga 001.cbz") is True
    assert is_comic_archive("Saga 001.CBZ") is True
    assert is_comic_archive("comic.cbr") is True
    assert is_comic_archive("comic.CBR") is True
    assert is_comic_archive("book.epub") is False


def test_archive_kind_sniffs_by_magic_not_extension() -> None:
    zip_bytes = _cbz(["000.jpg"])
    rar_bytes = _cbr(["000.jpg"])
    assert _archive_kind(zip_bytes) == "zip"
    assert _archive_kind(rar_bytes) == "rar"
    assert _archive_kind(b"Rar!\x1a\x07\x01\x00rest") == "rar"  # RAR5 magic
    assert _archive_kind(b"%PDF-1.4") is None


def test_parse_7z_listing() -> None:
    sample = (
        "\n----------\n"
        "Path = comic/000.jpg\nSize = 1234\nAttributes = A\n\n"
        "Path = comic\nSize = 0\nAttributes = D\n\n"
        "Path = comic/ComicInfo.xml\nSize = 88\nFolder = -\n"
    )
    assert _parse_listing(sample) == [("comic/000.jpg", 1234), ("comic/ComicInfo.xml", 88)]


def test_parse_empty_when_no_comicinfo() -> None:
    evidence = parse_comic_archive(_cbz(["000.jpg", "001.jpg"]), **_LIMITS)
    assert evidence.title is None
    assert evidence.authors == []
    assert evidence.series is None


def test_parse_reads_comicinfo() -> None:
    comicinfo = """<?xml version="1.0"?>
<ComicInfo>
  <Title>A Dream of You</Title>
  <Series>Saga</Series>
  <Number>3</Number>
  <Writer>Brian K. Vaughan, Fiona Staples</Writer>
  <LanguageISO>en</LanguageISO>
  <Summary>Space opera.</Summary>
</ComicInfo>"""
    evidence = parse_comic_archive(_cbz(["000.jpg"], comicinfo=comicinfo), **_LIMITS)
    assert evidence.title == "A Dream of You"
    assert evidence.series == "Saga"
    assert evidence.series_number == 3.0
    assert evidence.authors == ["Brian K. Vaughan", "Fiona Staples"]
    assert evidence.language == "en"
    assert evidence.description == "Space opera."


def test_parse_synthesizes_title_from_series_and_number_when_title_missing() -> None:
    comicinfo = "<ComicInfo><Series>Batman</Series><Number>404</Number></ComicInfo>"
    evidence = parse_comic_archive(_cbz(["000.jpg"], comicinfo=comicinfo), **_LIMITS)
    assert evidence.title == "Batman #404"


def test_parse_ignores_malformed_comicinfo() -> None:
    evidence = parse_comic_archive(_cbz(["000.jpg"], comicinfo="<ComicInfo><broken"), **_LIMITS)
    assert evidence.title is None


def test_parse_rejects_archive_with_no_images() -> None:
    with pytest.raises(EpubParseError, match="no page images"):
        parse_comic_archive(_cbz([], extra={"readme.txt": b"hi"}), **_LIMITS)


def test_parse_rejects_non_zip() -> None:
    with pytest.raises(EpubParseError):
        parse_comic_archive(b"not a zip", **_LIMITS)


def test_extract_cover_returns_first_page_by_name() -> None:
    data = _cbz(["002.jpg", "000.jpg", "001.jpg"])
    cover = extract_comic_cover(data, **_LIMITS)
    assert cover == b"\xff\xd8\xff\xe0 fake jpeg bytes"


def test_extract_cover_skips_macos_junk() -> None:
    data = _cbz(["page.jpg"], extra={"__MACOSX/._page.jpg": b"junk"})
    assert extract_comic_cover(data, **_LIMITS) == b"\xff\xd8\xff\xe0 fake jpeg bytes"


def test_extract_cover_none_when_no_images() -> None:
    assert extract_comic_cover(_cbz([], extra={"x.txt": b"x"}), **_LIMITS) is None
    assert extract_comic_cover(b"not a zip", **_LIMITS) is None


# --- .cbr (RAR) — same behaviour as .cbz, different container -----------------


@needs_7zip
def test_cbr_parse_empty_when_no_comicinfo() -> None:
    evidence = parse_comic_archive(_cbr(["000.jpg", "001.jpg"]), **_LIMITS)
    assert evidence.title is None
    assert evidence.series is None


@needs_7zip
def test_cbr_parse_reads_comicinfo() -> None:
    comicinfo = (
        "<ComicInfo><Title>Winter Turning</Title><Series>Wings of Fire</Series>"
        "<Number>7</Number><Writer>Tui T. Sutherland</Writer></ComicInfo>"
    )
    evidence = parse_comic_archive(_cbr(["000.jpg"], comicinfo=comicinfo), **_LIMITS)
    assert evidence.title == "Winter Turning"
    assert evidence.series == "Wings of Fire"
    assert evidence.series_number == 7.0
    assert evidence.authors == ["Tui T. Sutherland"]


@needs_7zip
def test_cbr_reads_comicinfo_nested_in_a_subfolder() -> None:
    comicinfo = "<ComicInfo><Series>Wings of Fire</Series><Number>7</Number></ComicInfo>"
    data = _cbr([], extra={"Winter Turning/000.jpg": _JPEG, "Winter Turning/ComicInfo.xml": comicinfo.encode()})
    evidence = parse_comic_archive(data, **_LIMITS)
    assert evidence.series == "Wings of Fire"


@needs_7zip
def test_cbr_extract_cover_returns_first_page_by_name() -> None:
    data = _cbr(["002.jpg", "000.jpg", "001.jpg"])
    assert extract_comic_cover(data, **_LIMITS) == _JPEG


@needs_7zip
def test_cbr_rejects_archive_with_no_images() -> None:
    with pytest.raises(EpubParseError, match="no page images"):
        parse_comic_archive(_cbr([], extra={"readme.txt": b"hi"}), **_LIMITS)


@needs_7zip
def test_cbr_enforces_size_caps_from_the_listing() -> None:
    big = _cbr([], extra={"page.jpg": b"x" * 5000})
    with pytest.raises(EpubParseError):
        parse_comic_archive(big, max_entry_bytes=1000, max_total_bytes=50_000, max_entries=1000)


@needs_7zip
def test_a_cbz_extension_holding_a_rar_still_works() -> None:
    # Mislabelled files are common — container is chosen by magic bytes.
    rar_bytes = _cbr(["000.jpg"], comicinfo="<ComicInfo><Series>X</Series></ComicInfo>")
    assert _archive_kind(rar_bytes) == "rar"
    evidence = parse_comic_archive(rar_bytes, **_LIMITS)
    assert evidence.series == "X"


def test_a_cbr_extension_holding_a_zip_still_works() -> None:
    zip_bytes = _cbz(["000.jpg"], comicinfo="<ComicInfo><Series>Y</Series></ComicInfo>")
    assert _archive_kind(zip_bytes) == "zip"
    evidence = parse_comic_archive(zip_bytes, **_LIMITS)
    assert evidence.series == "Y"
