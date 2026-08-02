from app.services.text_match import normalize, normalize_title, texts_match, titles_match


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
