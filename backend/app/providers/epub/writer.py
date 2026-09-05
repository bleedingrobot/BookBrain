"""Write BookBrain's resolved title / author / series (and, if missing, a
cover) back into an `.epub`'s OPF package document.

SPEC.md §3 originally deferred this ("no EPUB metadata repair/writing —
read-only parsing only"); it's now supported so a Kobo (and Calibre, and
every other reader) shows the metadata BookBrain resolved instead of the
often-messy original the misidentification came from.

`write_metadata` is a pure function — bytes in, bytes out, no I/O — so it
can be exhaustively unit-tested. It:

* rewrites the OPF `<metadata>`: one `dc:title`, one `dc:creator`, the
  legacy Calibre `calibre:series` / `calibre:series_index` pair AND the
  EPUB 3 `belongs-to-collection` group (Kobo historically reads the
  Calibre tags — both are written so old and new readers agree);
* embeds a cover ONLY when the epub genuinely lacks a usable one — an
  existing, resolvable cover image is left exactly as it is;
* copies every other zip entry through byte-for-byte, keeps `mimetype`
  first and stored, and never touches content files.
"""

import io
import posixpath
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime

from defusedxml import ElementTree as DET
from defusedxml.common import DefusedXmlException

from app.providers.epub.errors import EpubWriteError
from app.providers.epub.parser import (
    CONTAINER_NS,
    DC_NS,
    OPF_NS,
    _cover_href_by_name,
    _cover_href_from_meta,
    _cover_href_from_properties,
)

DCTERMS_NS = "http://purl.org/dc/terms/"

# A prefix (not the default namespace) is registered for the OPF namespace
# on purpose: XML has no way to put an *attribute* in the default namespace,
# so `opf:scheme` / `opf:role` / `opf:file-as` on elements we don't touch
# would be silently dropped if the package used a default `xmlns`. The cost
# is `<opf:package>` instead of `<package>` in the output — valid EPUB, read
# fine by ebooklib, Calibre and Kobo.
ET.register_namespace("opf", OPF_NS)
ET.register_namespace("dc", DC_NS)
ET.register_namespace("dcterms", DCTERMS_NS)

_SERIES_COLLECTION_ID = "bookbrain-series"
_COVER_ITEM_ID = "bookbrain-cover-image"
_COVER_BASENAME = "bookbrain-cover"

_META = f"{{{OPF_NS}}}meta"
_ITEM = f"{{{OPF_NS}}}item"

# meta[@property] values that make up an EPUB 3 series/collection group. We
# clear every one before writing our own — BookBrain's library is
# Calibre-origin with a single flat series per book, so there's no nested
# sub-collection to preserve.
_COLLECTION_PROPS = {"belongs-to-collection", "collection-type", "group-position"}
_LEGACY_SERIES_NAMES = {"calibre:series", "calibre:series_index"}


@dataclass(frozen=True)
class EpubMetadata:
    """The resolved identity to stamp into the file. `author` / `series` may
    be None (a standalone book with an unknown author writes no `dc:creator`
    and no series tags rather than empty ones)."""

    title: str
    author: str | None = None
    series: str | None = None
    series_number: float | None = None


