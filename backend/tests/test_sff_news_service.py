import httpx
import pytest
import respx

from app.services import sff_news_service
from app.services.sff_news_service import _plain, fetch_news

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Test Mag</title><link>https://testmag.example</link>
  <item>
    <title>Cover Reveal: &lt;i&gt;The Long Dark&lt;/i&gt;</title>
    <link>https://testmag.example/cover-reveal</link>
    <description>&lt;p&gt;A gorgeous new cover for the sequel.&lt;/p&gt; The post Cover Reveal appeared first on Test Mag.</description>
    <pubDate>Tue, 09 Sep 2026 12:00:00 +0000</pubDate>
  </item>
  <item>
    <title>Review roundup</title>
    <link>https://testmag.example/roundup</link>
    <description>Five books we loved this month. Continue reading &#8594;</description>
    <pubDate>Mon, 08 Sep 2026 09:00:00 +0000</pubDate>
  </item>
  <item>
    <title>No link here</title>
    <description>should be dropped</description>
  </item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Blogger Blog</title><link href="https://blog.example/"/>
  <entry>
    <title>SPFBO Finalist Review</title>
    <link rel="alternate" href="https://blog.example/spfbo-review"/>
    <summary>An early look at a strong contender.</summary>
    <published>2026-09-10T07:00:00Z</published>
  </entry>
</feed>"""


def _mock(mapping: dict[str, httpx.Response]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return mapping.get(str(request.url), httpx.Response(404))

    respx.route().mock(side_effect=handler)


@pytest.fixture
def _two_feeds(monkeypatch):
    monkeypatch.setattr(
        sff_news_service,
        "_FEEDS",
        [
            ("Test Mag", "https://testmag.example/feed/"),
            ("Blogger Blog", "https://blog.example/atom"),
            ("Dead Feed", "https://dead.example/feed/"),
        ],
    )


@respx.mock
async def test_merges_sorts_and_cleans(_two_feeds) -> None:
    _mock(
        {
            "https://testmag.example/feed/": httpx.Response(200, text=RSS),
            "https://blog.example/atom": httpx.Response(200, text=ATOM),
            "https://dead.example/feed/": httpx.Response(500),
        }
    )

    items = await fetch_news()

    # dead feed skipped, the no-link item dropped
    assert [i["source"] for i in items] == ["Blogger Blog", "Test Mag", "Test Mag"]
    # newest first
    assert items[0]["title"] == "SPFBO Finalist Review"
    assert items[0]["published"] == "2026-09-10T07:00:00+00:00"
    # HTML unescaped-then-stripped, WP + "Continue reading" trailers gone
    cover = next(i for i in items if "Cover Reveal" in i["title"])
    assert cover["title"] == "Cover Reveal: The Long Dark"
    assert cover["summary"] == "A gorgeous new cover for the sequel."
    roundup = next(i for i in items if "roundup" in i["title"])
    assert roundup["summary"] == "Five books we loved this month."


@respx.mock
async def test_dedupes_by_link(_two_feeds) -> None:
    dup = RSS.replace("https://blog.example/spfbo-review", "https://testmag.example/cover-reveal")
    _mock(
        {
            "https://testmag.example/feed/": httpx.Response(200, text=RSS),
            "https://blog.example/atom": httpx.Response(200, text=dup),
            "https://dead.example/feed/": httpx.Response(200, text="<rss></rss>"),
        }
    )
    items = await fetch_news()
    links = [i["link"] for i in items]
    assert len(links) == len(set(links))


@respx.mock
async def test_all_feeds_down_returns_empty(_two_feeds) -> None:
    _mock({})  # everything 404s
    assert await fetch_news() == []


def test_plain_strips_double_escaped_tags_and_entities() -> None:
    assert _plain("Tom &amp; Jerry &lt;b&gt;win&lt;/b&gt;") == "Tom & Jerry win"
    assert _plain("") == ""
    assert _plain("Plain text, nothing to do") == "Plain text, nothing to do"
