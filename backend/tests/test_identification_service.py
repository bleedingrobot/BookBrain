from app.providers.ai.anthropic_client import AIIdentificationError
from app.providers.ai.types import AIIdentificationResult
from app.providers.epub.parser import EpubEvidence
from app.providers.metadata.types import MetadataCandidate
from app.services.identification_service import IdentificationService


class _FakeAIClient:
    model_name = "fake-model"

    def __init__(self, result: AIIdentificationResult | None = None, raises: bool = False) -> None:
        self._result = result
        self._raises = raises
        self.prompts: list[str] = []

    async def identify(self, prompt: str):
        self.prompts.append(prompt)
        if self._raises:
            raise AIIdentificationError("simulated failure")
        return self._result, {"stop_reason": "tool_use"}


def _evidence(**overrides) -> EpubEvidence:
    defaults = dict(title="Dune", authors=["Frank Herbert"], language="en", isbn13="9780441172719")
    defaults.update(overrides)
    return EpubEvidence(**defaults)


async def test_fast_path_skips_ai_when_isbn_provider_and_epub_agree() -> None:
    fake_client = _FakeAIClient()
    service = IdentificationService(ai_client=fake_client)
    candidates = [MetadataCandidate(title="Dune", authors=["Frank Herbert"], isbn13="9780441172719", source="a")]

    result = await service.identify(filename="dune.epub", evidence=_evidence(), candidates=candidates)

    assert result.model == "deterministic"
    assert result.title == "Dune"
    assert result.author == "Frank Herbert"
    assert fake_client.prompts == []  # never called


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


async def test_ai_failure_falls_back_to_low_confidence_result() -> None:
    fake_client = _FakeAIClient(raises=True)
    service = IdentificationService(ai_client=fake_client)

    result = await service.identify(filename="dune.epub", evidence=_evidence(), candidates=[])

    assert result.model == "unavailable"
    assert result.needs_human_review is True
    assert "simulated failure" in result.reasoning_summary


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
