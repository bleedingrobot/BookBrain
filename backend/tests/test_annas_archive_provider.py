import httpx
import pytest
import respx

from app.providers.acquisition import annas_archive as aa_module
from app.providers.acquisition.annas_archive import AnnasArchiveProvider
from app.providers.acquisition.exceptions import AcquisitionUnavailable

BASE = "https://annas-archive.org"


@pytest.fixture(autouse=True)
def _no_throttle(monkeypatch):
    # The provider's own politeness floor would make every test wait several
    # seconds for no reason — tests don't need to prove the throttle exists,
    # just that search/download behave correctly.
    monkeypatch.setattr(aa_module, "_MIN_REQUEST_INTERVAL", 0)


@respx.mock
async def test_search_parses_results_into_acquisition_results() -> None:
    respx.get(f"{BASE}/search").mock(
        return_value=httpx.Response(
            200,
            text="""
            <div class="result">
                <a href="/md5/abc123def456">The Final Empire - Brandon Sanderson, epub, 1.2MB</a>
            </div>
            """,
        )
    )

    async with httpx.AsyncClient() as client:
        provider = AnnasArchiveProvider(client=client)
        results = await provider.search("The Final Empire Brandon Sanderson")

    assert len(results) == 1
    r = results[0]
    assert r.title == "The Final Empire"
    assert r.format == "epub"
    assert r.size == "1.2MB"
    assert r.full == "md5/abc123def456"
    assert r.provider == "annas_archive"
    assert r.server is None


@respx.mock
async def test_search_returns_empty_list_on_http_error_not_raise() -> None:
    respx.get(f"{BASE}/search").mock(return_value=httpx.Response(500))

    async with httpx.AsyncClient() as client:
        provider = AnnasArchiveProvider(client=client)
        results = await provider.search("anything")

    assert results == []


@respx.mock
async def test_search_with_no_result_links_returns_empty_list() -> None:
    respx.get(f"{BASE}/search").mock(return_value=httpx.Response(200, text="<div>no results</div>"))

    async with httpx.AsyncClient() as client:
        provider = AnnasArchiveProvider(client=client)
        results = await provider.search("obscure pamphlet")

    assert results == []


@respx.mock
async def test_download_follows_detail_page_to_a_working_mirror() -> None:
    respx.get(f"{BASE}/md5/abc123").mock(
        return_value=httpx.Response(
            200,
            text='<a href="/slow_download/abc123">Slow download</a>',
        )
    )
    respx.get(f"{BASE}/slow_download/abc123").mock(
        return_value=httpx.Response(200, content=b"fake epub bytes")
    )

    async with httpx.AsyncClient() as client:
        provider = AnnasArchiveProvider(client=client)
        path = await provider.download("md5/abc123")

    assert path.read_bytes() == b"fake epub bytes"
    path.unlink()


@respx.mock
async def test_download_skips_a_cloudflare_protected_mirror_and_tries_the_next() -> None:
    respx.get(f"{BASE}/md5/abc123").mock(
        return_value=httpx.Response(
            200,
            text=(
                '<a href="/slow_download/blocked">mirror 1</a>'
                '<a href="/slow_download/works">mirror 2</a>'
            ),
        )
    )
    respx.get(f"{BASE}/slow_download/blocked").mock(
        return_value=httpx.Response(403, text="Just a moment... checking your browser")
    )
    respx.get(f"{BASE}/slow_download/works").mock(
        return_value=httpx.Response(200, content=b"real epub bytes")
    )

    async with httpx.AsyncClient() as client:
        provider = AnnasArchiveProvider(client=client)
        path = await provider.download("md5/abc123")

    assert path.read_bytes() == b"real epub bytes"
    path.unlink()


@respx.mock
async def test_download_raises_acquisition_unavailable_when_every_mirror_fails() -> None:
    respx.get(f"{BASE}/md5/abc123").mock(
        return_value=httpx.Response(200, text='<a href="/slow_download/dead">mirror</a>')
    )
    respx.get(f"{BASE}/slow_download/dead").mock(return_value=httpx.Response(404))

    async with httpx.AsyncClient() as client:
        provider = AnnasArchiveProvider(client=client)
        with pytest.raises(AcquisitionUnavailable):
            await provider.download("md5/abc123")


@respx.mock
async def test_download_raises_when_detail_page_itself_fails() -> None:
    respx.get(f"{BASE}/md5/abc123").mock(return_value=httpx.Response(500))

    async with httpx.AsyncClient() as client:
        provider = AnnasArchiveProvider(client=client)
        with pytest.raises(AcquisitionUnavailable):
            await provider.download("md5/abc123")


def test_is_enabled_reflects_settings(monkeypatch):
    class _Cfg:
        annas_archive_enabled = True
        annas_archive_base_url = BASE

    monkeypatch.setattr(aa_module, "get_settings", lambda: _Cfg())
    assert AnnasArchiveProvider().is_enabled() is True
