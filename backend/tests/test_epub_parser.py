import zipfile

import pytest

from app.providers.epub.errors import (
    EpubParseError,
    EpubParseTimeoutError,
    EpubTooLargeError,
    EpubTooManyEntriesError,
)
from app.providers.epub.parser import parse_epub, parse_epub_safely
from tests.epub_fixtures import build_epub, build_rich_epub

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


def test_stage_d_extracts_publisher_date_subjects_and_all_isbns() -> None:
    data = build_rich_epub(
        title="Ancillary Justice",
        authors=("Ann Leckie",),
        description="A soldier seeks revenge across a galactic empire.",
        publisher="Orbit Books",
        pub_date="2013-10-01",
        subjects=("Science Fiction", "Space Opera"),
        identifiers=("urn:isbn:9780316246620", "9780356502403"),
        source="urn:isbn:031624662X",
    )

    ev = parse_epub(data, **GENEROUS)

    assert ev.publisher == "Orbit Books"
    assert ev.pub_date == "2013-10-01"
    assert ev.subjects == ["Science Fiction", "Space Opera"]
    assert ev.description.startswith("A soldier seeks revenge")
    # both <dc:identifier> ISBNs and the <dc:source> ISBN, deduped, in order
    assert ev.all_isbns == ["9780316246620", "9780356502403", "031624662X"]
    assert ev.isbn13 == "9780316246620"
    assert ev.isbn10 == "031624662X"


def test_stage_d_text_snippet_skips_cover_and_takes_front_matter_plus_body() -> None:
    copyright_page = (
        "First published in 2013 by Orbit Books. Copyright Ann Leckie. "
        "ISBN 978-0-316-24662-0. All rights reserved. " * 4
    )
    chapter = "The body of Lieutenant Awn lay naked and cooling in the snow. " * 10
    data = build_rich_epub(
        spine=(
            ("cover.xhtml", "<img src='c.jpg'/>"),
            ("titlepage.xhtml", "<h1>Ancillary Justice</h1>"),
            ("copyright.xhtml", f"<p>{copyright_page}</p>"),
            ("chapter1.xhtml", f"<p>{chapter}</p>"),
            ("chapter2.xhtml", "<p>later chapter</p>"),
        ),
    )

    ev = parse_epub(data, **GENEROUS)

    assert "[front matter]" in ev.text_snippet
    assert "First published in 2013" in ev.text_snippet
    assert "Ancillary Justice</h1>" not in ev.text_snippet  # titlepage skipped, tags stripped
    assert "body of Lieutenant Awn" in ev.text_snippet


def test_stage_d_text_snippet_falls_back_for_a_tiny_book() -> None:
    data = build_epub(
        chapter_text="<html><body><p>It was a dark and stormy night.</p></body></html>"
    )
    ev = parse_epub(data, **GENEROUS)
    assert "dark and stormy night" in ev.text_snippet


def test_stage_d_nav_document_is_skipped() -> None:
    data = build_rich_epub(
        spine=(
            ("nav.xhtml", "<nav>lots of table of contents entries here " * 20 + "</nav>"),
            ("chapter1.xhtml", "<p>" + "Real prose begins here now. " * 20 + "</p>"),
        ),
        manifest_extra_props={"nav.xhtml": "nav"},
    )
    ev = parse_epub(data, **GENEROUS)
    assert "table of contents" not in ev.text_snippet
    assert "Real prose begins here" in ev.text_snippet


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
