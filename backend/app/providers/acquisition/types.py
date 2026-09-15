from dataclasses import dataclass


@dataclass(frozen=True)
class AcquisitionResult:
    """A single search hit from an AcquisitionProvider. Field names match the
    original OpenBooks-only ``BookResult`` (now an alias for this class) so
    every existing consumer in acquisition_service.py keeps working
    unchanged — only ``provider`` is new, and it defaults so pre-existing
    construction sites don't need to change."""

    server: str | None
    author: str
    title: str
    format: str
    size: str
    full: str  # opaque handle, provider-specific; passed back into download()
    provider: str = "openbooks"
