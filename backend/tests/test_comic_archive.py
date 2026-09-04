import io
import zipfile

import pytest

from app.providers.comic.archive import (
    extract_comic_cover,
    is_comic_archive,
    parse_comic_archive,
)
from app.providers.epub.errors import EpubParseError

_LIMITS = {"max_entry_bytes": 10_000_000, "max_total_bytes": 50_000_000, "max_entries": 1000}


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
    assert is_comic_archive("book.epub") is False
    assert is_comic_archive("comic.cbr") is False


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
