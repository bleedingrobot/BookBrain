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
    # prompts/15 Stage D — extra deterministic evidence. All optional; added
    # with defaults so every existing caller and the evidence round-trip keep
    # working. NOT part of hash_evidence (the AI-decision cache key) — they
    # enrich the prompt for genuinely-new files without invalidating the
    # ~2200 cached decisions.
    publisher: str | None = None
    pub_date: str | None = None
    subjects: list[str] = field(default_factory=list)
    all_isbns: list[str] = field(default_factory=list)


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

    pub_el = metadata_el.find(f"{{{DC_NS}}}publisher")
    if pub_el is not None and pub_el.text and pub_el.text.strip():
        evidence.publisher = pub_el.text.strip()

    evidence.pub_date = _pick_publication_date(metadata_el)

    evidence.subjects = [
        el.text.strip()
        for el in metadata_el.findall(f"{{{DC_NS}}}subject")
        if el.text and el.text.strip()
    ]

    evidence.all_isbns = _collect_isbns(metadata_el)
    evidence.isbn13 = next((i for i in evidence.all_isbns if len(i) == 13), None)
    evidence.isbn10 = next((i for i in evidence.all_isbns if len(i) == 10), None)

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


_MIN_SUBSTANTIVE_CHARS = 200
_MAX_DOCS_SCANNED = 10
_FRONTMATTER_NAME_RE = re.compile(
    r"(cover|titlepage|title[-_]?page|halftitle|half[-_]?title|toc|nav|contents|frontmatter)",
    re.IGNORECASE,
)


def _extract_text_snippet(reader: SafeZipReader, opf_root, opf_dir: str, max_chars: int) -> str:
    """prompts/15 Stage D. The first spine document is very often
    ``cover.xhtml`` / ``titlepage.xhtml`` — near-useless. Walk further: take the
    first ~2 *substantive* documents (the copyright page, with publisher /
    "first published" / ISBN, usually lands here) plus one document ~20% into
    the spine (real first-chapter prose — a strong fingerprint), each labelled.
    Bounded to ``_MAX_DOCS_SCANNED`` reads to stay well inside the parse
    timeout."""
    manifest_el = opf_root.find(f"{{{OPF_NS}}}manifest")
    spine_el = opf_root.find(f"{{{OPF_NS}}}spine")
    if manifest_el is None or spine_el is None:
        return ""

    item_by_id = {
        item.attrib["id"]: item
        for item in manifest_el.findall(f"{{{OPF_NS}}}item")
        if "id" in item.attrib and "href" in item.attrib
    }

    spine_docs: list[tuple[str, set[str]]] = []
    for itemref in spine_el.findall(f"{{{OPF_NS}}}itemref"):
        item = item_by_id.get(itemref.attrib.get("idref", ""))
        if item is None:
            continue
        path = (
            posixpath.normpath(posixpath.join(opf_dir, item.attrib["href"]))
            if opf_dir
            else item.attrib["href"]
        )
        if reader.exists(path):
            spine_docs.append((path, set(item.attrib.get("properties", "").split())))

    if not spine_docs:
        return ""

    def _skippable(path: str, props: set[str]) -> bool:
        return bool(props & {"nav", "cover-image"}) or bool(
            _FRONTMATTER_NAME_RE.search(posixpath.basename(path))
        )

    front: list[str] = []
    reads = 0
    for path, props in spine_docs:
        if reads >= _MAX_DOCS_SCANNED or len(front) >= 2:
            break
        if _skippable(path, props):
            continue
        reads += 1
        text = _strip_tags(reader.read(path))
        if len(text) >= _MIN_SUBSTANTIVE_CHARS:
            front.append(text)

    body = ""
    start = max(1, int(len(spine_docs) * 0.2))
    for path, props in spine_docs[start : start + 6]:
        if _skippable(path, props):
            continue
        text = _strip_tags(reader.read(path))
        if len(text) >= _MIN_SUBSTANTIVE_CHARS and text not in front:
            body = text
            break

    if not front and not body:
        # Nothing substantive found (very short book, all front matter) —
        # fall back to the old behaviour: the first spine document as-is.
        return _strip_tags(reader.read(spine_docs[0][0]))[:max_chars]

    parts: list[str] = []
    front_budget = (max_chars // 2) if body else max_chars
    if front:
        parts.append("[front matter] " + " ".join(front)[:front_budget])
    if body:
        used = len(parts[0]) if parts else 0
        parts.append("[body sample] " + body[: max(0, max_chars - used - 20)])
    return "\n\n".join(parts)[:max_chars]


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


def _collect_isbns(metadata_el) -> list[str]:
    """Every valid ISBN in the metadata, deduped, in document order. Widened
    from "last <dc:identifier> wins" (prompts/15 F2): also reads every
    <dc:identifier> and every <dc:source> — an ebook often carries the print
    ISBN in <dc:source> and its own in <dc:identifier>, or several editions'
    ISBNs across multiple <dc:identifier> elements."""
    seen: list[str] = []
    elements = [
        *metadata_el.findall(f"{{{DC_NS}}}identifier"),
        *metadata_el.findall(f"{{{DC_NS}}}source"),
    ]
    for el in elements:
        classified = _classify_identifier(el.text or "")
        if classified is not None and classified[1] not in seen:
            seen.append(classified[1])
    return seen


def _pick_publication_date(metadata_el) -> str | None:
    """First <dc:date>, preferring one flagged as the publication date via the
    legacy ``opf:event="publication"`` attribute."""
    dates = metadata_el.findall(f"{{{DC_NS}}}date")
    for el in dates:
        if el.attrib.get(f"{{{OPF_NS}}}event") == "publication" and el.text and el.text.strip():
            return el.text.strip()
    for el in dates:
        if el.text and el.text.strip():
            return el.text.strip()
    return None


def _strip_tags(raw: bytes) -> str:
    text = _TAG_RE.sub(b" ", raw).decode("utf-8", errors="replace")
    return _WS_RE.sub(" ", text).strip()
