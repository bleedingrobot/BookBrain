import io
import zipfile


def build_epub(
    *,
    title: str = "Test Book",
    authors: tuple[str, ...] = ("Jane Author",),
    language: str = "en",
    isbn: str | None = "9780134685991",
    series: str | None = None,
    series_number: float | None = None,
    chapter_text: str = "<html><body><p>Chapter one text here.</p></body></html>",
    container_xml: str | None = None,
    opf_xml: str | None = None,
    extra_files: dict[str, str] | None = None,
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr(
            "META-INF/container.xml",
            container_xml
            or """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>""",
        )

        meta_extra = ""
        if series:
            meta_extra += f'<meta name="calibre:series" content="{series}"/>'
        if series_number is not None:
            meta_extra += f'<meta name="calibre:series_index" content="{series_number}"/>'
        creators = "".join(f"<dc:creator>{a}</dc:creator>" for a in authors)
        identifier = f"<dc:identifier id=\"BookId\">urn:isbn:{isbn}</dc:identifier>" if isbn else ""

        zf.writestr(
            "OEBPS/content.opf",
            opf_xml
            or f"""<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="BookId">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{title}</dc:title>
    {creators}
    <dc:language>{language}</dc:language>
    {identifier}
    {meta_extra}
  </metadata>
  <manifest>
    <item id="chap1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="chap1"/>
  </spine>
</package>""",
        )
        zf.writestr("OEBPS/chapter1.xhtml", chapter_text)

        for name, content in (extra_files or {}).items():
            zf.writestr(name, content)

    return buf.getvalue()


def build_rich_epub(
    *,
    title: str = "Test Book",
    authors: tuple[str, ...] = ("Jane Author",),
    language: str = "en",
    description: str | None = None,
    publisher: str | None = None,
    pub_date: str | None = None,
    subjects: tuple[str, ...] = (),
    identifiers: tuple[str, ...] = (),
    source: str | None = None,
    spine: tuple[tuple[str, str], ...] = (),
    manifest_extra_props: dict[str, str] | None = None,
) -> bytes:
    """A fuller EPUB for the Stage-D text/metadata tests: arbitrary <dc:*>
    metadata, multiple <dc:identifier>/<dc:source>, and a multi-document spine
    (``spine`` = ``((filename, inner_html), ...)`` in reading order).
    ``manifest_extra_props`` maps a spine filename to a ``properties=`` value
    (e.g. ``"nav"``)."""
    manifest_extra_props = manifest_extra_props or {}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>""",
        )

        meta = [f"<dc:title>{title}</dc:title>"]
        meta += [f"<dc:creator>{a}</dc:creator>" for a in authors]
        meta.append(f"<dc:language>{language}</dc:language>")
        if description:
            meta.append(f"<dc:description>{description}</dc:description>")
        if publisher:
            meta.append(f"<dc:publisher>{publisher}</dc:publisher>")
        if pub_date:
            meta.append(f"<dc:date>{pub_date}</dc:date>")
        meta += [f"<dc:subject>{s}</dc:subject>" for s in subjects]
        meta += [f'<dc:identifier id="id{i}">{v}</dc:identifier>' for i, v in enumerate(identifiers)]
        if source:
            meta.append(f"<dc:source>{source}</dc:source>")

        docs = spine or (("chapter1.xhtml", "<p>Chapter one text here.</p>"),)
        manifest_items = []
        spine_items = []
        for idx, (fname, _) in enumerate(docs):
            props = manifest_extra_props.get(fname)
            prop_attr = f' properties="{props}"' if props else ""
            manifest_items.append(
                f'<item id="d{idx}" href="{fname}" media-type="application/xhtml+xml"{prop_attr}/>'
            )
            spine_items.append(f'<itemref idref="d{idx}"/>')

        zf.writestr(
            "OEBPS/content.opf",
            f"""<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" xmlns:opf="http://www.idpf.org/2007/opf"
         version="3.0" unique-identifier="id0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    {"".join(meta)}
  </metadata>
  <manifest>
    {"".join(manifest_items)}
  </manifest>
  <spine>
    {"".join(spine_items)}
  </spine>
</package>""",
        )
        for fname, inner in docs:
            zf.writestr(f"OEBPS/{fname}", f"<html><body>{inner}</body></html>")

    return buf.getvalue()
