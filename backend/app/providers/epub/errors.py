class EpubParseError(Exception):
    """Base for all EPUB parsing failures — malformed structure, missing
    required files, or safety limits exceeded."""


class EpubTooLargeError(EpubParseError):
    pass


class EpubTooManyEntriesError(EpubParseError):
    pass


class EpubParseTimeoutError(EpubParseError):
    pass
