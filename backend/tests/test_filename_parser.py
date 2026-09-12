"""prompts/15 Stage C — structured inbound-filename parsing.

The fixture list below is a spread of real-world naming conventions (tracker
dumps, Calibre exports, libgen, Anna's Archive, "Last, First" catalogues). Add
messier real names from the inbox here as they turn up.
"""

import pytest

from app.providers.filename.parser import (
    FILENAME_GUESS_MIN_CONFIDENCE,
    parse_book_filename,
)

# (filename, expected title, author, series, series_number)  — None = "don't care / absent"
CASES = [
    (
        "Sanderson, Brandon - Mistborn 01 - The Final Empire (2006).epub",
        "The Final Empire", "Brandon Sanderson", "Mistborn", 1.0,
    ),
    (
        "Pratchett, Terry - Discworld 08 - Guards! Guards!.epub",
        "Guards! Guards!", "Terry Pratchett", "Discworld", 8.0,
    ),
    ("Brandon Sanderson - The Final Empire.epub", "The Final Empire", "Brandon Sanderson", None, None),
    ("The Final Empire - Brandon Sanderson.epub", "The Final Empire", "Brandon Sanderson", None, None),
    ("J. R. R. Tolkien - The Hobbit.epub", "The Hobbit", "J. R. R. Tolkien", None, None),
    ("Rothfuss, Patrick - The Name of the Wind.epub", "The Name of the Wind", "Patrick Rothfuss", None, None),
    ("Leviathan Wakes (The Expanse Book 1).epub", "Leviathan Wakes", None, "The Expanse", 1.0),
    ("Isaac Asimov - Foundation (Foundation #1).epub", "Foundation", "Isaac Asimov", "Foundation", 1.0),
    (
        "The Fellowship of the Ring - J.R.R. Tolkien (The Lord of the Rings, Book 1).epub",
        "The Fellowship of the Ring", "J.R.R. Tolkien", "The Lord of the Rings", 1.0,
    ),
    ("Andy Weir - Project Hail Mary (2021) (Z-Library).epub", "Project Hail Mary", "Andy Weir", None, None),
]


@pytest.mark.parametrize("name,title,author,series,number", CASES)
def test_parses_common_patterns(name, title, author, series, number) -> None:
    g = parse_book_filename(name)
    assert g.title == title
    if author is not None:
        assert g.author == author
    assert g.series == series
    assert g.series_number == number
    assert g.usable


def test_calibre_id_suffix_is_not_a_series_number() -> None:
    g = parse_book_filename("The Final Empire - Brandon Sanderson_1234.epub")
    assert g.title == "The Final Empire"
    assert g.author == "Brandon Sanderson"
    assert g.series is None
    assert g.series_number is None


def test_calibre_placeholder_absurd_number_is_dropped() -> None:
    # "Wronged - A Story of the Dark (Alexis Carew #301)" — #301 is a Calibre
    # sorting placeholder, not the 301st volume. Never emit it.
    g = parse_book_filename("Wronged - A Story of the Dark (Alexis Carew #301).epub")
    assert g.series_number is None
    assert g.series != "Alexis Carew"  # the junk parenthetical is stripped, not kept


def test_bare_title_is_low_confidence() -> None:
    for name in ("The Way of Kings.epub", "It.epub", "Dune (1965).epub", "book.epub"):
        g = parse_book_filename(name)
        assert g.confidence < FILENAME_GUESS_MIN_CONFIDENCE
        assert not g.usable


def test_year_is_extracted_only_when_enclosed() -> None:
    assert parse_book_filename("Andy Weir - The Martian (2011).epub").year == 2011
    # a bare number is far more likely the title than a year
    assert parse_book_filename("George Orwell - 1984.epub").year is None


def test_site_tags_are_stripped() -> None:
    g = parse_book_filename("Neuromancer - William Gibson [libgen.li] (Z-Library).epub")
    assert g.title == "Neuromancer"
    assert g.author == "William Gibson"


def test_empty_and_junk_names_do_not_raise() -> None:
    assert parse_book_filename("").title is None
    assert parse_book_filename(".epub").title is None
    assert parse_book_filename("   ").title is None


def test_underscores_become_spaces_and_lowercase_names_are_title_cased() -> None:
    g = parse_book_filename("brandon_sanderson_-_the_final_empire.epub")
    assert g.title == "The Final Empire"
    assert g.author == "Brandon Sanderson"
