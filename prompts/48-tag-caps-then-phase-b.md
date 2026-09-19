# Task 48 — Stop genre/mood caps acting as quotas, then prompts/47 Phase B

**Status: plan only, not started. Written 2026-09-19.** This picks up
straight after `prompts/47` A.1 part 2 shipped. Read `prompts/47`'s
"Update — 2026-09-19, A.1 part 2 shipped" section first: it has the
measurements this plan starts from. `prompts/47` still owns the Phase B
designs. This file only sets the order and adds what changed since they
were written.

**Order:** Step 1 (the cap problem) → Step 2 (B.1) → Step 3 (B.3) →
Step 4 (B.2/B.4, if nobody has done them) → the smaller items at the end
when there's a gap. Step 1 is first because it's the only time-sensitive
one: every night it waits, ~100 more books get tagged with filler values,
and a finished book can only be fixed by a re-tag.

**Conventions (unchanged from prompts/47):** work on `main`, one commit per
step, `cd backend && .venv/bin/pytest` green before each commit, push when
green, deploy with `sudo systemctl restart bookbrain`. The repo is
`/opt/bookbrain` (ignore the Windows path in `prompts/README.md`). James
tests against the running app, so run the commands yourself. Record what
shipped and what was measured in an "Update" section at the end of this
file.

**Ollama rules (all still apply):** allowed tagging hours are 9pm-8am every
night plus weekdays 9am-3pm, Pacific/Auckland. Outside those hours, use the
GPU only for what's needed to verify. Before any Ollama work, send one
real-sized map prompt, streamed, with a timeout; a "Say OK." prompt can
pass while real calls hang. Don't raise `OLLAMA_NUM_CTX`: at 8192,
qwen3:14b fits JamesGaming's GPU with nothing to spare, and the map prompt
is already ~6.7-7.0k tokens.

**Settled, don't reopen:** no vector or graph DB at this scale
(`prompts/39`); chunk batching was measured and rejected (`prompts/47`
A.5); the approved vocabulary and mapping in `tag_vocab.py` (if a mapping
looks wrong, tell James rather than changing it); no re-tag of old books
without James's say-so.

## Step 1 — Stop the caps acting as quotas (needs James's OK before shipping)

### The problem, as measured on 2026-09-19

`genres`/`moods` are enum arrays capped at 4/5 (`MAX_GENRES`/`MAX_MOODS`)
in both `CHUNK_RESULT_SCHEMA` (map step) and `TAG_RESULT_SCHEMA` (reduce
step). On *The Serpent Sea* (book 596):

- Every map call returned exactly 5 moods; 22 of 26 returned exactly 4
  genres. The reduce also returned exactly 4 and 5.
- The spare slots get filler. Across the 26 sections: Fantasy 26,
  Adventure 26, **Dark Fantasy 18**, Epic Fantasy 14, Mystery 14, Political
  Intrigue 2. The reduce kept Dark Fantasy, which is wrong for Raksura, and
  dropped Mystery.
- Mood tallies: tense 25, unsettling 20, adventurous 16, emotional 12,
  reflective 10, atmospheric 10, then smaller counts.
- Adding "4 is a limit, not a target: most books need two or three" to
  the prompt changed nothing (same genres, 5 moods again). **Don't try
  more prompt wording.** Reverted; not in the code.

### Hypothesis

The filler starts in the map step. Each section fills its slots, the
reduce step sees those values repeated as evidence across many sections,
and it keeps them. With fewer map slots, a section has to name only what
it's most sure of, and borderline values like Dark Fantasy should show up
in far fewer sections.

### The experiment

