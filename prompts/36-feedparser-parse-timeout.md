# Task 36 — bound `feedparser.parse` with a timeout (REVIEW-2026-09-10 F8)

Read `prompts/README.md`. Backend-only, tiny. No Anthropic, no real-Drive
write.

## Why

`app/services/sff_news_service.py`, `_fetch_one`:

```python
resp = await client.get(url, headers={"User-Agent": _UA}, follow_redirects=True)
resp.raise_for_status()
return await asyncio.to_thread(_parse_feed, resp.content, name)
```

The HTTP GET is bounded (`_TIMEOUT = 12.0`). `feedparser.parse` is **not**.
`feedparser` on a pathological feed — enormous, deeply-nested XML, entity
expansion — can run a very long time, and `fetch_news` dispatches all 11
feeds concurrently via `asyncio.gather`. A wedged parse holds an
`asyncio.to_thread` worker and the nightly `regenerate_news` step never
returns (the nightly has no overall deadline on that step).

Low likelihood (the feeds are curated), but a hijacked feed or a CDN serving
junk is exactly the failure mode, and the cost is a hung nightly.

## Goal

- Wrap the parse:

  ```python
  try:
      parsed = await asyncio.wait_for(
          asyncio.to_thread(_parse_feed, resp.content, name), timeout=_PARSE_TIMEOUT
      )
  except TimeoutError:
      logger.warning("sff news: feed parse timed out, skipping: %s", name)
      return []
  ```

  with `_PARSE_TIMEOUT = 15` as a module constant.
- `asyncio.wait_for` cancels the awaitable on timeout, but a `to_thread`
  worker can't actually be interrupted — the thread keeps running to
  completion in the background. That's acceptable (it's one pool thread, the
  default pool has room, and it finishes eventually) — just note it in a
  comment so the next reader doesn't think the thread is killed.
- Optionally also cap `resp.content` size before parsing (e.g. skip if
  `len(resp.content) > 5_000_000` with a log) — a 5 MB feed is already
  absurd. Cheap belt-and-braces.

## Acceptance criteria

- A feed whose parse hangs (test: monkeypatch `_parse_feed` to `time.sleep`)
  is skipped after `_PARSE_TIMEOUT`, the other feeds still return.
- `cd backend && python -m pytest -q tests/test_sff_news_service.py` green
  (set `_PARSE_TIMEOUT` small in the test, or monkeypatch it).
- Full `python -m pytest -q` + `-m corpus` green.
- One commit. Update `prompts/README.md`, `REVIEW-2026-09-10.md` (F8 done).

## Gotchas

- Keep the existing per-feed best-effort: any exception in `_fetch_one` →
  `return []`, run continues. The timeout is just one more of those.
- Don't lower `_TIMEOUT` (the HTTP one) — some feeds are genuinely slow to
  first byte.
