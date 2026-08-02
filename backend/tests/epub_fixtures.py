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
