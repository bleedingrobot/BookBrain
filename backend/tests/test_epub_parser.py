import zipfile

import pytest

from app.providers.epub.errors import (
    EpubParseError,
    EpubParseTimeoutError,
    EpubTooLargeError,
    EpubTooManyEntriesError,
)
from app.providers.epub.parser import parse_epub, parse_epub_safely
from tests.epub_fixtures import build_epub

GENEROUS = dict(max_entry_bytes=10_000_000, max_total_bytes=50_000_000, max_entries=1000)


def test_extracts_core_metadata() -> None:
    data = build_epub(
        title="Dune",
        authors=("Frank Herbert",),
        language="en",
        isbn="9780134685991",
        series="Dune Chronicles",
        series_number=1,
    )

    evidence = parse_epub(data, **GENEROUS)

    assert evidence.title == "Dune"
    assert evidence.authors == ["Frank Herbert"]
    assert evidence.language == "en"
    assert evidence.isbn13 == "9780134685991"
    assert evidence.series == "Dune Chronicles"
    assert evidence.series_number == 1.0


def test_extracts_text_snippet_without_tags() -> None:
    data = build_epub(chapter_text="<html><body><p>It was a dark and stormy night.</p></body></html>")

    evidence = parse_epub(data, **GENEROUS)

    assert "dark and stormy night" in evidence.text_snippet
    assert "<" not in evidence.text_snippet


def test_multiple_authors() -> None:
    data = build_epub(authors=("Author One", "Author Two"))

    evidence = parse_epub(data, **GENEROUS)

    assert evidence.authors == ["Author One", "Author Two"]


def test_missing_container_xml_raises() -> None:
    data = build_epub(extra_files={})
    # Rebuild without container.xml by constructing a bare zip
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
    bare = buf.getvalue()

    with pytest.raises(EpubParseError):
        parse_epub(bare, **GENEROUS)


def test_missing_opf_target_raises() -> None:
    data = build_epub(
        container_xml="""<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles>
    <rootfile full-path="OEBPS/does-not-exist.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""
    )

    with pytest.raises(EpubParseError):
        parse_epub(data, **GENEROUS)


def test_too_many_entries_rejected() -> None:
    data = build_epub()

    with pytest.raises(EpubTooManyEntriesError):
        parse_epub(data, max_entry_bytes=10_000_000, max_total_bytes=50_000_000, max_entries=1)


def test_entry_exceeding_cap_rejected() -> None:
    data = build_epub(chapter_text="x" * 1000)

    with pytest.raises(EpubTooLargeError):
        parse_epub(data, max_entry_bytes=100, max_total_bytes=50_000_000, max_entries=1000)


def test_total_size_exceeding_cap_rejected() -> None:
    data = build_epub(chapter_text="x" * 1000)

    with pytest.raises(EpubTooLargeError):
        parse_epub(data, max_entry_bytes=10_000_000, max_total_bytes=200, max_entries=1000)


def test_xxe_attempt_does_not_leak_and_is_rejected() -> None:
    hostile_container = """<?xml version="1.0"?>
<!DOCTYPE container [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml">&xxe;</rootfile>
  </rootfiles>
</container>"""
    data = build_epub(container_xml=hostile_container)

    with pytest.raises(EpubParseError):
        parse_epub(data, **GENEROUS)


def test_billion_laughs_is_rejected() -> None:
    hostile_container = """<?xml version="1.0"?>
<!DOCTYPE lolz [
 <!ENTITY lol "lol">
 <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
]>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml">&lol2;</rootfile>
  </rootfiles>
</container>"""
    data = build_epub(container_xml=hostile_container)

    with pytest.raises(EpubParseError):
        parse_epub(data, **GENEROUS)


def test_bad_zip_raises_parse_error() -> None:
    with pytest.raises(EpubParseError):
        parse_epub(b"not a zip file at all", **GENEROUS)


def test_parse_epub_safely_times_out(monkeypatch) -> None:
    import time

    from app.providers.epub import parser as parser_module

    def slow_parse(*args, **kwargs):
        time.sleep(0.2)
        return parser_module.EpubEvidence()

    monkeypatch.setattr(parser_module, "parse_epub", slow_parse)

    with pytest.raises(EpubParseTimeoutError):
        parser_module.parse_epub_safely(
            build_epub(), timeout_seconds=0.01, **GENEROUS
        )