1. **Lower the caps in the map schema only**: 2 genres and 3 moods.
   Keep 4/5 on the reduce schema. In `llm_tagging_service.py` that means:
   - new constants (e.g. `_MAP_MAX_GENRES = 2`, `_MAP_MAX_MOODS = 3`), used
     by `CHUNK_RESULT_SCHEMA` and by `validate_chunk_result` (which today
     goes through `_apply_vocab` with the reduce caps);
   - the map prompt's rules must state the map caps. `_GENRE_RULE` and
     `_MOOD_RULE` currently embed `MAX_GENRES`/`MAX_MOODS` and are shared by
     both prompts, so turn them into small functions that take the cap;
   - `test_schemas_confine_genres_and_moods_to_the_capped_enum` will need
     updating.

   Changing map caps doesn't change chunking, so a book that's mid-mapping
   at deploy just continues.
2. **Dry-run two books** in an allowed window, writing nothing to the DB:
   - *The Serpent Sea* (596), 26 map calls + reduce, ~5 min. Compare to
     the numbers above.
   - *It Starts with Us* (16), a contemporary romance. Its stored curated
     genres are just Contemporary Fiction, Romance, so any filler in slots
     3-4 is obvious. Stored curated moods: tense, emotional, hopeful,
     reflective, bittersweet, romantic.

   The 2026-09-19 dry-run script lived in a session scratchpad and is
   gone. Rebuild it (~15 min). What mattered:
   - load the book and its stored `llm_tags_json.full` by book id;
   - get bytes via `llm_tagging_service._book_bytes` and cache them to a
     local file so reruns don't hit Drive;
   - chunk with `chunk_documents(..., target_chars=_content_budget_chars(num_ctx))`;
   - call `/api/generate` **streamed** with `format` = the schema,
     `think: False` and `num_ctx`, under a first-token timeout (~90s) plus
     an overall `asyncio.wait_for`;
   - run `validate_chunk_result`/`_build_reduce_prompt`/`validate_tag_result`
     exactly as `_run_full_step` does;
   - log each section's genres/moods, `prompt_eval_count`, `eval_count`
     and `eval_duration`.

   The download runs in a spawned subprocess, which re-imports the script,
   so the script **must** use `if __name__ == "__main__":`.
3. **Save each section's genres/moods from these runs to a file.** If the
   reduce still fills 4/5, the fallback below can be simulated offline
   from that file, with no extra GPU.

### What counts as success

- *The Serpent Sea* loses Dark Fantasy, or it at least drops out of most
  sections.
- *It Starts with Us* comes back with fewer than 4 genres, or 4 that are
  all defensible.
- Nothing clearly right disappears (Fantasy/Adventure on 596; Romance on
  16).
- Per-call time doesn't get worse. Fewer items should make it slightly
  faster. Baseline: ~9s per map call, ~348 output tokens, 300s for all of
  596.

### If the reduce still fills every slot

Wording failed once, so the next lever is code: a deterministic filter
after the reduce. Keep a reduce genre/mood only if it appeared in at least
X% of sections' evidence, and always keep the first (most defining) value.
The inputs are already there: `full["chunkResults"]` is still in memory at
reduce time in `_run_full_step`. Pick X by simulating on the saved section
tallies from both books before writing any code. Note that on today's
Serpent Sea data Dark Fantasy was in 69% of sections, so this only works
**together with** the lower map caps, not instead of them.

### Decision for James before shipping

