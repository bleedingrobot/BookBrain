class EpubParseError(Exception):
    """Base for all EPUB parsing failures — malformed structure, missing
    required files, or safety limits exceeded."""


class EpubTooLargeError(EpubParseError):
    pass


class EpubTooManyEntriesError(EpubParseError):
    pass


class EpubParseTimeoutError(EpubParseError):
    pass


class EpubWriteError(Exception):
    """Raised by the metadata writer when an EPUB can't be safely rewritten —
    unreadable zip, missing/invalid container.xml or OPF, no <metadata>
    element. Distinct from EpubParseError: the read pipeline tolerates a
    parse failure (the book still lands as `unidentified`), but a write
    failure means we leave the original file completely untouched."""
