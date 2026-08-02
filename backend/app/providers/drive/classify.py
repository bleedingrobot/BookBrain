def classify_file(raw: dict) -> str | None:
    """SPEC.md §1: single-parent is an enforced invariant, not an assumption.
    Returns a files.status_reason value, or None if the file is well-formed."""
    parents = raw.get("parents") or []
    if len(parents) > 1:
        return "multi_parent"
    if len(parents) == 0:
        return "no_parent"
    return None