def write_metadata(
    epub_bytes: bytes, meta: EpubMetadata, cover_bytes: bytes | None = None
) -> bytes:
    """Return a new `.epub` with `meta` written into its OPF. Raises
    EpubWriteError if the archive can't be safely rewritten — callers must
    then leave the original file untouched."""
    try:
        zin = zipfile.ZipFile(io.BytesIO(epub_bytes))
    except zipfile.BadZipFile as exc:
        raise EpubWriteError("not a valid zip archive") from exc

    names = set(zin.namelist())
    opf_path = _find_opf_path(names, zin)
    opf_dir = posixpath.dirname(opf_path)

    root = _parse_xml(zin.read(opf_path), "OPF package document")
    if not root.tag.endswith("}package") and root.tag != "package":
        raise EpubWriteError(f"OPF root is <{root.tag}>, not <package>")

    version = root.get("version", "")
    metadata_el = root.find(f"{{{OPF_NS}}}metadata")
    if metadata_el is None:
        raise EpubWriteError("OPF has no <metadata> element")
    manifest_el = root.find(f"{{{OPF_NS}}}manifest")
    items = manifest_el.findall(_ITEM) if manifest_el is not None else []

    _set_title(metadata_el, meta.title)
    _set_creator(metadata_el, meta.author)
    _clear_series(metadata_el)
    if meta.series:
        _set_series(metadata_el, meta.series, meta.series_number)

    extra_entry: tuple[str, bytes] | None = None
    if cover_bytes and manifest_el is not None and not _has_usable_cover(root, items, opf_dir, names):
        extra_entry = _inject_cover(metadata_el, manifest_el, items, opf_dir, cover_bytes, names)

    _touch_modified(metadata_el, version)

    new_opf = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return _rewrite_zip(epub_bytes, opf_path, new_opf, extra_entry)


# --------------------------------------------------------------------------
# OPF editing
# --------------------------------------------------------------------------


def _set_title(metadata_el: ET.Element, title: str) -> None:
    existing = metadata_el.findall(f"{{{DC_NS}}}title")
    if existing:
        existing[0].text = title
        for extra in existing[1:]:
            metadata_el.remove(extra)
        return
    el = ET.Element(f"{{{DC_NS}}}title")
    el.text = title
    metadata_el.insert(0, el)


def _set_creator(metadata_el: ET.Element, author: str | None) -> None:
    if author is None:
        return
    for el in metadata_el.findall(f"{{{DC_NS}}}creator"):
        metadata_el.remove(el)
    el = ET.Element(f"{{{DC_NS}}}creator")
    el.text = author
    # After dc:title if there is one, else at the front.
    titles = metadata_el.findall(f"{{{DC_NS}}}title")
    pos = list(metadata_el).index(titles[-1]) + 1 if titles else 0
    metadata_el.insert(pos, el)


def _clear_series(metadata_el: ET.Element) -> None:
    for el in list(metadata_el):
        if el.tag != _META:
            continue
        if (
            el.get("name") in _LEGACY_SERIES_NAMES
            or el.get("property") in _COLLECTION_PROPS
            or el.get("refines") == f"#{_SERIES_COLLECTION_ID}"
        ):
            metadata_el.remove(el)


def _set_series(metadata_el: ET.Element, series: str, number: float | None) -> None:
    def meta(**attrs: str) -> ET.Element:
        el = ET.Element(_META)
        for k, v in attrs.items():
            el.set(k, v)
        return el

    # Legacy Calibre pair — what Kobo actually reads.
    metadata_el.append(meta(name="calibre:series", content=series))
    if number is not None:
        metadata_el.append(meta(name="calibre:series_index", content=_fmt_number(number)))

    # EPUB 3 collection group.
    collection = meta(property="belongs-to-collection", id=_SERIES_COLLECTION_ID)
    collection.text = series
    metadata_el.append(collection)
    ctype = meta(refines=f"#{_SERIES_COLLECTION_ID}", property="collection-type")
    ctype.text = "series"
    metadata_el.append(ctype)
    if number is not None:
        pos = meta(refines=f"#{_SERIES_COLLECTION_ID}", property="group-position")
        pos.text = _fmt_number(number)
        metadata_el.append(pos)


def _touch_modified(metadata_el: ET.Element, version: str) -> None:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    for el in metadata_el.findall(_META):
        if el.get("property") == "dcterms:modified":
            el.text = now
            return
    if version.startswith("3"):
        el = ET.Element(_META)
        el.set("property", "dcterms:modified")
        el.text = now
        metadata_el.append(el)


# --------------------------------------------------------------------------
# Cover
# --------------------------------------------------------------------------


def _has_usable_cover(root: ET.Element, items: list, opf_dir: str, names: set[str]) -> bool:
    href = (
        _cover_href_from_properties(items)
        or _cover_href_from_meta(root, items)
        or _cover_href_by_name(items)
    )
    if not href:
        return False
    return _resolve(opf_dir, href) in names


