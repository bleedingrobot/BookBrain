from app.providers.metadata.types import MetadataCandidate
from app.services.candidate_service import CandidateService


class _FakeProvider:
    def __init__(
        self,
        name: str,
        isbn_results: list[MetadataCandidate] | None = None,
        title_results: list[MetadataCandidate] | None = None,
    ) -> None:
        self.name = name
        self._isbn_results = isbn_results or []
        self._title_results = title_results or []
        self.isbn_calls: list[str] = []
        self.title_calls: list[tuple[str, str | None]] = []

    async def search_by_isbn(self, isbn: str) -> list[MetadataCandidate]:
        self.isbn_calls.append(isbn)
        return self._isbn_results

    async def search_by_title_author(
        self, title: str, author: str | None
    ) -> list[MetadataCandidate]:
        self.title_calls.append((title, author))
        return self._title_results


async def test_isbn_lookup_aggregates_across_providers() -> None:
    candidate_a = MetadataCandidate(title="Dune", source="a")
    candidate_b = MetadataCandidate(title="Dune (alt)", source="b")
    provider_a = _FakeProvider("a", isbn_results=[candidate_a])
    provider_b = _FakeProvider("b", isbn_results=[candidate_b])
    service = CandidateService(providers=[provider_a, provider_b])

    results = await service.generate_candidates(isbn13="9780441172719", title="Dune")

    assert results == [candidate_a, candidate_b]
    assert provider_a.isbn_calls == ["9780441172719"]
    assert provider_b.isbn_calls == ["9780441172719"]
    # title fallback never triggered — ISBN lookup already returned results
    assert provider_a.title_calls == []


async def test_falls_back_to_title_author_when_isbn_yields_nothing() -> None:
    candidate = MetadataCandidate(title="Dune", source="a")
    provider = _FakeProvider("a", isbn_results=[], title_results=[candidate])
    service = CandidateService(providers=[provider])

    results = await service.generate_candidates(isbn13="0000000000000", title="Dune", authors="Frank Herbert")

    assert results == [candidate]
    assert provider.title_calls == [("Dune", "Frank Herbert")]


async def test_no_isbn_goes_straight_to_title_fallback() -> None:
    candidate = MetadataCandidate(title="Dune", source="a")
    provider = _FakeProvider("a", title_results=[candidate])
    service = CandidateService(providers=[provider])

    results = await service.generate_candidates(title="Dune")

    assert results == [candidate]
    assert provider.isbn_calls == []


async def test_no_isbn_and_no_title_returns_empty() -> None:
    provider = _FakeProvider("a")
    service = CandidateService(providers=[provider])

    results = await service.generate_candidates()

    assert results == []
    assert provider.isbn_calls == []
    assert provider.title_calls == []


async def test_empty_provider_list_is_a_safe_no_op() -> None:
    service = CandidateService(providers=[])

    results = await service.generate_candidates(isbn13="9780441172719", title="Dune")

    assert results == []
