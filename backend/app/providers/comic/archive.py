"""Comic archives (.cbz) are kept in their original format — never converted
to EPUB — so all this does is read whatever metadata the archive actually
carries: a `ComicInfo.xml` sidecar if present (the ComicRack/Anansi de-facto
standard), and otherwise nothing but the filename the caller already has.

Everything is expressed as an `EpubEvidence` so the rest of the scan
pipeline (candidates, AI identification, confidence, quality score) needs no
comic-specific branch — an unpopulated field just means "the archive didn't
say", exactly as it does for an EPUB with thin metadata.
"""

import posixpath
import xml.etree.ElementTree as ET

import isbnlib
from defusedxml import ElementTree as DET
from defusedxml.common import DefusedXmlException

from app.providers.epub.errors import EpubParseError
from app.providers.epub.parser import EpubEvidence
from app.providers.epub.safe_zip import SafeZipReader

_COMIC_EXTENSIONS = (".cbz",)

# The page-image formats a .cbz realistically holds. WebP/AVIF show up in
# modern "digital" releases; the older ones are almost always JPEG.
_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".bmp")

_COMICINFO_NAMES = ("ComicInfo.xml", "comicinfo.xml")


def is_comic_archive(filename: str) -> bool:
    return filename.lower().endswith(_COMIC_EXTENSIONS)


def _image_names(reader: SafeZipReader) -> list[str]:
    return [
        name
        for name in reader.names
        if posixpath.splitext(name)[1].lower() in _IMAGE_EXTENSIONS
        and not posixpath.basename(name).startswith(".")  # skip __MACOSX/._* junk
    ]


def _reader(data: bytes, *, max_entry_bytes: int, max_total_bytes: int, max_entries: int) -> SafeZipReader:
    return SafeZipReader(
        data,
        max_entry_bytes=max_entry_bytes,
        max_total_bytes=max_total_bytes,
        max_entries=max_entries,
    )


def parse_comic_archive(
    data: bytes,
    *,
    max_entry_bytes: int,
    max_total_bytes: int,
    max_entries: int,
) -> EpubEvidence:
    """Evidence for a .cbz. Raises `EpubParseError` (the same failure mode the
    scan pipeline already handles for a corrupt EPUB) when the archive isn't a
    valid zip or holds no page images at all — that's not a comic."""
    reader = _reader(
        data,
        max_entry_bytes=max_entry_bytes,
        max_total_bytes=max_total_bytes,
        max_entries=max_entries,
    )
    if not _image_names(reader):
        raise EpubParseError("comic archive contains no page images")

    evidence = EpubEvidence()
    comicinfo = next((n for n in _COMICINFO_NAMES if reader.exists(n)), None)
    if comicinfo is not None:
        _apply_comicinfo(evidence, reader.read(comicinfo))
    return evidence


def extract_comic_cover(
    data: bytes,
    *,
    max_entry_bytes: int,
    max_total_bytes: int,
    max_entries: int,
) -> bytes | None:
    """Raw bytes of the comic's cover — the first page image by sorted name,
    which is the near-universal packing convention (000.jpg / cover.jpg sort
    first). None if the archive can't be read or has no images."""
    try:
        reader = _reader(
            data,
            max_entry_bytes=max_entry_bytes,
            max_total_bytes=max_total_bytes,
            max_entries=max_entries,
        )
    except EpubParseError:
        return None
    images = _image_names(reader)
    if not images:
        return None
    try:
        return reader.read(images[0])
    except EpubParseError:
        return None


def _apply_comicinfo(evidence: EpubEvidence, raw: bytes) -> None:
    """ComicInfo.xml is a flat, namespace-less element per field. Best-effort:
    a malformed sidecar just leaves the evidence empty rather than failing the
    whole file."""
    try:
        root = DET.fromstring(raw)
    except (DefusedXmlException, ET.ParseError):
        return

    def text(tag: str) -> str | None:
        el = root.find(tag)
        if el is not None and el.text and el.text.strip():
            return el.text.strip()
        return None

    series = text("Series")
    number = text("Number")
    title = text("Title")

    evidence.series = series
    if number:
        try:
            evidence.series_number = float(number)
        except ValueError:
            pass

    if title:
        evidence.title = title
    elif series:
        evidence.title = f"{series} #{number}" if number else series

    writers = text("Writer")
    if writers:
        evidence.authors = [w.strip() for w in writers.split(",") if w.strip()]

    language = text("LanguageISO")
    if language:
        evidence.language = language

    summary = text("Summary")
    if summary:
        evidence.description = summary

    # GTIN occasionally carries the collected edition's ISBN.
    gtin = text("GTIN")
    if gtin:
        canonical = isbnlib.canonical(gtin)
        if isbnlib.is_isbn13(canonical):
            evidence.isbn13 = canonical
        elif isbnlib.is_isbn10(canonical):
            evidence.isbn10 = canonical
