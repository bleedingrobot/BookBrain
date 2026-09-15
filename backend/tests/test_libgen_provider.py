import httpx
import pytest
import respx

from app.providers.acquisition import libgen as lg_module
from app.providers.acquisition.exceptions import AcquisitionUnavailable
from app.providers.acquisition.libgen import LibgenProvider

MIRROR_A = "https://libgen.li"
MIRROR_B = "https://libgen.vg"

MD5_FULL = "ac1017fff36d3cb3187e7ac2f292f94e"
MD5_COMPACT = "8bf185829be54de61f4d48cce50ca872"
DECOY_MD5 = "deadbeefdeadbeefdeadbeefdeadbeef"

# A full (9-cell) row and a compact (5-cell) row, modeled on real libgen.li
# search-result markup — including a decoy md5-shaped string in the Title
# cell (e.g. a cover-image URL) the parser must not pick up, and the
# view/favorite/read-count badge spans real rows carry (live-confirmed
# 2026-09-16 to otherwise leak into the title as e.g. "b l 508418 f 467902").
SEARCH_HTML = f"""
<table id="tablelibgen"><thead><tr><th>header</th></tr></thead><tbody>
<tr>
<td><a href="/cover/{DECOY_MD5}.jpg"></a><a href="edition.php?id=1">The Final Empire</a>
<nobr><span class="badge badge-primary">b</span> <span class="badge badge-secondary">l 508418</span></nobr>
</td>
<td><a href="author.php?id=1">Brandon Sanderson(Author)</a></td>
<td>Publisher</td>
<td>2006</td>
<td>English</td>
<td>544</td>
<td><a href="/file.php?id=1">3&nbsp;MB</a></td>
<td>pdf</td>
<td><a href="/ads.php?md5={MD5_FULL}">1</a></td>
</tr>
<tr>
<td><a href="edition.php?id=1">The Final Empire</a></td>
<td>544</td>
<td>2&nbsp;MB</td>
<td>fb2</td>
<td><a href="/ads.php?md5={MD5_COMPACT}">Libgen</a></td>
</tr>
</tbody></table>
"""

NO_TABLE_HTML = "<html><body>no results table here</body></html>"

ADS_HTML = f"""
<html><body>
<a href="get.php?md5={MD5_FULL}&key=SOMEKEY123">
<h2>GET</h2>
</a>
</body></html>
"""


@pytest.fixture(autouse=True)
def _no_throttle(monkeypatch):
    monkeypatch.setattr(lg_module, "_MIN_REQUEST_INTERVAL", 0)


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    class _Cfg:
        libgen_enabled = True
        libgen_base_urls = f"{MIRROR_A},{MIRROR_B}"

    monkeypatch.setattr(lg_module, "get_settings", lambda: _Cfg())


@respx.mock
async def test_search_parses_full_and_compact_rows() -> None:
    respx.get(f"{MIRROR_A}/index.php").mock(return_value=httpx.Response(200, text=SEARCH_HTML))

    async with httpx.AsyncClient() as client:
        provider = LibgenProvider(client=client)
        results = await provider.search("the final empire")

    assert [r.full for r in results] == [MD5_FULL, MD5_COMPACT]

    full = results[0]
    assert full.title == "The Final Empire"
    assert full.author == "Brandon Sanderson(Author)"
    assert full.format == "pdf"
    assert full.size == "3 MB"
    assert full.provider == "libgen"
    assert full.server is None

    compact = results[1]
    assert compact.title == "The Final Empire"
    assert compact.format == "fb2"
    assert compact.size == "2 MB"
    assert compact.author == ""


@respx.mock
async def test_search_md5_scoped_to_mirrors_cell_not_decoy() -> None:
    respx.get(f"{MIRROR_A}/index.php").mock(return_value=httpx.Response(200, text=SEARCH_HTML))

    async with httpx.AsyncClient() as client:
        provider = LibgenProvider(client=client)
        results = await provider.search("anything")

    assert DECOY_MD5 not in {r.full for r in results}


@respx.mock
async def test_search_tries_next_mirror_when_first_has_no_results_table() -> None:
    respx.get(f"{MIRROR_A}/index.php").mock(return_value=httpx.Response(200, text=NO_TABLE_HTML))
    respx.get(f"{MIRROR_B}/index.php").mock(return_value=httpx.Response(200, text=SEARCH_HTML))

    async with httpx.AsyncClient() as client:
        provider = LibgenProvider(client=client)
        results = await provider.search("anything")

    assert [r.full for r in results] == [MD5_FULL, MD5_COMPACT]


