import posixpath
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field

import isbnlib
from defusedxml import ElementTree as DET
from defusedxml.common import DefusedXmlException

from app.providers.epub.errors import EpubParseError, EpubParseTimeoutError
from app.providers.epub.safe_zip import SafeZipReader

OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"
CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"

_TAG_RE = re.compile(rb"<[^>]+>")
_WS_RE = re.compile(r"\s+")

_executor = ThreadPoolExecutor(max_workers=4)


@dataclass
class EpubEvidence:
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    language: str | None = None
    description: str | None = None
    isbn10: str | None = None
    isbn13: str | None = None
    series: str | None = None
    series_number: float | None = None
    text_snippet: str = ""


def parse_epub(
    data: bytes,
    *,
    max_entry_bytes: int,
    max_total_bytes: int,
    max_entries: int,
    text_snippet_chars: int = 4000,
) -> EpubEvidence:
    reader = SafeZipReader(
        data,
        max_entry_bytes=max_entry_bytes,
        max_total_bytes=max_total_bytes,
        max_entries=max_entries,
    )

    opf_path = _find_opf_path(reader)
    opf_root = _safe_xml_parse(reader.read(opf_path))
    opf_dir = posixpath.dirname(opf_path)

    evidence = _extract_metadata(opf_root)
    evidence.text_snippet = _extract_text_snippet(reader, opf_root, opf_dir, text_snippet_chars)
    return evidence


def parse_epub_safely(
    data: bytes,
    *,
    max_entry_bytes: int,
    max_total_bytes: int,
    max_entries: int,
    timeout_seconds: int,
    text_snippet_chars: int = 4000,
) -> EpubEvidence:
    future = _executor.submit(
        parse_epub,
        data,
        max_entry_bytes=max_entry_bytes,
        max_total_bytes=max_total_bytes,
        max_entries=max_entries,
        text_snippet_chars=text_snippet_chars,
    )
    try:
        return future.result(timeout=timeout_seconds)
    except FutureTimeoutError as exc:
        raise EpubParseTimeoutError("EPUB parsing exceeded the time limit") from exc


def extract_cover(
    data: bytes,
    *,
    max_entry_bytes: int,
    max_total_bytes: int,
    max_entries: int,
) -> bytes | None:
    """Raw bytes of the EPUB's cover image, or None if it has no
    identifiable cover. Resolution order matches how readers pick it:
      1. a manifest item with properties="cover-image" (EPUB 3)
      2. <meta name="cover" content="ID"> pointing at a manifest item (EPUB 2)
      3. a manifest image whose id/href looks like "cover"
    """
    try:
        reader = SafeZipReader(
            data,
            max_entry_bytes=max_entry_bytes,
            max_total_bytes=max_total_bytes,
            max_entries=max_entries,
        )
        opf_path = _find_opf_path(reader)
        opf_root = _safe_xml_parse(reader.read(opf_path))
        opf_dir = posixpath.dirname(opf_path)

        manifest_el = opf_root.find(f"{{{OPF_NS}}}manifest")
        if manifest_el is None:
            return None
        items = manifest_el.findall(f"{{{OPF_NS}}}item")

        href = _cover_href_from_properties(items)
        if href is None:
            href = _cover_href_from_meta(opf_root, items)
        if href is None:
            href = _cover_href_by_name(items)
        if href is None:
            return None

        path = posixpath.normpath(posixpath.join(opf_dir, href)) if opf_dir else href
        if not reader.exists(path):
            return None
        return reader.read(path)
    except (EpubParseError, EpubParseTimeoutError, KeyError, ValueError):
        return None


def _cover_href_from_properties(items: list) -> str | None:
    for item in items:
        props = item.attrib.get("properties", "")
        if "cover-image" in props.split() and item.attrib.get("href"):
            return item.attrib["href"]
    return None


def _cover_href_from_meta(opf_root, items: list) -> str | None:
    metadata_el = opf_root.find(f"{{{OPF_NS}}}metadata")
    if metadata_el is None:
        return None
    cover_id = next(
        (
            m.attrib.get("content")
            for m in metadata_el.findall(f"{{{OPF_NS}}}meta")
            if m.attrib.get("name") == "cover"
        ),
        None,
    )
    if not cover_id:
        return None
    return next(
        (i.attrib["href"] for i in items if i.attrib.get("id") == cover_id and i.attrib.get("href")),
        None,
    )


