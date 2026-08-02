from app.providers.epub.parser import EpubEvidence
from app.services.quality_service import score_quality


def _evidence(**overrides) -> EpubEvidence:
    defaults = dict(
        title="Dune",
        authors=["Frank Herbert"],
        language="en",
        isbn13="9780441172719",
        text_snippet="It was a dark and stormy night.",
    )
    defaults.update(overrides)
    return EpubEvidence(**defaults)


def test_complete_evidence_and_reasonable_size_scores_100() -> None:
    assert score_quality(_evidence(), size_bytes=50_000) == 100


def test_empty_evidence_and_tiny_size_scores_zero() -> None:
    empty = EpubEvidence()
    assert score_quality(empty, size_bytes=100) == 0


def test_missing_text_snippet_drops_thirty_points() -> None:
    full = score_quality(_evidence(), size_bytes=50_000)
    without_snippet = score_quality(_evidence(text_snippet=""), size_bytes=50_000)
    assert full - without_snippet == 30


def test_tiny_file_drops_ten_points() -> None:
    full = score_quality(_evidence(), size_bytes=50_000)
    tiny = score_quality(_evidence(), size_bytes=1_000)
    assert full - tiny == 10


def test_missing_isbn_drops_fifteen_points() -> None:
    full = score_quality(_evidence(), size_bytes=50_000)
    without_isbn = score_quality(_evidence(isbn13=None), size_bytes=50_000)
    assert full - without_isbn == 15


def test_missing_authors_drops_fifteen_points() -> None:
    full = score_quality(_evidence(), size_bytes=50_000)
    without_authors = score_quality(_evidence(authors=[]), size_bytes=50_000)
    assert full - without_authors == 15