@respx.mock
async def test_search_stops_at_first_mirror_with_a_well_formed_empty_table() -> None:
    empty_table = '<table id="tablelibgen"><thead><tr><th>h</th></tr></thead><tbody></tbody></table>'
    route_a = respx.get(f"{MIRROR_A}/index.php").mock(return_value=httpx.Response(200, text=empty_table))
    route_b = respx.get(f"{MIRROR_B}/index.php").mock(return_value=httpx.Response(200, text=SEARCH_HTML))

    async with httpx.AsyncClient() as client:
        provider = LibgenProvider(client=client)
        results = await provider.search("anything")

    assert results == []
    assert route_a.called
    assert not route_b.called


@respx.mock
async def test_search_returns_empty_list_on_http_error_not_raise() -> None:
    respx.get(f"{MIRROR_A}/index.php").mock(return_value=httpx.Response(500))
    respx.get(f"{MIRROR_B}/index.php").mock(return_value=httpx.Response(500))

    async with httpx.AsyncClient() as client:
        provider = LibgenProvider(client=client)
        results = await provider.search("anything")

    assert results == []


@respx.mock
async def test_download_resolves_ads_page_to_get_php_and_downloads() -> None:
    respx.get(f"{MIRROR_A}/ads.php", params={"md5": MD5_FULL}).mock(
        return_value=httpx.Response(200, text=ADS_HTML)
    )
    respx.get(f"{MIRROR_A}/get.php", params={"md5": MD5_FULL, "key": "SOMEKEY123"}).mock(
        return_value=httpx.Response(200, content=b"x" * 20_000)
    )

    async with httpx.AsyncClient() as client:
        provider = LibgenProvider(client=client)
        path = await provider.download(MD5_FULL)

    assert path.read_bytes() == b"x" * 20_000
    path.unlink()


@respx.mock
async def test_download_rejects_undersized_response_and_tries_next_mirror() -> None:
    respx.get(f"{MIRROR_A}/ads.php", params={"md5": MD5_FULL}).mock(
        return_value=httpx.Response(200, text=ADS_HTML)
    )
    respx.get(f"{MIRROR_A}/get.php", params={"md5": MD5_FULL, "key": "SOMEKEY123"}).mock(
        return_value=httpx.Response(200, content=b"tiny error page")
    )
    respx.get(f"{MIRROR_B}/ads.php", params={"md5": MD5_FULL}).mock(
        return_value=httpx.Response(200, text=ADS_HTML)
    )
    respx.get(f"{MIRROR_B}/get.php", params={"md5": MD5_FULL, "key": "SOMEKEY123"}).mock(
        return_value=httpx.Response(200, content=b"y" * 20_000)
    )

    async with httpx.AsyncClient() as client:
        provider = LibgenProvider(client=client)
        path = await provider.download(MD5_FULL)

    assert path.read_bytes() == b"y" * 20_000
    path.unlink()


@respx.mock
async def test_download_tries_next_mirror_when_ads_page_has_no_get_link() -> None:
    respx.get(f"{MIRROR_A}/ads.php", params={"md5": MD5_FULL}).mock(
        return_value=httpx.Response(200, text="<html>no download link here</html>")
    )
    respx.get(f"{MIRROR_B}/ads.php", params={"md5": MD5_FULL}).mock(
        return_value=httpx.Response(200, text=ADS_HTML)
    )
    respx.get(f"{MIRROR_B}/get.php", params={"md5": MD5_FULL, "key": "SOMEKEY123"}).mock(
        return_value=httpx.Response(200, content=b"z" * 20_000)
    )

    async with httpx.AsyncClient() as client:
        provider = LibgenProvider(client=client)
        path = await provider.download(MD5_FULL)

    assert path.read_bytes() == b"z" * 20_000
    path.unlink()


@respx.mock
async def test_download_raises_acquisition_unavailable_when_every_mirror_fails() -> None:
    respx.get(f"{MIRROR_A}/ads.php", params={"md5": MD5_FULL}).mock(return_value=httpx.Response(404))
    respx.get(f"{MIRROR_B}/ads.php", params={"md5": MD5_FULL}).mock(return_value=httpx.Response(404))

    async with httpx.AsyncClient() as client:
        provider = LibgenProvider(client=client)
        with pytest.raises(AcquisitionUnavailable):
            await provider.download(MD5_FULL)


def test_is_enabled_reflects_settings(monkeypatch):
    class _Cfg:
        libgen_enabled = True
        libgen_base_urls = MIRROR_A

    monkeypatch.setattr(lg_module, "get_settings", lambda: _Cfg())
    assert LibgenProvider().is_enabled() is True

    class _CfgOff:
        libgen_enabled = False
        libgen_base_urls = MIRROR_A

    monkeypatch.setattr(lg_module, "get_settings", lambda: _CfgOff())
    assert LibgenProvider().is_enabled() is False
