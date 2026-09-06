from app.providers.ai.anthropic_client import AIIdentificationError
from app.providers.ai.types import AIIdentificationResult, AISeriesResult
from app.providers.epub.parser import EpubEvidence
from app.providers.metadata.types import MetadataCandidate
from app.services.identification_service import (
    IdentificationService,
    _build_prompt,
    should_ground,
)


class _FakeAIClient:
    model_name = "fake-model"

    def __init__(
        self,
        result: AIIdentificationResult | None = None,
        raises: bool = False,
        series_result: AISeriesResult | None = None,
        series_raises: bool = False,
    ) -> None:
        self._result = result
        self._raises = raises
        self._series_result = series_result or AISeriesResult(series=None, series_number=None)
        self._series_raises = series_raises
        self.prompts: list[str] = []
        self.ground_flags: list[bool] = []
        self.series_calls: list[tuple[str, str | None]] = []

    async def identify(self, prompt: str, *, ground: bool = False):
        self.prompts.append(prompt)
        self.ground_flags.append(ground)
        if self._raises:
            raise AIIdentificationError("simulated failure")
        return self._result, {"stop_reason": "tool_use"}

    async def identify_series(self, title: str, author: str | None):
        self.series_calls.append((title, author))
        if self._series_raises:
            raise AIIdentificationError("simulated series failure")
        return self._series_result, {"stop_reason": "tool_use"}


def _evidence(**overrides) -> EpubEvidence:
    defaults = dict(title="Dune", authors=["Frank Herbert"], language="en", isbn13="9780441172719")
    defaults.update(overrides)
    return EpubEvidence(**defaults)


async def test_fast_path_skips_full_identify_call() -> None:
    fake_client = _FakeAIClient()
    service = IdentificationService(ai_client=fake_client)
    candidates = [MetadataCandidate(title="Dune", authors=["Frank Herbert"], isbn13="9780441172719", source="a")]

    result = await service.identify(filename="dune.epub", evidence=_evidence(), candidates=candidates)

    assert result.model == "deterministic"
    assert result.title == "Dune"
    assert result.author == "Frank Herbert"
    assert fake_client.prompts == []  # full identify_book call never made


async def test_fast_path_enriches_series_when_missing_from_epub_and_providers() -> None:
    fake_client = _FakeAIClient(
        series_result=AISeriesResult(series="Dune Chronicles", series_number=1)
    )
    service = IdentificationService(ai_client=fake_client)
    candidates = [MetadataCandidate(title="Dune", authors=["Frank Herbert"], isbn13="9780441172719", source="a")]

    result = await service.identify(filename="dune.epub", evidence=_evidence(), candidates=candidates)

    assert result.model == "deterministic"
    assert result.series == "Dune Chronicles"
    assert result.series_number == 1
    assert fake_client.series_calls == [("Dune", "Frank Herbert")]
    assert "Dune Chronicles" in result.reasoning_summary


async def test_fast_path_skips_series_lookup_when_epub_already_has_it() -> None:
    fake_client = _FakeAIClient()
    service = IdentificationService(ai_client=fake_client)
    candidates = [MetadataCandidate(title="Dune", authors=["Frank Herbert"], isbn13="9780441172719", source="a")]

    result = await service.identify(
        filename="dune.epub", evidence=_evidence(series="Dune Chronicles"), candidates=candidates
    )

    assert result.series == "Dune Chronicles"
    assert fake_client.series_calls == []  # EPUB already had it — no AI call needed


async def test_fast_path_skips_series_lookup_when_a_provider_already_has_it() -> None:
    fake_client = _FakeAIClient()
    service = IdentificationService(ai_client=fake_client)
    candidates = [
        MetadataCandidate(
            title="Dune", authors=["Frank Herbert"], isbn13="9780441172719",
            series="Dune Chronicles", source="a",
        )
    ]

    result = await service.identify(filename="dune.epub", evidence=_evidence(), candidates=candidates)

    assert result.series == "Dune Chronicles"
    assert fake_client.series_calls == []


async def test_fast_path_series_lookup_failure_does_not_break_identification() -> None:
    fake_client = _FakeAIClient(series_raises=True)
    service = IdentificationService(ai_client=fake_client)
    candidates = [MetadataCandidate(title="Dune", authors=["Frank Herbert"], isbn13="9780441172719", source="a")]

    result = await service.identify(filename="dune.epub", evidence=_evidence(), candidates=candidates)

    assert result.model == "deterministic"
    assert result.series is None
    assert result.computed_confidence > 0


