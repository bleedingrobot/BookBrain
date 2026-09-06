import pytest

from app.services.metadata_sanity import (
    clamp_series_number,
    looks_like_placeholder_author,
    looks_like_placeholder_title,
    sane_series_number,
)


def test_no_series_forces_number_to_none() -> None:
    assert sane_series_number(None, 3) is None
    assert sane_series_number("", 3) is None


def test_absurd_numbers_are_dropped_series_kept() -> None:
    assert sane_series_number("Alexis Carew", 301) is None
    assert sane_series_number("Some Series", -1) is None
    assert sane_series_number("Some Series", 0) is None


def test_legitimate_numbers_survive() -> None:
    assert sane_series_number("Discworld", 39) == 39
    assert sane_series_number("Discworld", 39.5) == 39.5
    assert sane_series_number("Edge Case", 50) == 50
    assert sane_series_number("Nothing", None) is None


def test_clamp_records_original_and_only_when_changed() -> None:
    raw: dict = {}
    assert clamp_series_number("Alexis Carew", 301, raw) is None
    assert raw["series_number_clamped"] == 301

    raw2: dict = {}
    assert clamp_series_number("Discworld", 5, raw2) == 5
    assert "series_number_clamped" not in raw2


@pytest.mark.parametrize(
    "title",
    ["", "  ", "Unknown", "unknown title", "Calibre", "epub", "book1", "Book 3",
     "Volume 2", "12345", "untitled", "New Document", "??"],
)
def test_placeholder_titles(title) -> None:
    assert looks_like_placeholder_title(title)


@pytest.mark.parametrize("title", ["The Way of Kings", "Dune", "1984", "S."])
def test_real_titles_are_not_placeholders(title) -> None:
    # "1984" and "S." only pass with corroboration (an ISBN / provider match).
    assert not looks_like_placeholder_title(title, corroborated=True)


def test_short_title_needs_corroboration() -> None:
    assert looks_like_placeholder_title("It")  # bare, no ISBN/provider
    assert not looks_like_placeholder_title("It", corroborated=True)


@pytest.mark.parametrize(
    "author",
    ["", "Unknown", "Anonymous", "Various", "various authors", "Author Unknown",
     "n/a", "Tor Books", "Penguin", "Smashwords", "admin"],
)
def test_placeholder_authors(author) -> None:
    assert looks_like_placeholder_author(author)


@pytest.mark.parametrize("author", ["Brandon Sanderson", "J. R. R. Tolkien", "Le Guin, Ursula K."])
def test_real_authors_are_not_placeholders(author) -> None:
    assert not looks_like_placeholder_author(author)
