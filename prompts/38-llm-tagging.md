# Task 38 — Local LLM (Ollama) tagging + descriptions

**Read first:** `prompts/25-hardcover-integration.md` (posture on optional,
never-fails-the-run providers), `prompts/37-openbooks-acquire.md` (the
allowed-window / "one thing per tick" background-job pattern this copies),
`SPEC.md` § "EPUB safe parsing".

## The ask

James runs Ollama on his gaming PC ("JamesGaming"), reachable from the
server over Tailscale. He wants BookBrain to read each organised book's
actual text and derive:

- `ageRating`, `genres`, `moods`, `themes`, `representation`,
  `contentWarnings`, `confidenceNotes` — same shape of tags Hardcover's
  catalogue metadata already contributes (`hardcover_recs_service`'s
  `meta`), but grounded in the book itself rather than crowd data.
- `shortDescription` — a spoiler-free 2-3 sentence blurb.
- `longSummary` — a fuller synopsis that may include the ending.

Two independent read strategies, kept separate so they can be compared
before either is trusted:
- **excerpt**: opening + a middle sample + ending, one Ollama call.
- **full**: the whole book. Map-reduced, since a novel is routinely
  100-150k tokens and Ollama's default context window is a few thousand —
  a single call would just silently truncate. Each chunk gets a cheap
  "what happens here + what evidence is here" call; a final call reduces
  all chunk summaries into the same schema as the excerpt pass.

Deliberately a slow drip: **one unit of work per tick**, only inside
allowed windows (anytime overnight, weekdays 9am-3pm), only while Ollama
answers — never competing with James for the GPU. No batching, no rush.

## Why not fold this into `hardcover_json.meta`

`Book.hardcover_json` already carries a `meta` sub-object with a very
similar shape (genres/moods/contentWarnings from Hardcover's catalogue).
Nesting the new fields there was considered and rejected:
`hardcover_recs_service` does full-object replacement
(`book.hardcover_json = {...}`) on every refresh, which would silently
wipe LLM-derived data the next time it ran. Stored instead in its own
`Book.llm_tags_json` column: `{"excerpt": {...}, "full": {...}}`.

## Why not `ebooklib` for text extraction

BookBrain's own EPUB parser (`app/providers/epub/parser.py`) already has an
XXE/zip-bomb-hardened `SafeZipReader` + OPF walk, reused here
(`extract_full_text_documents[_safely]`) rather than adding a second,
unhardened EPUB library that would need to re-solve the same safety
problem.

## Scheduling

Follows the existing `OPENBOOKS_AUTOGET_ENABLED` pattern exactly
(`app/jobs/scheduler.py`): an `IntervalTrigger` job (~5 min, jittered)
gated by a DB toggle (`LLM_TAGGING_ENABLED`), registered/unregistered from
`PUT /api/library/llm-tagging`. The interval trigger fires constantly;
`llm_tagging_service.tick()` itself decides whether the window/
reachability/toggle conditions are actually met and no-ops otherwise —
same division of responsibility as auto-get.

## State machine for the full-text pass

Progress lives entirely in `Book.llm_tags_json.full`, not in a separate
table or temp files, so a window closing mid-book loses no work:

```
not started -> {status: "mapping", chunksTotal, chunksDone: 0, chunkResults: []}
            -> ... one chunk mapped per tick ...
            -> {status: "reducing", chunksTotal, chunksDone == chunksTotal, chunkResults}
            -> {status: "done", ageRating, genres, ..., shortDescription, longSummary, generatedAt}
```

A tick re-downloads and re-chunks the book from Drive every step rather
than persisting raw chunk text in the DB — parsing is cheap and already
thread-pooled; re-chunking is deterministic as long as `ollama_num_ctx`
hasn't changed, and a chunk-count mismatch (e.g. `num_ctx` raised
mid-flight) triggers a clean restart rather than mapping against stale
boundaries.

At most one book's full-text pass is in flight at a time: a tick always
resumes an in-progress `full` pass before starting a new one, and every
book gets its (single-call, cheap) excerpt pass done before any book's
(slow, multi-call) full pass starts. Simpler to reason about, and gives
broad excerpt coverage across the library quickly.

## Failure handling

A per-book/per-step failure (malformed JSON, no extractable text, a schema
violation) is recorded as `{"error": ..., "failedAt": ...}` and retried
after a 24h backoff — never retried every single tick. A genuinely
unreachable Ollama host (rather than a bad response) is NOT recorded as a
book failure — the tick just skips silently and tries again next time, per
`app/providers/ai/ollama_client.py`'s `OllamaUnavailable` vs
`OllamaBadResponse` split.

## Config

- `ollama_host`, `ollama_model` (default `qwen3:14b`), `ollama_num_ctx`
  (default 8192), `ollama_timeout_seconds` — env vars in `config.py`
  (deployment-level, like `openbooks_ws_url`), not DB settings.
- `LLM_TAGGING_ENABLED` — DB setting, toggled via
  `GET`/`PUT /api/library/llm-tagging` (also reports `configured` —
  whether `ollama_host` is set — and rough progress counts).

## Update — 2026-09-13, excerpt pass dropped

The two passes ran side by side on one real book (*Holy Sister*) and the
results were put in front of James directly: full-text pulled out roughly
1.5-2x more genres/moods/themes/warnings, and its confidence notes were
noticeably more self-aware ("high confidence across multiple sections" vs.
"based on limited sample text"). He preferred full-text clearly enough that
running two passes per book — one costing 34 Ollama calls and ~5 minutes,
the other 1 call and a few seconds, for a result he'd only use one of —
stopped making sense. The excerpt pass (`sample_excerpt`, its prompt, and
the priority logic that ran it across the whole library before any book's
full pass could start) was removed; `tick()` now goes straight to advancing
a book's full-text map-reduce. Already-stored `llm_tags_json.excerpt` data
from before this change is harmless leftover, just nothing new writes it.

## Not done here (deliberately)

- No writeback into `Book.description` or the main
  `genres`/`moods`/`contentWarnings` fields the viewer already reads —
  same "compare before trusting" posture as the two passes above each
  other. `shortDescription` is a strong future candidate to replace
  `description_service`'s blind (title/author-only) Anthropic fallback,
  once James has eyeballed enough of them.
- No admin-UI toggle yet — the API is there
  (`PUT /api/library/llm-tagging {"enabled": true}`); a frontend checkbox
  is a small follow-up if wanted.
- No exposure of `llm_tags_json` in the `bookbrain-index.json` sidecar —
  the library-viewer doesn't show any of this yet, pending that same
  comparison.