async def test_fast_path_not_taken_when_isbn_mismatched() -> None:
    fake_client = _FakeAIClient(
        result=AIIdentificationResult(
            title="Dune",
            author="Frank Herbert",
            series=None,
            series_number=None,
            ai_confidence=91,
            reasoning_summary="matched",
            needs_human_review=False,
        )
    )
    service = IdentificationService(ai_client=fake_client)
    candidates = [
        MetadataCandidate(title="Dune", authors=["Frank Herbert"], isbn13="0000000000000", source="a")
    ]

    result = await service.identify(filename="dune.epub", evidence=_evidence(), candidates=candidates)

    assert result.model == "fake-model"
    assert len(fake_client.prompts) == 1


async def test_ai_path_used_when_no_isbn_match() -> None:
    fake_client = _FakeAIClient(
        result=AIIdentificationResult(
            title="Dune",
            author="Frank Herbert",
            series="Dune Chronicles",
            series_number=1,
            ai_confidence=88,
            reasoning_summary="Matched via text analysis.",
            needs_human_review=False,
        )
    )
    service = IdentificationService(ai_client=fake_client)

    result = await service.identify(filename="dune.epub", evidence=_evidence(), candidates=[])

    assert result.title == "Dune"
    assert result.series == "Dune Chronicles"
    assert result.ai_reported_confidence == 88
    assert result.model == "fake-model"
    assert len(fake_client.prompts) == 1


async def test_computed_confidence_not_ai_reported_confidence_drives_review_flag() -> None:
    # AI is very confident, but with no candidates and no ISBN the computed
    # score is low — needs_human_review must follow the computed score.
    fake_client = _FakeAIClient(
        result=AIIdentificationResult(
            title="Dune",
            author="Frank Herbert",
            series=None,
            series_number=None,
            ai_confidence=99,
            reasoning_summary="Very sure.",
            needs_human_review=False,
        )
    )
    service = IdentificationService(ai_client=fake_client)

    result = await service.identify(
        filename="book.epub", evidence=_evidence(isbn13=None, title=None), candidates=[]
    )

    assert result.ai_reported_confidence == 99
    assert result.computed_confidence < 85
    assert result.needs_human_review is True


async def test_fast_path_invented_series_drops_below_review_bar() -> None:
    # ISBN + provider + EPUB all agree on title/author, but the only reason
    # this book has a series is the fast-path series lookup guessed one that
    # no source mentions. That must pull the computed score under 85.
    fake_client = _FakeAIClient(
        series_result=AISeriesResult(series="The Invented Hierarchy", series_number=2)
    )
    service = IdentificationService(ai_client=fake_client)
    candidates = [
        MetadataCandidate(
            title="Dune", authors=["Frank Herbert"], isbn13="9780441172719", source="a"
        )
    ]

    result = await service.identify(
        filename="dune.epub", evidence=_evidence(), candidates=candidates
    )

    assert result.series == "The Invented Hierarchy"
    assert result.computed_confidence < 85
    assert result.needs_human_review is True
    assert result.raw_response["confidence_breakdown"]["conflicts"][
        "uncorroborated_series"
    ] == -15


async def test_fast_path_series_from_provider_is_not_penalized() -> None:
    fake_client = _FakeAIClient()
    service = IdentificationService(ai_client=fake_client)
    candidates = [
        MetadataCandidate(
            title="Dune", authors=["Frank Herbert"], isbn13="9780441172719",
            series="Dune Chronicles", series_number=1, source="a",
        )
    ]

    result = await service.identify(
        filename="dune.epub", evidence=_evidence(), candidates=candidates
    )

    assert result.series == "Dune Chronicles"
    conflicts = result.raw_response["confidence_breakdown"]["conflicts"]
    assert "uncorroborated_series" not in conflicts


async def test_ai_path_series_corroborated_by_a_provider_is_not_penalized() -> None:
    # prompts/15 Stage B: providers now return series, so an AI-supplied series
    # that a provider candidate also carries no longer takes the
    # uncorroborated-series penalty.
    fake_client = _FakeAIClient(
        result=AIIdentificationResult(
            title="The Final Empire",
            author="Brandon Sanderson",
            series="Mistborn",
            series_number=1,
            ai_confidence=88,
            reasoning_summary="matched",
            needs_human_review=False,
        )
    )
    service = IdentificationService(ai_client=fake_client)
    candidates = [
        MetadataCandidate(
            title="The Final Empire",
            authors=["Brandon Sanderson"],
            series="Mistborn",
            series_number=1,
            source="open_library",
        )
    ]

    result = await service.identify(
        filename="the-final-empire.epub",
        evidence=_evidence(title="The Final Empire", authors=["Brandon Sanderson"], isbn13=None),
        candidates=candidates,
    )

    conflicts = result.raw_response["confidence_breakdown"]["conflicts"]
    assert "uncorroborated_series" not in conflicts
    assert "series_disagreement" not in conflicts


