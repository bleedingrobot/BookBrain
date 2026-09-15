from pathlib import Path

from app.providers.acquisition.openbooks import OpenBooksProvider
from app.services import openbooks_service
from app.services.openbooks_service import SearchOutcome


def test_is_enabled_delegates_to_openbooks_service(monkeypatch):
    monkeypatch.setattr(openbooks_service, "is_enabled", lambda: True)
    assert OpenBooksProvider().is_enabled() is True
    monkeypatch.setattr(openbooks_service, "is_enabled", lambda: False)
    assert OpenBooksProvider().is_enabled() is False


def test_is_busy_delegates_to_openbooks_service(monkeypatch):
    monkeypatch.setattr(openbooks_service, "is_busy", lambda: True)
    assert OpenBooksProvider().is_busy() is True


async def test_search_delegates_and_unwraps_search_outcome(monkeypatch):
    outcome = SearchOutcome(results=[], parse_errors=0)

    async def fake_search(query: str) -> SearchOutcome:
        assert query == "dune"
        return outcome

    monkeypatch.setattr(openbooks_service, "search", fake_search)
    provider = OpenBooksProvider()
    assert await provider.search("dune") is outcome.results


async def test_download_delegates_to_openbooks_service(monkeypatch, tmp_path):
    target = tmp_path / "book.epub"
    target.write_bytes(b"x")

    async def fake_download(full: str) -> Path:
        assert full == "!Bsk x.epub"
        return target

    monkeypatch.setattr(openbooks_service, "download", fake_download)
    provider = OpenBooksProvider()
    assert await provider.download("!Bsk x.epub") == target
