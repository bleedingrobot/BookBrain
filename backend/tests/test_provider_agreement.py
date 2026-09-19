from app.providers.metadata.types import MetadataCandidate
from app.services.provider_agreement import compute_agreement


def test_no_candidates_yields_no_agreement() -> None:
    assert compute_agreement([]) == {}


def test_single_candidate_never_agrees_with_itself() -> None:
    candidates = [MetadataCandidate(title="Dune", authors=["Frank Herbert"], source="a")]
    assert compute_agreement(candidates) == {}


def test_two_of_three_agree_on_title_and_author() -> None:
    candidates = [
        MetadataCandidate(title="Dune", authors=["Frank Herbert"], source="a"),
        MetadataCandidate(title="Dune", authors=["Frank Herbert"], source="b"),
        MetadataCandidate(title="Something Else", authors=["Nobody"], source="c"),
    ]
    agreement = compute_agreement(candidates)

    assert agreement["title"].value == "Dune"
    assert agreement["title"].provider_count == 2
    assert agreement["title"].sources == ("a", "b")
    assert agreement["authors"].value == "Frank Herbert"
    assert agreement["authors"].provider_count == 2


def test_cosmetic_title_differences_still_agree() -> None:
    # normalize_title strips a leading article and any colon/paren subtitle,
    # so these count as the same value even though the raw strings differ.
    candidates = [
        MetadataCandidate(title="The Hobbit", source="a"),
        MetadataCandidate(title="Hobbit: There and Back Again", source="b"),
    ]
    agreement = compute_agreement(candidates)

    assert agreement["title"].provider_count == 2


def test_series_agreement_is_order_independent() -> None:
    candidates = [
        MetadataCandidate(series="The Wheel of Time", source="a"),
        MetadataCandidate(series="Wheel of Time, The", source="b"),
    ]
    agreement = compute_agreement(candidates)

    assert "series" in agreement
    assert agreement["series"].provider_count == 2


def test_genre_agreement_is_case_and_whitespace_insensitive() -> None:
    candidates = [
        MetadataCandidate(genre="  Science Fiction ", source="a"),
        MetadataCandidate(genre="science fiction", source="b"),
    ]
    agreement = compute_agreement(candidates)

    assert agreement["genre"].value == "  Science Fiction "
    assert agreement["genre"].provider_count == 2


def test_no_agreement_when_candidates_disagree() -> None:
    candidates = [
        MetadataCandidate(title="Dune", source="a"),
        MetadataCandidate(title="Foundation", source="b"),
    ]
    assert compute_agreement(candidates) == {}


def test_missing_field_on_all_candidates_is_skipped() -> None:
    candidates = [
        MetadataCandidate(title="Dune", source="a"),
        MetadataCandidate(title="Dune", source="b"),
    ]
    agreement = compute_agreement(candidates)

    assert "series" not in agreement
    assert "genre" not in agreement
