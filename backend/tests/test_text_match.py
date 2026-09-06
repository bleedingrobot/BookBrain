from app.services.text_match import (
    normalize,
    normalize_title,
    normalize_title_strict,
    texts_match,
    title_similarity,
    titles_match,
)


def test_title_similarity_separates_same_series_different_book() -> None:
    # Both pass titles_match (colon-strip) but are different books.
    assert titles_match("Mistborn: The Final Empire", "Mistborn: The Well of Ascension")
    assert title_similarity("Mistborn: The Final Empire", "Mistborn: The Well of Ascension") < 0.8


def test_title_similarity_tolerates_a_leading_article() -> None:
    assert title_similarity("The Hobbit", "Hobbit") == 1.0
    assert title_similarity("Dune", "Dune") == 1.0


def test_title_similarity_empty() -> None:
    assert title_similarity(None, "x") == 0.0
    assert title_similarity("x", "") == 0.0


import pytest

from app.services.text_match import normalize_person_name, person_sort_name


@pytest.mark.parametrize(
    "a,b",
    [
        ("J.R.R. Tolkien", "J. R. R. Tolkien"),
        ("J.R.R. Tolkien", "Tolkien, J.R.R."),
        ("J. R. R. Tolkien", "Tolkien, J. R. R."),
        ("Iain M. Banks", "Iain Banks"),
        ("Ursula K. Le Guin", "Le Guin, Ursula K."),
        ("Ursula K. Le Guin", "Ursula Le Guin"),
        ("Brandon Sanderson", "Sanderson, Brandon"),
        ("Margaret Weis & Tracy Hickman", "Margaret Weis"),
        ("Weis, Margaret", "Margaret Weis"),
    ],
)
def test_normalize_person_name_unifies_variants(a, b) -> None:
    assert normalize_person_name(a) == normalize_person_name(b)


@pytest.mark.parametrize(
    "a,b",
    [
        ("James Smith", "Jane Smith"),
        ("George R. R. Martin", "George Martin"),
        ("Frank Herbert", "Brian Herbert"),
    ],
)
def test_normalize_person_name_keeps_distinct_people_distinct(a, b) -> None:
    assert normalize_person_name(a) != normalize_person_name(b)


def test_person_sort_name() -> None:
    assert person_sort_name("Brandon Sanderson") == "Sanderson, Brandon"
    assert person_sort_name("Ursula K. Le Guin") == "Le Guin, Ursula K."
    assert person_sort_name("Sanderson, Brandon") == "Sanderson, Brandon"
    assert person_sort_name("Plato") == "Plato"
    assert person_sort_name("") == ""


def test_normalize_strips_punctuation_and_case() -> None:
    assert normalize("Dune: House Atreides!") == "dunehouseatreides"


def test_normalize_does_not_strip_leading_article() -> None:
    assert normalize("The Winner's Crime") == "thewinnerscrime"


def test_normalize_title_strips_leading_the() -> None:
    assert normalize_title("The Winner's Crime") == "winnerscrime"


def test_normalize_title_strips_leading_a_and_an() -> None:
    assert normalize_title("A Study in Scarlet") == "studyinscarlet"
    assert normalize_title("An Unexpected Journey") == "unexpectedjourney"


def test_normalize_title_only_strips_leading_article_not_mid_string() -> None:
    assert normalize_title("Gone with the Wind") == "gonewiththewind"


def test_normalize_title_empty() -> None:
    assert normalize_title(None) == ""
    assert normalize_title("") == ""


def test_titles_match_ignores_leading_article_difference() -> None:
    assert titles_match("The Winner's Crime", "Winner's Crime") is True


def test_texts_match_is_strict_about_leading_article() -> None:
    # texts_match is the generic (non-title-aware) comparator — used for
    # authors, where article-stripping would be wrong.
    assert texts_match("The Winner's Crime", "Winner's Crime") is False


def test_titles_match_still_requires_real_agreement() -> None:
    assert titles_match("The Winner's Crime", "The Winner's Curse") is False


def test_normalize_title_strips_colon_separated_series_suffix() -> None:
    assert normalize_title("Disquiet Gods : The Sun Eater") == "disquietgods"


def test_normalize_title_strips_semicolon_separated_suffix() -> None:
    assert normalize_title("Disquiet Gods; The Sun Eater") == "disquietgods"


def test_titles_match_ignores_provider_subtitle_suffix() -> None:
    # Regression: Open Library sometimes returns "Title : Series Name" for
    # one candidate and just "Title" for another record of the same book —
    # that's not a real title disagreement.
    assert titles_match("Disquiet Gods : The Sun Eater", "Disquiet Gods") is True


def test_normalize_title_does_not_strip_mid_word_colon_free_text() -> None:
    assert normalize_title("Gone with the Wind") == "gonewiththewind"


def test_normalize_title_strips_trailing_parenthetical_series_suffix() -> None:
    assert normalize_title("A Reaper at the Gates (An Ember in the Ashes 03)") == "reaperatthegates"
    assert (
        normalize_title("A Reaper at the Gates (An Ember in the Ashes Book 3)")
        == "reaperatthegates"
    )


def test_normalize_title_strips_multiple_trailing_parentheticals() -> None:
    assert normalize_title("Some Title (Unabridged) (Book 3)") == "sometitle"


def test_normalize_title_does_not_strip_non_trailing_parens() -> None:
    # Only a parenthetical at the very end is treated as a series/edition
    # suffix — one in the middle of the title is part of the title itself.
    assert normalize_title("The Hobbit (Illustrated) Edition") == "hobbitillustratededition"


def test_titles_match_ignores_differently_formatted_series_suffixes() -> None:
    # Regression: an EPUB embedding "(Series Name 03)" in its title field, a
    # provider embedding "(Series Name Book 3)", and an AI returning the
    # clean short title are all the same book, not three disagreeing ones.
    assert titles_match(
        "A Reaper at the Gates (An Ember in the Ashes 03)",
        "A Reaper at the Gates (An Ember in the Ashes Book 3)",
    ) is True
    assert titles_match(
        "A Reaper at the Gates (An Ember in the Ashes 03)", "A Reaper at the Gates"
    ) is True


def test_normalize_title_strict_keeps_colon_subtitle() -> None:
    # The distinguishing part of "<Series>: <Book>" titles is *after* the
    # colon — the strict normalizer must keep it so two different books in a
    # series don't collapse onto one key.
    assert normalize_title_strict("Mistborn: The Final Empire") == "mistbornthefinalempire"
    assert (
        normalize_title_strict("Mistborn: The Well of Ascension")
        == "mistbornthewellofascension"
    )
    assert normalize_title_strict("Mistborn: The Final Empire") != normalize_title_strict(
        "Mistborn: The Well of Ascension"
    )


def test_normalize_title_strict_still_folds_case_article_and_trailing_parens() -> None:
    assert normalize_title_strict("The Hob's Bargain") == normalize_title_strict("The Hob's bargain")
    assert normalize_title_strict("A Study in Scarlet") == "studyinscarlet"
    assert (
        normalize_title_strict("Heir to the Empire (Thrawn Trilogy 1)")
        != normalize_title_strict("Dark Force Rising (Thrawn Trilogy 2)")
    )


def test_normalize_title_strict_does_not_merge_series_prefix_books() -> None:
    assert normalize_title_strict("Star Wars: Heir to the Empire") != normalize_title_strict(
        "Star Wars: Dark Force Rising"
    )


def test_normalize_title_strict_empty() -> None:
    assert normalize_title_strict(None) == ""
    assert normalize_title_strict("") == ""
