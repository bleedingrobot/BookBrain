def classify_file(raw: dict) -> str | None:
    """SPEC.md §1: single-parent is an enforced invariant, not an assumption.
    Returns a files.status_reason value, or None if the file is well-formed."""
    parents = raw.get("parents") or []
    if len(parents) > 1:
        return "multi_parent"
    if len(parents) == 0:
        return "no_parent"
    return None


_SUPPORTED_EBOOK_EXTENSIONS = (".epub", ".kpub")


def is_supported_ebook(filename: str) -> bool:
    """.kpub (Kobo) doesn't have a reliable Drive mimeType, so both formats
    are recognized by filename extension rather than mimeType."""
    return filename.lower().endswith(_SUPPORTED_EBOOK_EXTENSIONS)