async def test_filename_parse_corroborates_the_identification() -> None:
    # prompts/15 Stage C: a rich tracker filename that agrees with the AI's
    # answer earns the filename_matches_title component, and the structured
    # parse is shown to the model.
    fake_client = _FakeAIClient(
        result=AIIdentificationResult(
            title="The Final Empire",
            author="Brandon Sanderson",
            series="Mistborn",
            series_number=1,
            ai_confidence=80,
            reasoning_summary="matched",
            needs_human_review=False,
        )
    )
    service = IdentificationService(ai_client=fake_client)

    result = await service.identify(
        filename="Sanderson, Brandon - Mistborn 01 - The Final Empire (2006).epub",
        evidence=_evidence(title="The Final Empire", authors=["Brandon Sanderson"], isbn13=None),
        candidates=[],
    )

    assert result.raw_response["confidence_breakdown"]["components"]["filename_matches_title"] == 5
    assert "Structured parse of the filename" in fake_client.prompts[0]
    assert "Mistborn" in fake_client.prompts[0]
    assert result.raw_response["filename_guess"].startswith("title='The Final Empire'")


async def test_wrong_filename_parse_does_not_corroborate() -> None:
    fake_client = _FakeAIClient(
        result=AIIdentificationResult(
            title="Dune", author="Frank Herbert", series=None, series_number=None,
            ai_confidence=80, reasoning_summary="x", needs_human_review=False,
        )
    )
    service = IdentificationService(ai_client=fake_client)

    result = await service.identify(
        filename="Brandon Sanderson - The Way of Kings.epub",
        evidence=_evidence(title=None, isbn13=None),
        candidates=[],
    )

    assert result.raw_response["confidence_breakdown"]["components"]["filename_matches_title"] == 0


async def test_junk_series_number_is_clamped_series_name_kept() -> None:
    fake_client = _FakeAIClient(
        result=AIIdentificationResult(
            title="Command Decision",
            author="J. Daniel Layfield",
            series="Alexis Carew",
            series_number=301,
            ai_confidence=80,
            reasoning_summary="#301 is almost certainly a Calibre placeholder.",
            needs_human_review=False,
        )
    )
    service = IdentificationService(ai_client=fake_client)

    result = await service.identify(
        filename="carew.epub", evidence=_evidence(isbn13=None, title=None), candidates=[]
    )

    assert result.series == "Alexis Carew"
    assert result.series_number is None
    assert result.raw_response["series_number_clamped"] == 301


async def test_fractional_series_number_survives_the_clamp() -> None:
    fake_client = _FakeAIClient(
        result=AIIdentificationResult(
            title="Snuff",
            author="Terry Pratchett",
            series="Discworld",
            series_number=39.5,
            ai_confidence=80,
            reasoning_summary="novella between volumes",
            needs_human_review=False,
        )
    )
    service = IdentificationService(ai_client=fake_client)

    result = await service.identify(
        filename="snuff.epub", evidence=_evidence(isbn13=None, title=None), candidates=[]
    )

    assert result.series_number == 39.5
    assert "series_number_clamped" not in result.raw_response


async def test_ai_failure_falls_back_to_low_confidence_result() -> None:
    fake_client = _FakeAIClient(raises=True)
    service = IdentificationService(ai_client=fake_client)

    result = await service.identify(filename="dune.epub", evidence=_evidence(), candidates=[])

    assert result.model == "unavailable"
    assert result.needs_human_review is True
    assert "simulated failure" in result.reasoning_summary


def _standalone_correction() -> list[dict]:
    return [
        {
            "proposed": {
                "title": "Scion",
                "author": "James Islington",
                "series": "The Hierarchy",
                "series_number": 2,
            },
            "corrected": {
                "title": "Scion",
                "author": "James Islington",
                "series": None,
                "series_number": None,
            },
        }
    ]


def test_build_prompt_includes_stage_d_evidence() -> None:
    ev = _evidence(
        description="A soldier seeks revenge across a galactic empire.",
        publisher="Orbit Books",
        pub_date="2013-10-01",
        subjects=["Science Fiction", "Space Opera"],
        all_isbns=["9780316246620", "031624662X"],
    )
    prompt = _build_prompt("x.epub", ev, [])

    assert "EPUB description: A soldier seeks revenge" in prompt
    assert "EPUB publisher: Orbit Books" in prompt
    assert "EPUB publication date: 2013-10-01" in prompt
    assert "EPUB subjects/genre: Science Fiction, Space Opera" in prompt
    assert "All ISBNs found in the EPUB: 9780316246620, 031624662X" in prompt


def test_build_prompt_byte_identical_without_corrections() -> None:
    evidence = _evidence()
    baseline = _build_prompt("dune.epub", evidence, [])
    assert _build_prompt("dune.epub", evidence, [], None) == baseline
    assert _build_prompt("dune.epub", evidence, [], []) == baseline


