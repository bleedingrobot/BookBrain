from app.providers.epub.parser import EpubEvidence

TITLE_PRESENT = 20
AUTHOR_PRESENT = 15
TEXT_SNIPPET_PRESENT = 30
LANGUAGE_PRESENT = 10
ISBN_PRESENT = 15
REASONABLE_SIZE = 10

# Below this, the file is almost certainly a stub, a cover-only extract, or
# corrupted — not a full book.
MIN_REASONABLE_SIZE_BYTES = 10_000


def score_quality(evidence: EpubEvidence, size_bytes: int) -> int:
    """A from-scratch heuristic (no spec definition existed for this) for
    the *file's own* completeness — distinct from identification confidence,
    which is about whether we know which book this is. Sums to 100; does not
    gate automation, only informs the Library/Duplicates UI."""
    total = 0
    if evidence.title:
        total += TITLE_PRESENT
    if evidence.authors:
        total += AUTHOR_PRESENT
    if evidence.text_snippet:
        total += TEXT_SNIPPET_PRESENT
    if evidence.language:
        total += LANGUAGE_PRESENT
    if evidence.isbn13 or evidence.isbn10:
        total += ISBN_PRESENT
    if size_bytes >= MIN_REASONABLE_SIZE_BYTES:
        total += REASONABLE_SIZE
    return total
