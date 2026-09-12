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


def test_default_service_adds_hardcover_only_when_token_is_set(monkeypatch) -> None:
    from app.core.config import get_settings
    from app.services.candidate_service import default_candidate_service

    settings = get_settings()

    monkeypatch.setattr(settings, "hardcover_api_token", "")
    names = {p.name for p in default_candidate_service()._providers}
    assert "hardcover" not in names
    assert {"google_books", "open_library"} <= names

    monkeypatch.setattr(settings, "hardcover_api_token", "tok")
    assert "hardcover" in {p.name for p in default_candidate_service()._providers}


async def test_resolve_author_person_id_without_hardcover_is_none() -> None:
    service = CandidateService(providers=[_FakeProvider("a")])
    assert await service.resolve_author_person_id("Iain Banks") is None
    assert await service.resolve_author_person_id(None) is None


async def test_resolve_author_person_id_delegates_to_hardcover(monkeypatch) -> None:
    from app.providers.metadata.hardcover import HardcoverProvider

    hc = HardcoverProvider(token="t")

    async def _fake(name):
        return 95997 if name == "Iain M. Banks" else None

    monkeypatch.setattr(hc, "resolve_person_id", _fake)
    service = CandidateService(providers=[_FakeProvider("a"), hc])

    assert await service.resolve_author_person_id("Iain M. Banks") == 95997
    assert await service.resolve_author_person_id("Someone Else") is None