def test_build_prompt_renders_a_standalone_correction() -> None:
    prompt = _build_prompt("scion.epub", _evidence(), [], _standalone_correction())

    assert "Corrections a human has previously made" in prompt
    assert 'You said: "Scion" by James Islington, series "The Hierarchy" #2' in prompt
    assert "Corrected to: standalone, no series" in prompt


def test_build_prompt_correction_only_shows_changed_fields() -> None:
    corrections = [
        {
            "proposed": {
                "title": "The Wrong Title",
                "author": "A. Author",
                "series": "Some Series",
                "series_number": 4,
            },
            "corrected": {
                "title": "The Right Title",
                "author": "A. Author",
                "series": "Some Series",
                "series_number": 4,
            },
        }
    ]

    prompt = _build_prompt("x.epub", _evidence(), [], corrections)

    assert 'Corrected to: title "The Right Title"' in prompt
    assert "Some Series" not in prompt.split("Corrected to:")[1]


async def test_identify_feeds_corrections_into_the_prompt() -> None:
    fake_client = _FakeAIClient(
        result=AIIdentificationResult(
            title="Scion",
            author="James Islington",
            series=None,
            series_number=None,
            ai_confidence=70,
            reasoning_summary="standalone",
            needs_human_review=False,
        )
    )
    service = IdentificationService(ai_client=fake_client)

    await service.identify(
        filename="scion.epub",
        evidence=_evidence(isbn13=None, title="Scion"),
        candidates=[],
        corrections=_standalone_correction(),
    )

    assert "standalone, no series" in fake_client.prompts[0]


def _candidate(**overrides) -> MetadataCandidate:
    defaults = dict(title="Dune", authors=["Frank Herbert"], isbn13="9780441172719", source="gb")
    defaults.update(overrides)
    return MetadataCandidate(**defaults)


import datetime as _dt

_THIS_YEAR = _dt.date.today().year


def test_should_ground_only_on_a_recent_year_signal() -> None:
    # Thin / conflicting / ISBN-less provider evidence does NOT ground — that's
    # money spent on books the model already knows. Only a recent-year signal
    # (the post-cutoff "invented series" risk) does.
    assert not should_ground(filename="dune.epub", evidence=_evidence(), candidates=[])
    assert not should_ground(
        filename="x.epub",
        evidence=_evidence(isbn13=None),
        candidates=[_candidate(title="Dune"), _candidate(title="Children of Dune", source="ol")],
    )


def test_should_ground_on_a_recent_filename_year() -> None:
    assert should_ground(
        filename=f"Some New Book ({_THIS_YEAR}).epub",
        evidence=_evidence(),
        candidates=[],
    )


def test_should_ground_on_a_recent_provider_pub_date() -> None:
    assert should_ground(
        filename="some-new-book.epub",
        evidence=_evidence(),
        candidates=[_candidate(first_published=f"{_THIS_YEAR}-03-01")],
    )


def test_should_ground_ignores_an_old_year() -> None:
    assert not should_ground(
        filename="Dune (1965).epub", evidence=_evidence(), candidates=[]
    )


def test_should_ground_disabled_by_settings(monkeypatch) -> None:
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("AI_WEB_SEARCH_ENABLED", "false")
    try:
        assert not should_ground(
            filename=f"New Book ({_THIS_YEAR}).epub", evidence=_evidence(), candidates=[]
        )
    finally:
        get_settings.cache_clear()


async def test_ai_path_grounds_only_for_a_recent_book() -> None:
    def _client() -> _FakeAIClient:
        return _FakeAIClient(
            result=AIIdentificationResult(
                title="Dune", author="Frank Herbert", series=None, series_number=None,
                ai_confidence=80, reasoning_summary="x", needs_human_review=False,
            )
        )

    old = _client()
    await IdentificationService(ai_client=old).identify(
        filename="dune.epub", evidence=_evidence(), candidates=[]
    )
    assert old.ground_flags == [False]

    recent = _client()
    await IdentificationService(ai_client=recent).identify(
        filename=f"Brand New ({_THIS_YEAR}).epub", evidence=_evidence(), candidates=[]
    )
    assert recent.ground_flags == [True]


async def test_evidence_hash_is_stable_for_identical_input() -> None:
    fake_client = _FakeAIClient(
        result=AIIdentificationResult(
            title="Dune",
            author="Frank Herbert",
            series=None,
            series_number=None,
            ai_confidence=80,
            reasoning_summary="x",
            needs_human_review=True,
        )
    )
    service = IdentificationService(ai_client=fake_client)

    r1 = await service.identify(filename="dune.epub", evidence=_evidence(), candidates=[])
    r2 = await service.identify(filename="dune.epub", evidence=_evidence(), candidates=[])

    assert r1.evidence_hash == r2.evidence_hash