def _cover_href_by_name(items: list) -> str | None:
    for item in items:
        media_type = item.attrib.get("media-type", "")
        href = item.attrib.get("href", "")
        ident = item.attrib.get("id", "")
        if media_type.startswith("image/") and (
            "cover" in ident.lower() or "cover" in posixpath.basename(href).lower()
        ):
            return href
    return None


def _safe_xml_parse(data: bytes) -> ET.Element:
    """defusedxml blocks entity expansion / external references but raises
    its own exception types — normalize to EpubParseError so callers (the
    scan pipeline) have one failure mode to handle, not a leaked internal
    exception type from a hostile file."""
    try:
        return DET.fromstring(data)
    except (DefusedXmlException, ET.ParseError) as exc:
        raise EpubParseError(f"unsafe or malformed XML: {exc}") from exc


def _find_opf_path(reader: SafeZipReader) -> str:
    if not reader.exists("META-INF/container.xml"):
        raise EpubParseError("missing META-INF/container.xml")

    container_root = _safe_xml_parse(reader.read("META-INF/container.xml"))
    rootfile = container_root.find(f".//{{{CONTAINER_NS}}}rootfile")
    if rootfile is None or "full-path" not in rootfile.attrib:
        raise EpubParseError("container.xml has no rootfile with a full-path")

    opf_path = rootfile.attrib["full-path"]
    if not reader.exists(opf_path):
        raise EpubParseError(f"OPF file {opf_path!r} not found in archive")
    return opf_path


def _extract_metadata(opf_root) -> EpubEvidence:
    evidence = EpubEvidence()
    metadata_el = opf_root.find(f"{{{OPF_NS}}}metadata")
    if metadata_el is None:
        return evidence

    title_el = metadata_el.find(f"{{{DC_NS}}}title")
    if title_el is not None and title_el.text:
        evidence.title = title_el.text.strip()

    evidence.authors = [
        el.text.strip()
        for el in metadata_el.findall(f"{{{DC_NS}}}creator")
        if el.text and el.text.strip()
    ]

    lang_el = metadata_el.find(f"{{{DC_NS}}}language")
    if lang_el is not None and lang_el.text:
        evidence.language = lang_el.text.strip()

    desc_el = metadata_el.find(f"{{{DC_NS}}}description")
    if desc_el is not None and desc_el.text:
        evidence.description = desc_el.text.strip()

    for id_el in metadata_el.findall(f"{{{DC_NS}}}identifier"):
        classified = _classify_identifier(id_el.text or "")
        if classified is None:
            continue
        kind, value = classified
        if kind == "isbn13":
            evidence.isbn13 = value
        elif kind == "isbn10":
            evidence.isbn10 = value

    for meta_el in metadata_el.findall(f"{{{OPF_NS}}}meta"):
        name = meta_el.attrib.get("name")
        if name == "calibre:series":
            evidence.series = meta_el.attrib.get("content")
        elif name == "calibre:series_index":
            raw_index = meta_el.attrib.get("content")
            if raw_index:
                try:
                    evidence.series_number = float(raw_index)
                except ValueError:
                    pass

    return evidence


def _extract_text_snippet(reader: SafeZipReader, opf_root, opf_dir: str, max_chars: int) -> str:
    manifest_el = opf_root.find(f"{{{OPF_NS}}}manifest")
    spine_el = opf_root.find(f"{{{OPF_NS}}}spine")
    if manifest_el is None or spine_el is None:
        return ""

    href_by_id = {
        item.attrib["id"]: item.attrib["href"]
        for item in manifest_el.findall(f"{{{OPF_NS}}}item")
        if "id" in item.attrib and "href" in item.attrib
    }

    for itemref in spine_el.findall(f"{{{OPF_NS}}}itemref"):
        href = href_by_id.get(itemref.attrib.get("idref", ""))
        if not href:
            continue
        content_path = posixpath.normpath(posixpath.join(opf_dir, href)) if opf_dir else href
        if not reader.exists(content_path):
            continue
        return _strip_tags(reader.read(content_path))[:max_chars]

    return ""


def _classify_identifier(value: str) -> tuple[str, str] | None:
    cleaned = value.split(":")[-1].strip()
    canonical = isbnlib.canonical(cleaned)
    if not canonical:
        return None
    if isbnlib.is_isbn13(canonical):
        return "isbn13", canonical
    if isbnlib.is_isbn10(canonical):
        return "isbn10", canonical
    return None


def _strip_tags(raw: bytes) -> str:
    text = _TAG_RE.sub(b" ", raw).decode("utf-8", errors="replace")
    return _WS_RE.sub(" ", text).strip()
