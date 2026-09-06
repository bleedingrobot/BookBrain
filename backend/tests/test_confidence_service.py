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


def test_series_disagreement_penalized_on_a_provider_consensus() -> None:
    # Two providers agree on a series the EPUB doesn't mention — a genuine
    # consensus pointing elsewhere.
    candidates = [
        MetadataCandidate(title="Dune", authors=["Frank Herbert"], series="Wrong Series", source="a"),
        MetadataCandidate(title="Dune", authors=["Frank Herbert"], series="Wrong Series", source="b"),
    ]
    breakdown = score(
        evidence=_evidence(series="Dune Chronicles"), candidates=candidates, filename="dune.epub"
    )

    assert breakdown.conflicts["series_disagreement"] == -10


def test_lone_provider_series_does_not_contradict_a_correct_epub_series() -> None:
    # prompts/15 Stage B: provider series are messy/sometimes wrong. One
    # provider disagreeing with the EPUB's own series must not tank confidence.
    candidates = [
        MetadataCandidate(
            title="Dune", authors=["Frank Herbert"], series="Some Wrong Series", source="a"
        )
    ]
    breakdown = score(
        evidence=_evidence(series="Dune Chronicles"), candidates=candidates, filename="dune.epub"
    )

    assert "series_disagreement" not in breakdown.conflicts


def test_two_providers_split_on_series_is_not_a_consensus_disagreement() -> None:
    candidates = [
        MetadataCandidate(title="Dune", authors=["Frank Herbert"], series="Series X", source="a"),
        MetadataCandidate(title="Dune", authors=["Frank Herbert"], series="Series Y", source="b"),
    ]
    breakdown = score(
        evidence=_evidence(series="Dune Chronicles"), candidates=candidates, filename="dune.epub"
    )

    assert "series_disagreement" not in breakdown.conflicts


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


def test_provider_dropping_leading_article_is_not_a_disagreement() -> None:
    # Regression: library catalogs (Open Library, Google Books) commonly
    # strip a leading "The"/"A"/"An" from titles. That must not be scored
    # as a provider/EPUB disagreement.
    evidence = _evidence(title="The Winner's Crime", authors=["Marie Rutkoski"])
    candidates = [
        MetadataCandidate(
            title="Winner's Crime", authors=["Marie Rutkoski"], source="open_library"
        )
    ]

    breakdown = score(
        evidence=evidence, candidates=candidates, filename="the_winners_crime.epub"
    )

    assert breakdown.conflicts == {}
    assert breakdown.components["provider_matches_epub"] == 20


def test_series_dropping_leading_article_is_not_a_disagreement() -> None:
    evidence = _evidence(series="The Expanse")
    candidates = [
        MetadataCandidate(title="Dune", authors=["Frank Herbert"], series="Expanse", source="a")
    ]

    breakdown = score(evidence=evidence, candidates=candidates, filename="dune.epub")

    assert "series_disagreement" not in breakdown.conflicts


def test_uncorroborated_series_penalized_only_when_opted_in() -> None:
    candidates = [
        MetadataCandidate(title="Dune", authors=["Frank Herbert"], source="a")
    ]

    # No resolved_series passed → historical/recompute callers unchanged.
    without = score(evidence=_evidence(), candidates=candidates, filename="dune.epub")
    assert "uncorroborated_series" not in without.conflicts

    # AI (or the fast-path series lookup) asserted a series no source mentions.
    with_penalty = score(
        evidence=_evidence(),
        candidates=candidates,
        filename="dune.epub",
        resolved_series="The Invented Chronicles",
    )
    assert with_penalty.conflicts["uncorroborated_series"] == -15
    assert without.total - with_penalty.total == 15


def test_series_backed_by_a_candidate_is_not_penalized() -> None:
    candidates = [
        MetadataCandidate(
            title="Dune", authors=["Frank Herbert"], series="Dune Chronicles", source="a"
        )
    ]

    breakdown = score(
        evidence=_evidence(),
        candidates=candidates,
        filename="dune.epub",
        resolved_series="dune chronicles",  # word-set match, not exact string
    )

    assert "uncorroborated_series" not in breakdown.conflicts


def test_series_backed_by_the_epub_is_not_penalized() -> None:
    breakdown = score(
        evidence=_evidence(series="The Expanse"),
        candidates=[],
        filename="dune.epub",
        resolved_series="Expanse (The)",
    )

    assert "uncorroborated_series" not in breakdown.conflicts


def test_provider_title_with_colon_series_suffix_is_not_a_disagreement() -> None:
    # Regression: Open Library returned two candidates for the same book —
    # one titled "Disquiet Gods : The Sun Eater", one titled "Disquiet
    # Gods" — which the scorer treated as disagreeing providers (-25) and
    # dropped the identification to confidence 20, despite the AI and every
    # other signal agreeing this was one unambiguous book.
    evidence = _evidence(title="Disquiet Gods", authors=["Christopher Ruocchio"], isbn13=None)
    candidates = [
        MetadataCandidate(
            title="Disquiet Gods : The Sun Eater",
            authors=["Christopher Ruocchio"],
            source="open_library",
        ),
        MetadataCandidate(
            title="Disquiet Gods", authors=["Christopher Ruocchio"], source="open_library"
        ),
    ]

    breakdown = score(
        evidence=evidence, candidates=candidates, filename="Disquiet Gods.epub"
    )

    assert "provider_disagreement" not in breakdown.conflicts
    assert breakdown.components["providers_agree"] == 15
