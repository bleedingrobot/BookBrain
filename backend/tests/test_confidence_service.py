from app.providers.epub.parser import EpubEvidence
from app.providers.metadata.types import MetadataCandidate
from app.services.confidence_service import score


def _evidence(**overrides) -> EpubEvidence:
    defaults = dict(
        title="Dune",
        authors=["Frank Herbert"],
        language="en",
        isbn13="9780441172719",
    )
    defaults.update(overrides)
    return EpubEvidence(**defaults)


def test_full_agreement_with_ai_hits_max_when_two_providers_agree() -> None:
    candidates = [
        MetadataCandidate(title="Dune", authors=["Frank Herbert"], source="a"),
        MetadataCandidate(title="Dune", authors=["Frank Herbert"], source="b"),
    ]
    breakdown = score(
        evidence=_evidence(),
        candidates=candidates,
        filename="Dune - Frank Herbert.epub",
        ai_corroborates=True,
    )

    assert breakdown.total == 100
    assert breakdown.conflicts == {}


def test_deterministic_only_caps_at_95_without_ai() -> None:
    candidates = [
        MetadataCandidate(title="Dune", authors=["Frank Herbert"], source="a"),
        MetadataCandidate(title="Dune", authors=["Frank Herbert"], source="b"),
    ]
    breakdown = score(
        evidence=_evidence(),
        candidates=candidates,
        filename="Dune - Frank Herbert.epub",
        ai_corroborates=False,
    )

    assert breakdown.total == 95


def test_no_candidates_scores_low() -> None:
    breakdown = score(evidence=_evidence(), candidates=[], filename="dune.epub")

    # isbn (40) + epub_complete (15) + filename_match (5) — nothing else
    assert breakdown.total == 60


def test_missing_isbn_drops_forty_points() -> None:
    with_isbn = score(evidence=_evidence(), candidates=[], filename="dune.epub")
    without_isbn = score(
        evidence=_evidence(isbn13=None), candidates=[], filename="dune.epub"
    )

    assert with_isbn.total - without_isbn.total == 40


def test_provider_disagreement_penalized() -> None:
    candidates = [
        MetadataCandidate(title="Dune", authors=["Frank Herbert"], source="a"),
        MetadataCandidate(title="Dune Messiah", authors=["Frank Herbert"], source="b"),
    ]
    breakdown = score(evidence=_evidence(), candidates=candidates, filename="dune.epub")

    assert breakdown.conflicts["provider_disagreement"] == -25


def test_epub_provider_disagreement_penalized() -> None:
    candidates = [
        MetadataCandidate(title="Something Else Entirely", authors=["Someone"], source="a"),
    ]
    breakdown = score(evidence=_evidence(), candidates=candidates, filename="dune.epub")

    assert breakdown.conflicts["epub_provider_disagreement"] == -15
    assert breakdown.components["provider_matches_epub"] == 0


def test_series_disagreement_penalized() -> None:
    candidates = [
        MetadataCandidate(
            title="Dune", authors=["Frank Herbert"], series="Wrong Series", source="a"
        )
    ]
    breakdown = score(
        evidence=_evidence(series="Dune Chronicles"), candidates=candidates, filename="dune.epub"
    )

    assert breakdown.conflicts["series_disagreement"] == -10


def test_score_never_goes_below_zero() -> None:
    candidates = [
        MetadataCandidate(title="A", authors=["X"], series="S1", source="a"),
        MetadataCandidate(title="B", authors=["Y"], series="S2", source="b"),
    ]
    breakdown = score(
        evidence=_evidence(title="Unrelated Title", isbn13=None, series="S3"),
        candidates=candidates,
        filename="whatever.epub",
    )

    assert breakdown.total == 0


def test_filename_match_is_case_and_punctuation_insensitive() -> None:
    breakdown = score(
        evidence=_evidence(), candidates=[], filename="DUNE!!! (Frank Herbert).epub"
    )

    assert breakdown.components["filename_matches_title"] == 5
