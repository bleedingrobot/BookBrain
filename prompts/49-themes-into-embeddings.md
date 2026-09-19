# Task 49 — B.1: canonical themes into search embeddings

**Status: plan only, not started. Written 2026-09-19.** This is `prompts/48`
Step 2, split into its own file because Step 1 (the tag-cap fix) is done and
deployed. Read `prompts/47`'s "Phase B" section (the design) and `48`'s
"Update — 2026-09-19 23:46 NZST, Step 1 deployed" section (what's live now)
before starting.

**Conventions (unchanged):** work on `main`, one commit, `cd backend &&
.venv/bin/pytest` green before committing, push when green. The repo is
`/opt/bookbrain` (ignore the Windows path in `prompts/README.md`). James
tests against the running app, so run the commands yourself.

**Settled, don't reopen:** no vector or graph DB at this scale (`prompts/39`);
the approved vocabulary and mapping in `tag_vocab.py` (if a mapping looks
wrong, tell James rather than changing it); no re-tag of old books without
James's say-so — 17 books were tagged under the old 4/5 map caps before Step
1 shipped and are still sitting there un-re-tagged, on purpose.

## Why now

`llm_tags_json.full.themesCanonical` exists on every done book since A.3
(theme dedup) — generic bare themes ("identity", "survival") are already
filtered out of it, so it's cleaner signal than the raw `themes` array ever
was. `embedding_service.embed_input` currently builds its `tags` argument
from genres/moods/representation only and deliberately excludes themes
([embedding_service.py:104-108](../backend/app/services/embedding_service.py#L104-L108))
— that exclusion predates the dedup and was correct at the time (the LLM
rephrased the same idea differently almost every call, pure noise). It no
longer applies to the canonical, deduped list.

## What to build

1. In `embedding_service.refresh_embeddings`
   ([embedding_service.py:160-175](../backend/app/services/embedding_service.py#L160-L175)),
   add `themesCanonical` to the `tags` list passed into `embed_input`, read
   straight from `book.llm_tags_json["full"]` (not through `_llm_tags()`,
   which is the index/recs-facing helper — see point 3).
2. Update the `embed_input` docstring
   ([embedding_service.py:100-108](../backend/app/services/embedding_service.py#L100-L108)),
   which still says themes are excluded and why; say what changed and why
   the old reasoning no longer applies now that A.3 dedupes them.
3. **Don't** add `themesCanonical` to
   `library_index_service._LLM_TAGS_KEYS`
   ([library_index_service.py:56](../backend/app/services/library_index_service.py#L56)).
   That constant feeds the viewer index (ShowcaseSection, facet chips) as
   well as `content_recs_service` and `embedding_service`'s genre/mood/rep
   tags — adding themes there ships them in the viewer index for no reason
   this task asks for. This task only touches the *embedding* input, built
   directly in `refresh_embeddings`, not through that shared list.
4. Leave `longSummary` out for now — prompts/47 B.1 flagged it as a risk
   (pulls search toward plot mechanics and ending spoilers over vibe).
   Revisit only after seeing themes alone perform on real queries, not in
   this pass.

## Measure before and after

Pick the ~5 "vibe" queries `prompts/47` names — "found family in space",
"grief", "political scheming at court", "cozy magic", "survival horror" — or
ask James if he'd rather pick his own. For each:

- Embed the query with `embedding_service.embed_texts` (CPU only, no
  Ollama) and rank all books by cosine against the *current* `books.embedding`
  column. Save the top 10 per query — this is the "before" table.
- Re-run `refresh_embeddings` (now including themes) at higher `limit`
  (everything tagged so far), then re-embed the same 5 queries and rank
  against the new `books.embedding`. Save the top 10 per query —"after".
- Diff the two top-10s per query and show James both tables side by side,
  plus which books moved in/out and why (spot-check 2-3 by eye against their
  `themesCanonical` — does the new top result's theme list actually match
  the query, or did a theme phrase just happen to share a word with it?).

This mirrors how `48`'s Step 1 was measured — don't skip straight to
deploying because the design already looks right on paper; B.3's MMR change
later in this plan showed a real, measurable reshuffle from a much smaller
tweak, so check this one too.

## Deploy (only after James has seen the before/after and says yes)

1. `sudo systemctl restart bookbrain` (ships the code).
2. `POST /api/library/embeddings/refresh?limit=5000` — every tagged book
   re-embeds against the new `embed_input`, ~1-2 min.
3. `POST /api/library/embeddings` — writes the viewer's
   `bookbrain-embeddings.bin` sidecar from the refreshed table.

Record what shipped and what was measured in an "Update" section at the end
of this file, same as `47`/`48` do.

## Then

Once this lands (or James says skip it), the next items in `48`'s order are
Step 3 (B.3 — MMR on content recs' top 8) and Step 4 (B.2/B.4, check
`git log` first in case another session already did them). Don't start
those in this session unless James asks — this file is scoped to B.1 only.
