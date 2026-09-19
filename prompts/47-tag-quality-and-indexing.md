# Task 47 — Tag quality and indexing: from free-form generation to something search can trust

**Status: plan only, not started. Captured 2026-09-19.** James asked to revisit
`prompts/39-llm-tag-intelligence.md`'s research now that some of it has
actually shipped, do a fresh research pass, and turn it into an executable
plan focused on two things: how the tagging pipeline itself treats tags at
generation time, and how the index/viewer consumes them afterward.

**Read first:** `prompts/38-llm-tagging.md` (the map-reduce tagging pipeline
itself — chunking, windows, `Book.llm_tags_json.full`), `prompts/39-llm-tag-
intelligence.md` (the original research: options 1–6, the libtrails deep
dive, the "no vector/graph DB at this scale" finding — still valid, don't
relitigate it), `prompts/29-semantic-search.md` (the embedding pipeline this
plan extends).

## Correction to prompts/39: two of its "unbuilt" options have since shipped

`prompts/39` (2026-09-13) recommended starting with option 2 then option 1.
Both are now live — a future session reading `39` cold would otherwise
assume they're still open:

- **Option 1 (tag-based similar books)** — `content_recs_service.py`.
  IDF-weighted cosine similarity over genres/moods/representation (weights
  at [content_recs_service.py:40](../backend/app/services/content_recs_service.py#L40)),
  self-adjusting instead of a hand-maintained stoplist, `sharedTags` shipped
  to the viewer for the "because you both have…" explainer `prompts/39`
  called out as "nearly free" — it wasn't skipped, it's live in
  `recommendations.ts`.
- **Option 2 (richer embeddings)** — `embed_input()` at
  [embedding_service.py:93-119](../backend/app/services/embedding_service.py#L93-L119)
  now folds in genres/moods/representation and `shortDescription`
  (`refresh_embeddings`, [embedding_service.py:163-175](../backend/app/services/embedding_service.py#L163-L175)).

Both **exclude themes and content warnings entirely** rather than fixing
them — themes because "the LLM rephrases the same idea differently almost
every time" (content_recs_service.py's own comment,
[lines 14-19](../backend/app/services/content_recs_service.py#L14-L19)), which is
exactly the two-tier-dedup problem `prompts/39`'s libtrails deep-dive already
diagnosed but nothing has yet fixed. **That gap is Phase A below.**

## New research this session

Three searches to check `prompts/39`'s conclusions still hold and fill a gap
it didn't cover (tag generation itself, as opposed to what's done with tags
afterward):

**Controlled vocabulary vs. free-form generation.** The literature is blunt
about the actual failure mode BookBrain has, in production, right now: *"If
half the catalog is tagged 'womenswear' and half 'women's clothing,' a
filter built on that field returns half the catalog"* — a fragmented facet,
not a similarity problem. BookBrain's own real output already shows the
same pattern independent of themes (which `39` covers) — genres/moods are
just as free-form and will fragment the same way as the library grows past
what IDF-weighting can paper over. The consistent recommendation: *"AI
handles the bulk work and proposes tags, [mapped to] your own taxonomy…
otherwise you automate the sprawl."* This is a generation-time fix, not a
downstream cleanup, and it's the piece `prompts/39` didn't address — it only
proposed post-hoc dedup for themes, never asked whether genres/moods should
be constrained at the source.

**Structured-output reliability + batching.** Confirms `prompts/39`'s
libtrails-borrowed idea (batch 5 chunks per Ollama call) is sound practice,
with a sharper caveat: *"batching can produce invalid output when the model
must return one label for each item"* in a single unconstrained blob —
native **JSON-Schema-constrained decoding** (not just "respond with valid
JSON") is what makes batch parsing reliable in practice, cited as the 2026
"gold standard" over prompt-only JSON instructions.

**Semantic tag dedup.** Confirms the Union-Find two-tier approach
`prompts/39` already borrowed from libtrails (>0.95 auto-merge, 0.85–0.95
co-occurrence-gated) matches current standard practice (cosine threshold
~0.9 is the common default elsewhere) — no better technique found, this was
already the right call on paper. The gap is that nobody built it.

## What this actually changes about the plan

`ollama_client.py`'s `generate_json()` already uses Ollama's `format: "json"`
mode ([ollama_client.py:70](../backend/app/providers/ai/ollama_client.py#L70))
— that's "syntactically valid JSON," not "matches this schema." The
tagging prompts spell the shape out in English instead
(`_SCHEMA_INSTRUCTIONS`, [llm_tagging_service.py:181-189](../backend/app/services/llm_tagging_service.py#L181-L189))
and `validate_tag_result`/`validate_chunk_result`
([llm_tagging_service.py:148-166](../backend/app/services/llm_tagging_service.py#L148-L166))
catch shape mismatches after the fact, coercing rather than rejecting
(`value if isinstance(value, list) else []`). Passing Ollama an actual JSON
Schema via `format` (it accepts either `"json"` or a schema object) turns
"the model usually gets this right" into "the model cannot emit the wrong
shape" — and is a real prerequisite for batching safely, not just a nice-to-
have, per the research above.

## Phase A — Treat tags better at generation time (do this first — everything downstream inherits its output)

1. **Constrain genres/moods to a controlled vocabulary.** Pass an actual
   JSON Schema (not `format: "json"`) to Ollama with `genres`/`moods` as
   `enum` arrays drawn from a curated list (seed it from the ~553 books
   already tagged — pull the current distribution, hand-curate down to a
   sane closed set, James reviews once). Add a capped escape hatch (e.g.
   `otherGenre`, max 1 value, only used when nothing in the enum fits) so
   a genuinely novel genre isn't silently dropped — surfaced somewhere
   for periodic review, per the "AI proposes, human curates" pattern above.
   Leave `themes`/`contentWarnings`/`representation` free-form — they're
   inherently open-ended in a way genre/mood aren't — and fix those via
   steps 2–3 instead.
2. **Stoplist + prompt instruction against bare generic themes.** Adapt
   libtrails' ~30-word list (`power`, `identity`, `survival`, `growth`,
   `love`, …) into both a prompt instruction ("must be a specific
   multi-word phrase, not a bare abstract noun") and a post-hoc filter in
   `validate_chunk_result`/`validate_tag_result`. BookBrain's own data
   already shows the problem (*Grey Sister*'s themes included bare
   "identity", "survival" per `prompts/39`) — this is a same-day, prompt-
   plus-validator change.
3. **Real two-tier theme dedup**, finally building what `prompts/39`
   scoped: MiniLM-embed each distinct theme string (the model's already
   vendored, no new dependency), Union-Find merge at >0.95 cosine
   unconditionally, 0.85–0.95 only when the two themes also co-occur on a
   shared book. Run it as a batch pass over existing data first (there are
   already hundreds of theme strings across 553 tagged books to dedup
   against), then incrementally on each new book's reduce output. This is
   what unlocks themes for Phase B — right now they're excluded everywhere
   specifically because this doesn't exist yet.
4. **JSON-Schema `format`, not `format: "json"`.** Define the schema once
   (map-step and reduce-step shapes differ slightly — see
   `_build_map_prompt`/`_build_reduce_prompt`), pass it as `format` in
   `generate_json()`. Cuts `OllamaBadResponse` retries and is the
   prerequisite for step 5.
5. **Batch 3–5 chunks per map call**, libtrails' pattern (`--- Passage N
   ---`, one JSON object keyed by passage number, per-chunk fallback if the
   batch parse fails) — now safe because of step 4's schema constraint.
   This is the throughput lever: BookBrain currently spends one full Ollama
   round-trip per chunk (measured ~12.5 min/book including window-gating,
   33-42 calls for a typical novel per `prompts/39`); batching directly
   cuts round-trip count 3-5x, independent of and additive with whatever
   fixes today's Ollama hang. Worth sizing against `OLLAMA_NUM_CTX=8192`
   (`.env`) — `qwen3:14b` supports up to 40,960 tokens per `prompts/39`, so
   there's real headroom to raise `ollama_num_ctx` rather than shrink chunk
   size to fit multiple passages in.

## Phase B — Indexing: let search and recs use the now-cleaner tags

Blocked on Phase A.3 (theme dedup) for the theme-specific items; B2/B4 are
independent and can go anytime.

1. **Feed deduped themes + `longSummary` into `embed_input`.** Currently
   only `shortDescription` + genres/moods/representation
   ([embedding_service.py:163-175](../backend/app/services/embedding_service.py#L163-L175)).
   Themes were correctly left out while they were noisy — once A.3 lands,
   add the deduped canonical theme labels. Keep `longSummary` out of the
   *search* embedding if it risks spoiling relevance toward plot mechanics
   over vibe (worth eyeballing on real data before committing either way).
2. **RRF fusion of keyword + semantic search.** `semanticSearch.ts`
   currently runs standalone (pure cosine, no fusion with the existing
   title/filename match mode — `App.tsx`'s Keyword|Meaning toggle is an
   either/or, not a blend). `score += 1/(60 + rank + 1)` per signal, no
   score normalization needed, client-side, cheap. Directly actionable per
   both `prompts/39` and this session's research.
3. **MMR reranking on `content_recs_service`'s top-8.** Prevents the
   current `_KEEP = 8` list
   ([content_recs_service.py](../backend/app/services/content_recs_service.py))
   from being five near-duplicate entries of the same sub-genre. Cheap,
   `prompts/39` flagged it as "worth building in from the start" — it
   wasn't, this closes that gap.
4. **Hard content-warning exclude filter in the viewer.** The data already
   exists and is already shown — just not filterable
   ([BookRow.tsx:441-446](../library-viewer/src/components/BookRow.tsx#L441-L446),
   collapsed detail text only). The genre facet-chip pattern already exists
   to copy (`genreFacets`/`FilterKey`,
   [App.tsx:501](../library-viewer/src/App.tsx#L501) and
   [:1126](../library-viewer/src/App.tsx#L1126)) — this is near-zero new
   infrastructure, `prompts/39` called it out as a good standalone feature
   independent of everything else in this plan.

## Phase C — Defer until A/B prove out on real, clean data

Unchanged from `prompts/39`'s sequencing, restated briefly since they still
apply and shouldn't be started early: **auto-clustered shelves** (c-TF-IDF
naming, UMAP+HDBSCAN or k-means) — now genuinely worth doing once themes
are deduped rather than raw; **relationship graph** (hybrid PMI+embedding
edges, hub removal before Leiden — non-optional per libtrails' own
mega-cluster post-mortem, CPM not modularity); **natural-language mood
query parsing**; **GraphRAG / "ask your library"** — real techniques, not
starting points, per `prompts/39`'s original judgment, which this session's
research didn't find reason to revise.

## Sequencing

**A.2 → A.4 → A.5 → A.3 → A.1 → B.2/B.4 (either order, independent) → B.1 →
B.3 → revisit Phase C.**

A.2 (stoplist) and A.4 (schema format) are same-day, low-risk, no migration.
A.5 (batching) is the throughput fix and depends on A.4. A.3 (theme dedup)
is the biggest unlock for Phase B and can run as a batch job against the
553 already-tagged books immediately, independent of A.5. A.1 (controlled
vocabulary for genres/moods) is the highest-effort item — needs a real
curated list and touches every future tagging call — sequence it after the
cheaper wins land and after seeing A.3's dedup output (it'll surface the
actual genre/mood fragmentation in the existing data, which should inform
the curated list rather than guessing at it blind). B.2 and B.4 need
nothing from Phase A and can be picked up in parallel by a different
session if useful.

One dependency outside this plan: **none of Phase A can accumulate new data
while Ollama is wedged on JamesGaming** (see this session's earlier
investigation — `/api/generate` hangs indefinitely on that host as of
2026-09-19, unrelated to this plan, needs a restart on the JamesGaming side
before any tagging throughput work can be measured live).

## Update — 2026-09-19, A.3 shipped (theme dedup), with a different middle-tier gate

Built as `theme_dedup_service.py`; results in each done book's
`llm_tags_json.full.themesCanonical` (raw `themes` untouched). Re-runs over
the whole library after every reduce step, and on demand via
`POST /api/library/themes/refresh`. Nothing reads it yet — that's B.1.

**The co-occurrence gate from prompts/39 was measured and dropped.** On the
553 tagged books it rejected 321 of the 0.85–0.95 pairs and accepted 3, and
the rejections were the real duplicates ("moral dilemma"/"moral dilemmas",
"consequences of war"/"war and its consequences"), while one of the 3
accepted was wrong ("sacrifice" into "love and sacrifice"). libtrails
extracts ~117 topics per book, so paraphrases share books there; BookBrain's
reduce step emits ~10 per book and already dedups within one, so a shared
book is evidence two themes are *different*. Ungated, the band is too loose
("father-daughter" ~ "father-son relationships"). Replaced with a lexical
gate: the middle band merges only when both strings reduce to the same set
of content-word stems. The >0.95 tier is unchanged.

**What it buys, measured:** 2,074 raw theme strings → 1,615 after case/
whitespace folding → 1,476 canonical (123 merged groups, largest 4, all 123
checked by hand, no wrong merges). Single-book themes 1,113 → 970. That's
a smaller cut than prompts/39 implied: most of the remaining fragmentation
is *semantic*, not phrasing ("power and control" / "power and corruption" /
"power dynamics" are distinct strings about overlapping ideas, and
correctly aren't merged at these thresholds). So for B.1, feed canonical
themes to the *embedding* (which handles near-meanings on its own), and be
cautious about exact-match uses like content_recs' IDF — the bare generic
themes A.2 targets ("survival" on 215 books, "identity" 82, "moral
ambiguity" 220) are the bigger hub problem there, and dedup doesn't touch
them.

**Ollama health check gives false positives.** On 2026-09-19 at ~08:45 NZST
the trivial `"Say OK."` generate returned in 10s, but a real map call (a
~7k-token prompt at `num_ctx` 8192) got no first token in 200-300s, and
over the next minutes even 10- and 100-word prompts hung intermittently
while others answered in 4s. A tiny prompt isn't enough to tell whether
Ollama is serving: test with a real-sized map prompt before starting
throughput work (A.5).

## Update — 2026-09-19, A.2 and A.4 shipped; A.5 shipped as something other than batching

Once Ollama was restarted, the remaining steps ran in the planned order.

**A.2 (stoplist):** both prompts now ask for specific multi-word themes,
and the validators drop an exact bare match against ~50 generic nouns
(`theme_dedup_service.GENERIC_THEMES`), which also filter
`themesCanonical` for books tagged earlier. Same book, same model: the
old prompt gave *The Serpent Sea* 8 themes, 5 of them bare generics
("belonging", "identity", "survival", "community", "legacy"). The new
one gave 10 specific phrases ("Restoration of a Broken Community",
"Coexistence Between Species").

**A.4 (JSON-Schema `format`):** a whole book (41 calls) with the schema
parsed and validated every response, and took 4.7 min vs 4.8 min in
`"json"` mode, so constraining costs no speed.

**A.5: batching was measured and rejected.** On a real full-size chunk,
prefill of ~6.3k tokens took 2.2s (~2,900 tok/s), while generating the
~200-330-token summary took 5-8s (42 tok/s). Generation dominates, and N
passages in one call still means N summaries. Holding N chunks also
needs num_ctx ~N × 8192. At 8192, qwen3:14b is 10.3 GB and fits
JamesGaming's GPU with nothing to spare. At 24576 it's 13.6 GB, Ollama
could only put 10.3 GB on the GPU, and three passages batched took 158s
vs 22s one at a time. (That 8192 fits *exactly* is also the best
available explanation for this morning's hang: anything else using the
GPU pushes the model partly onto the CPU. Unconfirmed.)

What the measurements did point at, and what shipped instead:
- **The chunker made ~30% more map calls than needed.** It split each
  chapter bigger than the budget into full-size pieces plus a leftover
  that became its own chunk, and nearly every chapter of a real novel is
  bigger than the budget. A 1k-char leftover still costs ~6s, because
  generation dominates. It now packs the book as one continuous stream.
- **Every step re-downloaded the whole EPUB from Drive**, 3-4s through a
  freshly spawned subprocess. The in-flight book's bytes are now kept
  between ticks.

**Measured against the live pipeline:**
- *Before:* 12 books that finished back to back on 2026-09-18,
  re-chunked to recover their step counts. 481 steps over 101 min is
  12.6s per step, **7.1 books/hour**, in line with the 7.5/hour average
  across all 553.
- *After:* the real `tick()` against the live DB, Drive and Ollama for
  25 min, with only the window check bypassed. Steps averaged 9.4s. The
  *Books of the Raksura* omnibus took 12.9 min (old path: 121 × 12.6s ≈
  25 min), *Strange Highways* 6.7 min (≈ 10.7), *Stories of the Raksura
  Vol. 1* 2.9 min (≈ 4.6). The same 12 novels would now need 343 steps
  at 9.4s ≈ 54 min, **~13.4 books/hour, about 1.9×.**
- *Next lever, not done:* 9.4s per step is still ~2s above the Ollama
  call itself. Each tick re-queries every organised file and re-extracts
  the whole book.

**Separate finding:** 26 books always fail with "no extractable text".
They re-queue every 24h and are nearly all Dean Koontz titles. *Hell's
Gate* extracts to zero spine documents, so this is the EPUB parser or
those files, not tagging. Not investigated.

## Update — 2026-09-19, A.1 vocabulary approved (part 1 of 2)

James approved the closed genre/mood sets and the raw→curated mapping as
drafted. They live in `backend/app/services/tag_vocab.py`, which nothing
imports yet. Part 2 (enum schemas, `otherGenre`, backfill, consumers) is
still to do.

**Measured on the 556 books with a done full pass:** 202 distinct raw
genres and 177 raw moods (casefolded) → 46 genres and 23 moods. Every raw
value in use has a mapping entry. Fifteen rare but real genres (poetry,
epistolary, legal fiction…) map to `otherGenre` rather than being dropped.
After mapping, one book (*Aura*) has no genres and one (*Nightmare
Journey*, whose only mood was "Neutral") has no moods.

**The bigger problem is overuse, not fragmentation.** After mapping,
`tense` is on 544 of 556 books, `reflective` 492, Adventure 319, Mystery
287 (*Sorcery of Thorns*, *Winner's Crime*). Books averaged 6.3 genres and
7.4 moods, apparently because the reduce step unions every chunk's
evidence. Many raw moods were a character's state in one scene (`desperate`
143, `determined` 121, `relieved` 23), not the book's tone; those map to
drop. The mapping can't fix overuse in existing data. Approved for new
output: at most 4 genres and 5 moods (`MAX_GENRES`/`MAX_MOODS`), most
defining first, plus prompt guidance for the overused values. A re-tag of
old books would be the only fix for them (a done book keeps no
chunkResults, so there's no reduce-only redo: ~42 window-hours). **Not
approved; don't start one.**

Notable calls, all approved: `urban fiction` → Urban Fantasy (all 10 were
Rivers of London / Alpha & Omega); `epic fiction` → Epic Fantasy and `dark
fiction` → Dark Fantasy (nearly all co-occur with Fantasy); `political
thriller` → Political Intrigue only (also mapping it to Thriller would add
71 books to an already overused value); `supernatural`,
`speculative fiction`, `religious fiction`, `psychological fiction`,
`philosophical fiction`, `drama` dropped. Genres are Title Case, moods
lowercase.

## Sources

New this session:
- [What is a controlled vocabulary? — Sanity](https://www.sanity.io/glossary/controlled-vocabulary)
- [Controlled vocabulary — Wikipedia](https://en.wikipedia.org/wiki/Controlled_vocabulary)
- [Using Entity Labels to Automatically Tag Memories — Hindsight](https://hindsight.vectorize.io/blog/2026/06/02/entity-labels-automatic-memory-tagging)
- [Batch Processing With LLMs in 2026 — projectsupply.in](https://projectsupply.in/blog/batch-processing-llms-cost-effective-2026)
- [Getting Structured Output From LLMs in 2026 — projectsupply.in](https://projectsupply.in/blog/structured-output-llm-2026)
- [Reliable JSON from Any LLM: Pydantic + Zod (2026) — TECHSY](https://techsy.io/en/blog/llm-structured-outputs-guide)
- [Map Reduce for Large Document Summarization with LLMs — f22labs](https://www.f22labs.com/blogs/map-reduce-for-large-document-summarization-with-llms/)
- [How SemHash Simplifies Semantic Deduplication for LLM Data — Medium](https://medium.com/@sreeprad99/how-semhash-simplifies-semantic-deduplication-for-llm-data-a0b1a53e84fe)
- [Semantic Deduplication — NeMo Curator (NVIDIA)](https://docs.nvidia.com/nemo/curator/curate-text/process-data/deduplication/semdedup)

Carried forward from `prompts/39` (unchanged, not re-verified this session):
see that file's own Sources section — the libtrails deep dive
(github.com/seaberger/libtrails) and RRF/Tagsplanations/BookGraph citations
in particular still ground Phase B/C above.