Lower map caps depart from what he approved ("capped at
MAX_GENRES/MAX_MOODS" in both schemas). Show him the two books'
before/after tables and get a yes before deploying.

Also tell him **how many books were tagged with quota-filled tags in the
meantime**: done books whose `generatedAt` is after 2026-09-19 03:32 UTC
(the A.1 deploy). Whether to re-tag them is his call. They're a small
fraction of the queue, so re-tagging is cheap if he wants it.

## Step 2 — B.1: canonical themes into the embeddings

Unblocked since A.3. `llm_tags_json.full.themesCanonical` exists on every
done book; generic bare themes are already filtered out of it.

- `embedding_service.refresh_embeddings` builds `tags` from `_llm_tags()`
  genres/moods/representation (curated genres/moods since A.1). Add
  `themesCanonical`, read straight from `book.llm_tags_json["full"]`.
  **Don't** add it to `library_index_service._LLM_TAGS_KEYS`: that would
  ship it in the viewer index for no reason.
- Keep `longSummary` out for now: it risks pulling search toward plot
  mechanics and ending spoilers (prompts/47 B.1). Revisit only after
  seeing themes on real queries.
- Update the `embed_input` docstring, which still says themes are
  excluded.
- **Measure before and after:** pick ~5 "vibe" queries James might type
  ("found family in space", "grief", "political scheming at court",
  "cozy magic", "survival horror"). Save the top 10 for each from the
  current embeddings first (the vectors are in `books.embedding`; embed the
  query with `embedding_service.embed_texts`). Then re-embed and compare.
  CPU only, no Ollama.
- **Deploy:** restart, then `POST /api/library/embeddings/refresh?limit=5000`
  followed by `POST /api/library/embeddings` (writes the viewer's
  sidecar). Every tagged book re-embeds, ~1-2 min.

## Step 3 — B.3: MMR on content recs' top 8

`content_recs_service._top_matches` takes the top 8 by cosine, with a
2-per-series cap and a 2-shared-tags minimum. Add MMR (maximal marginal
relevance) on top:

- Take a candidate pool (~top 30 after the existing filters). Pick
  greedily by `λ·sim(source, c) − (1−λ)·max sim(c, already picked)`,
  starting at λ ≈ 0.7. The candidate-to-candidate similarities come from
  the same `sim` matrix, so no new compute.
- Keep the series cap and `_MIN_SHARED_TAGS`.
- **Measure:** recs were just reshuffled by A.1 (only 32% of the old top 8
  survived on average). So snapshot `content_recs_json` for all books
  before the change and report:
  - list overlap;
  - the same-author/same-series proxy (currently 30.4% / 11.0%);
  - a diversity number, e.g. mean pairwise tag-cosine within each top 8.

  Tell James the numbers, since recs will move again.
- **Deploy:** `POST /api/library/content-recs/refresh`, then
  `POST /api/library/recommendations` (the viewer's file).

## Step 4 — B.2 and B.4, if nobody has done them

Both need nothing from Phase A and may be picked up by another session.
**Check `git log` first.** Designs are in `prompts/47` Phase B:
- **B.2:** RRF fusion of keyword and semantic search in
  `library-viewer/src/lib/semanticSearch.ts`/`App.tsx`.
- **B.4:** a hard content-warning exclude filter, copying the genre facet
  chip pattern.

Both are `library-viewer` only, which deploys to GitHub Pages on push. Run
`npm run build`, `npx vitest run` and `npm run lint` there before
committing.

## Smaller items (when there's a gap)

- **Per-step overhead in `tick()`.** Each step spends ~2s outside the
  Ollama call: it re-queries every organised file and re-extracts and
  re-chunks the whole book. With map calls now ~9s, that's ~20% of a step.
  Caching the in-flight book's chunks next to `_download_cache` is the
  obvious fix. The chunk count must stay deterministic, because it's what
  `chunksTotal` checks against. Measure the step time before and after,
  like prompts/47 A.5 did.
- **26 books that always fail with "no extractable text".** They re-queue
  every 24h and are nearly all Dean Koontz. *Hell's Gate* extracts to zero
  spine documents, so this is the EPUB parser or those files, not tagging.
  Start with `extract_full_text_documents_safely` on that one file.

## Out of scope

Phase C (auto-shelves, relationship graph, mood-query parsing, ask your
library), a re-tag of old books, and changes to the approved mappings.

## Update — 2026-09-19, Step 1 built and dry-run (NOT deployed)

Commit `7ec10a6`: map-only caps `_MAP_MAX_GENRES = 2`, `_MAP_MAX_MOODS = 3`
in `CHUNK_RESULT_SCHEMA`, the map prompt and `validate_chunk_result`; the
reduce keeps 4/5. `_GENRE_RULE`/`_MOOD_RULE` became `_genre_rule(cap)`/
`_mood_rule(cap)`. Tests green (1046). **Waiting on James's yes before
`systemctl restart`.** The service runs from this working tree, so any
restart before then deploys it.

Dry-runs 22:40-23:30 NZST, nothing written to the DB (SQLite opened
`mode=ro`, OAuth token refreshed in memory only). The live job was running
back-to-back throughout, so wall times include queueing; "compute" below
is Ollama's `prompt_eval_duration + eval_duration`. A 4/5-caps mode of the
script was checked byte-for-byte against the pre-`7ec10a6` map prompt and
schema.

**The Serpent Sea (596), 26 sections**

| | stored → mapped | 4/5 map (prompts/47) | 2/3 map |
|---|---|---|---|
| genres | Fantasy, Science Fiction, Adventure, Mystery | Fantasy, Epic Fantasy, Adventure, Dark Fantasy | Fantasy, Adventure, Epic Fantasy, Mystery |
| moods | atmospheric, tense, hopeful, reflective | adventurous, tense, unsettling, hopeful, reflective | adventurous, tense, reflective, hopeful, mysterious |

Section tallies at 2/3: Fantasy 26, Adventure 19, Mystery 5, Epic Fantasy 2,
**Dark Fantasy 0** (was 18). Moods: tense 25, adventurous 13, emotional 7,
atmospheric 6, reflective 6, mysterious 5, unsettling 5 (was 20), dark 4,
hopeful 3, melancholic 3, bittersweet 1. Map calls: 9.7s compute mean,
298 output tokens (was ~348), 40.9 tok/s. Reduce 13.5s. Sum of compute 265s.

**It Starts with Us (16), 17 sections**

| | stored → mapped | 4/5 map | 2/3 map |
|---|---|---|---|
| genres | Contemporary Fiction, Romance | Romance, Contemporary Fiction, Slice of Life, Coming-of-Age | Romance, Contemporary Fiction |
| moods | tense, emotional, hopeful, reflective, bittersweet, romantic | emotional, bittersweet, hopeful, reflective, tense | emotional, hopeful, reflective, romantic, tense |

Section tallies, 4/5: Romance 17, Contemporary Fiction 17, Slice of Life 9,
Coming-of-Age 6, Mystery 5, Dystopian 4, Psychological Thriller 3,
Thriller 2, Erotica 1. At 2/3: Romance 17, Contemporary Fiction 16,
Mystery 1. Moods at 2/3: emotional 17, tense 9, hopeful 8, reflective 7,
romantic 6, bittersweet 3, melancholic 1. Map compute 10.2s (316 tok) at
4/5 vs 9.4s (280 tok) at 2/3.

**Findings.** Every map section still fills every slot (26/26, 17/17), but
with fewer slots the filler mostly disappears. The reduce still filled 4
genres on 596 (Epic Fantasy from 2 sections, Mystery from 5) and 5 moods
on both books; on 16 it stopped at 2 genres.

**Section-count filter, simulated offline** (keep a reduce value if in
≥X% of sections, always keep the first):
- 596: X=20% → Fantasy, Adventure / adventurous, tense, reflective. X≥25%
  → moods adventurous, tense only. Mystery sits at 19%, right at the edge.
- 16: X≤30% changes nothing; X=40% drops *romantic* (35%) from a romance,
  X=50% leaves emotional, tense.
- So X≈20% is the only value that helps 596 without hurting 16, and two
  books is too small a sample to pick it. No code written.

One map call (596 section 20, first attempt) streamed for the full 300s
overall timeout; the same section re-run alone took 9.4s and the full
re-run had no problem. Not reproduced; unexplained.

**Tagged since the A.1 deploy** (generatedAt > 2026-09-19 03:32 UTC):
17 books as of 23:31 NZST, all exactly 4 genres / 5 moods. 16 are Wild
Cards: Dark Fantasy on 10 of them, Superhero on only 1.