def _inject_cover(
    metadata_el: ET.Element,
    manifest_el: ET.Element,
    items: list,
    opf_dir: str,
    cover_bytes: bytes,
    names: set[str],
) -> tuple[str, bytes] | None:
    ext, mime = _image_kind(cover_bytes)
    if ext is None:
        return None  # not a JPEG/PNG — don't guess

    href = f"{_COVER_BASENAME}.{ext}"
    full = _resolve(opf_dir, href)
    n = 1
    while full in names:
        href = f"{_COVER_BASENAME}-{n}.{ext}"
        full = _resolve(opf_dir, href)
        n += 1

    # Drop any stale cover-image marker / EPUB2 <meta name="cover"> so ours
    # is unambiguously the one.
    for item in items:
        props = item.get("properties", "").split()
        if "cover-image" in props:
            props.remove("cover-image")
            if props:
                item.set("properties", " ".join(props))
            else:
                item.attrib.pop("properties", None)
    for el in metadata_el.findall(_META):
        if el.get("name") == "cover":
            metadata_el.remove(el)

    item = ET.Element(_ITEM)
    item.set("id", _COVER_ITEM_ID)
    item.set("href", href)
    item.set("media-type", mime)
    item.set("properties", "cover-image")
    manifest_el.append(item)

    cover_meta = ET.Element(_META)
    cover_meta.set("name", "cover")
    cover_meta.set("content", _COVER_ITEM_ID)
    metadata_el.append(cover_meta)

    return full, cover_bytes


def _image_kind(data: bytes) -> tuple[str | None, str]:
    if data[:3] == b"\xff\xd8\xff":
        return "jpg", "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png", "image/png"
    return None, ""


# --------------------------------------------------------------------------
# zip / container plumbing
# --------------------------------------------------------------------------


def _find_opf_path(names: set[str], zin: zipfile.ZipFile) -> str:
    if "META-INF/container.xml" not in names:
        raise EpubWriteError("missing META-INF/container.xml")
    root = _parse_xml(zin.read("META-INF/container.xml"), "container.xml")
    rootfile = root.find(f".//{{{CONTAINER_NS}}}rootfile")
    if rootfile is None or "full-path" not in rootfile.attrib:
        raise EpubWriteError("container.xml has no rootfile with a full-path")
    opf_path = rootfile.attrib["full-path"]
    if opf_path not in names:
        raise EpubWriteError(f"OPF file {opf_path!r} not found in archive")
    return opf_path


def _parse_xml(data: bytes, what: str) -> ET.Element:
    try:
        return DET.fromstring(data)
    except (DefusedXmlException, ET.ParseError) as exc:
        raise EpubWriteError(f"could not parse {what}: {exc}") from exc


def _rewrite_zip(
    original: bytes, opf_path: str, new_opf: bytes, extra: tuple[str, bytes] | None
) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(original))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zout:
        # EPUB OCF: `mimetype` must be the first entry and stored uncompressed.
        if "mimetype" in zin.namelist():
            mt = zipfile.ZipInfo("mimetype")
            mt.compress_type = zipfile.ZIP_STORED
            zout.writestr(mt, zin.read("mimetype"))

        for info in zin.infolist():
            if info.filename == "mimetype":
                continue
            if extra is not None and info.filename == extra[0]:
                continue  # replaced by the injected cover below
            data = new_opf if info.filename == opf_path else zin.read(info.filename)
            clone = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            clone.compress_type = info.compress_type
            clone.external_attr = info.external_attr
            clone.internal_attr = info.internal_attr
            clone.create_system = info.create_system
            zout.writestr(clone, data)

        if extra is not None:
            ci = zipfile.ZipInfo(extra[0])
            ci.compress_type = zipfile.ZIP_DEFLATED
            zout.writestr(ci, extra[1])

    return out.getvalue()


def _resolve(opf_dir: str, href: str) -> str:
    return posixpath.normpath(posixpath.join(opf_dir, href)) if opf_dir else href


def _fmt_number(number: float) -> str:
    return f"{number:g}"
