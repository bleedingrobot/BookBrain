import httpx
import respx

from app.providers.acquisition.flaresolverr import FlareSolverrClient, looks_like_challenge_page

URL = "http://127.0.0.1:8191/v1"


def test_looks_like_challenge_page_matches_known_markers():
    assert looks_like_challenge_page("Just a moment... checking your browser", 200) is True
    assert looks_like_challenge_page("<title>DDoS-Guard</title>", 200) is True
    assert looks_like_challenge_page("<title>DDOS-GUARD</title>", 200) is True  # case-insensitive
    assert looks_like_challenge_page("<html>real content</html>", 200) is False


def test_looks_like_challenge_page_treats_403_429_503_as_blocked():
    assert looks_like_challenge_page("totally normal looking content", 403) is True
    assert looks_like_challenge_page("totally normal looking content", 429) is True
    assert looks_like_challenge_page("totally normal looking content", 503) is True
    assert looks_like_challenge_page("totally normal looking content", 200) is False


@respx.mock
async def test_solve_returns_solved_page_on_success():
    respx.post(URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "ok",
                "solution": {
                    "response": "<html>real page</html>",
                    "status": 200,
                    "cookies": [{"name": "session", "value": "abc123"}, {"name": "__ddg9_", "value": "xyz"}],
                    "userAgent": "Mozilla/5.0 fake",
                },
            },
        )
    )

    async with httpx.AsyncClient() as client:
        result = await FlareSolverrClient(URL, client=client).solve("https://example.com/search")

    assert result is not None
    assert result.html == "<html>real page</html>"
    assert result.cookies == {"session": "abc123", "__ddg9_": "xyz"}
    assert result.user_agent == "Mozilla/5.0 fake"


@respx.mock
async def test_solve_sends_session_and_timeout():
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, json={"status": "ok", "solution": {"response": "<html>ok</html>", "status": 200, "cookies": []}})
    )

    async with httpx.AsyncClient() as client:
        await FlareSolverrClient(URL, client=client).solve(
            "https://example.com/x", session="sess-1", max_timeout_ms=12345
        )

    sent = respx.calls.last.request
    import json

    body = json.loads(sent.content)
    assert body == {"cmd": "request.get", "url": "https://example.com/x", "maxTimeout": 12345, "session": "sess-1"}


@respx.mock
async def test_solve_returns_none_on_network_error():
    respx.post(URL).mock(side_effect=httpx.ConnectError("refused"))

    async with httpx.AsyncClient() as client:
        result = await FlareSolverrClient(URL, client=client).solve("https://example.com/search")

    assert result is None


@respx.mock
async def test_solve_returns_none_on_status_error():
    respx.post(URL).mock(return_value=httpx.Response(200, json={"status": "error", "message": "nope"}))

    async with httpx.AsyncClient() as client:
        result = await FlareSolverrClient(URL, client=client).solve("https://example.com/search")

    assert result is None


@respx.mock
async def test_solve_returns_none_when_ok_but_still_a_challenge_page():
    # Regression test for FlareSolverr's own PR #1773 false-positive: "ok"
    # status but the body is actually still a DDoS-Guard CAPTCHA page.
    respx.post(URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "ok",
                "solution": {
                    "response": "<title>DDoS-Guard</title><body>please verify you are human</body>",
                    "status": 200,
                    "cookies": [],
                },
            },
        )
    )

    async with httpx.AsyncClient() as client:
        result = await FlareSolverrClient(URL, client=client).solve("https://example.com/search")

    assert result is None


@respx.mock
async def test_create_session_returns_session_id():
    respx.post(URL).mock(return_value=httpx.Response(200, json={"status": "ok", "solution": {"session": "sess-42"}}))

    async with httpx.AsyncClient() as client:
        session_id = await FlareSolverrClient(URL, client=client).create_session()

    assert session_id == "sess-42"


@respx.mock
async def test_create_session_returns_none_on_failure():
    respx.post(URL).mock(return_value=httpx.Response(500))

    async with httpx.AsyncClient() as client:
        session_id = await FlareSolverrClient(URL, client=client).create_session()

    assert session_id is None


@respx.mock
async def test_destroy_session_never_raises_even_on_failure():
    respx.post(URL).mock(side_effect=httpx.ConnectError("refused"))

    async with httpx.AsyncClient() as client:
        await FlareSolverrClient(URL, client=client).destroy_session("sess-1")  # must not raise
