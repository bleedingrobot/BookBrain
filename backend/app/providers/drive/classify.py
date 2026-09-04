def classify_file(raw: dict) -> str | None:
    """SPEC.md §1: single-parent is an enforced invariant, not an assumption.
    Returns a files.status_reason value, or None if the file is well-formed."""
    parents = raw.get("parents") or []
    if len(parents) > 1:
        return "multi_parent"
    if len(parents) == 0:
        return "no_parent"
    return None


_SUPPORTED_EBOOK_EXTENSIONS = (".epub", ".kpub", ".cbz")


def is_supported_ebook(filename: str) -> bool:
    """Files BookBrain ingests as-is: EPUB, Kobo .kpub, and .cbz comic
    archives. All recognized by filename extension rather than Drive's
    mimeType, which is unreliable for .kpub and .cbz alike."""
    return filename.lower().endswith(_SUPPORTED_EBOOK_EXTENSIONS)
