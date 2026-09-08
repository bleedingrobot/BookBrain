# Task 29 — Natural-language ("meaning") search in the library-viewer

Run as its own fresh session. **Read first:** `SPEC.md` § "library-viewer",
the `project_bookbrain_metadata_sidecar` + `project_bookbrain_viewer_reader`
memories (the sidecar pattern + the vendored-foliate-js precedent), and
`library-viewer/src/lib/libraryIndex.ts` / `books.ts` (how search works now).

## The ask

"That sci-fi one about a generation ship." The real daily use of a personal
library is *"I know we own it, I can't remember the title"* — which keyword
search (`matchesRow`: substring-AND over author/title/series/filename) can't
do. Add semantic search: embed the book blurbs, embed the query, rank by
cosine similarity. **No API cost** — a small local embedding model on both
sides.

## Shape

The viewer is a static site (GitHub Pages, no backend). So:

- **Backend** pre-embeds every organised book (it has the descriptions and
  Python) and writes a `bookbrain-embeddings.bin` sidecar next to the others.
- **Viewer** downloads that (~1 MB), loads the *same* embedding model once
  (~23 MB, cached), and at query time embeds **only the query string** (one
  forward pass, instant) then does an int8 dot-product against the ~2.4k book
  vectors in JS.

Query-only embedding in the browser keeps it fast even on a phone; the
backend sidecar means first search works immediately and the ~2.4k-book
indexing cost is paid once, server-side, not per device.

---

## Phase 0 — descriptions (prerequisite, not code)

**Semantic search is only as good as the text it embeds.** Right now
(2026-09-09): `Book.description` is set for **109 / 2420** organised books.
But there's far more available for free:

- ~1033 books have a Hardcover `meta.description` already sitting in
  `hardcover_json` (prompts/26 Part C).
- ~1236 files have an EPUB embedded `<dc:description>` in `metadata_sources`
  — and `build_index_payload` already falls back to it
  (`book.description or epub_desc.get(f.id)`), so the *viewer's* coverage is
  higher than 109.

**Before Phase 2 is worth shipping:** run the free description backfill
(`POST /api/library/descriptions` — Hardcover + Google Books + Open Library,
**no `ai=true`**). Report coverage before/after. If Google Books 429s (empty
`GOOGLE_BOOKS_API_KEY`), that's fine — Hardcover + OL + the EPUB fallback
still get most books to *some* blurb.

Embedding input per book (mirror the index's own fallback):
`f"{title}. {author}. {series}. {plain_text(description or epub_description)}"`
— title/author/series always included so a book with no blurb still ranks on
its own name.

---

## Phase 1 — backend embeddings + sidecar

### Model

**`sentence-transformers/all-MiniLM-L6-v2`** — 384-dim, ~23 MB quantized
ONNX, the standard small browser embedder, no query-prefix quirks. (A later
quality bump: `BAAI/bge-small-en-v1.5`, same dims, ~2× size, needs a
`"Represent this sentence…"` query prefix + a full re-embed. Note the model
id in the sidecar so a swap is detectable.)

### Embedder — `app/services/embedding_service.py`

Run the model **without torch** — `pip install fastembed` (Qdrant's ONNX
runner: pulls `onnxruntime` + `tokenizers`, ~20 MB, ships this exact model).
Or raw `onnxruntime` + `tokenizers` + numpy mean-pool/normalize if fastembed's
output doesn't match the browser's (see the parity gate).

`refresh_embeddings(session, *, limit=500)`:

1. Migration (nullable ADD COLUMNs, the established pattern):
   `books.embedding BLOB`, `books.embedding_hash TEXT`, `books.embedding_model TEXT`.
2. Select organised books whose `embedding_hash` ≠ `sha1(embed_input)` (or
   null), capped at `limit`.
3. Embed in batches; store the **float32** vector as `embedding` (bytes),
   the input hash, and the model id. Commit per batch (not per row — pure
   CPU, no slow HTTP, so a short transaction is fine; but keep batches ≤ 128
   so a crash loses little).
4. Return `{embedded, unchanged, total}`.

### Sidecar — `bookbrain-embeddings.bin`

Binary, not JSON (2.4k × 384 as JSON text is ~7 MB; int8 is ~0.9 MB):

```
[4 bytes  ] uint32  header length N
[N bytes  ] utf-8 JSON: {version, model, dim, count, ids: [driveFileId, …]}
[count*dim] int8    L2-normalised vectors, row-major, matching `ids` order
                    (component * 127, clamped — normalised vectors are in [-1,1])
```

`library_index_service.build_embeddings_payload` + `regenerate_embeddings(creds,
folder)` + write via a new `_write_bytes_file` helper (the existing
`_write_json_file` won't do — this is `application/octet-stream`). Nightly:
one more token-free step after the index. Routes
`POST /api/library/embeddings/refresh?limit=` + `POST /api/library/embeddings`,
mirroring the recs pair.

### Tests

`test_embedding_service.py`: incremental (unchanged hash → skipped), the
`title. author. series. blurb` input assembly, batch commit. Vector values
themselves aren't asserted (model output) — a shape + norm≈1 check is enough.
`build_embeddings_payload`: header parses, `ids` aligns with the byte block,
int8 round-trips within tolerance.

---

## Phase 2 — viewer

### Vendor the model (don't fetch from a CDN)

The viewer is a PWA with a same-origin-only service worker. Fetching the
model from `huggingface.co` / jsdelivr works but is never SW-cached, so
offline search breaks. Instead:

- `npm i @huggingface/transformers` (lazy-imported, so it's not in the main
  bundle).
- Copy the ONNX model + tokenizer into `public/models/all-MiniLM-L6-v2/…` and
  the `onnxruntime-web` `.wasm` files into `public/ort/`. Set
  `env.allowRemoteModels = false`, `env.localModelPath = `${BASE_URL}models/``,
  `env.backends.onnx.wasm.wasmPaths = `${BASE_URL}ort/``.
- ~23 MB in `public/` → the deploy artifact and first-load-of-search grow by
  that much. Acceptable for a personal PWA; it downloads once per device and
  is then SW-cached (offline search works). GitHub's 100 MB/file limit is
  fine; note the repo-size bump in the commit.

### `lib/embeddings.ts`

`fetchEmbeddings(token, folderId) → { model, dim, ids: string[], vectors: Int8Array }`
— the modifiedTime-gated localStorage/IndexedDB cache pattern from
`recommendations.ts` (but the payload is binary + ~1 MB, so cache it in
IndexedDB via `bookCache`'s store, not localStorage). Parse the 4-byte header
length, then the JSON, then the `Int8Array` tail.

### `lib/semanticSearch.ts`

- `ensureModel()` — lazy `import('@huggingface/transformers')`, build a
  `pipeline('feature-extraction', 'all-MiniLM-L6-v2')` once, memoised. Surface
  a `loading` state (first call downloads + inits the model).
- `search(query, { ids, vectors, dim }) → { id, score }[]` — embed the query
  (`{ pooling: 'mean', normalize: true }`), quantise to int8, dot-product
  against every row (`for` loop over the flat `Int8Array`, `score = Σ q[k]*v[k]`),
  sort desc, return the top ~60 with `score` above a floor (~0.25 after the
  /127² rescale — tune on real queries).

### Search UI (`App.tsx` + the search box)

- A small segmented toggle by the box: **Keyword | Meaning** (default
  Keyword). Persist the choice in `settings` (browser-local), like
  `showGlobalReleases`.
- **Meaning** mode: on submit (not per keystroke — the model call, though
  fast, isn't free), run `semanticSearch.search`, then render rows in score
  order. **Pin exact substring matches on top** regardless of score, so
  "dune" still finds *Dune* first. Show a subtle "· 82% match" on each.
- First use in Meaning mode: an inline "Loading search model — downloads once
  (~23 MB)…" with a spinner; the box stays usable in Keyword mode meanwhile.
- Empty query in Meaning mode → fall back to the normal list.
- Works fully offline once the model + sidecar are cached — verify with
  DevTools offline.

### Tests

Viewer env is `node`; `@huggingface/transformers` runs there. Keep the model
out of CI (don't download 23 MB in `npm test`): unit-test the **pure**
bits — header/int8 parsing in `embeddings.ts`, the dot-product ranking in
`semanticSearch.ts` fed hand-made vectors, the exact-match pinning. Gate the
actual-model test behind an env flag, run locally.

---

## Parity gate (do this between Phase 1 and Phase 2)

The backend (`fastembed`) and the browser (`@huggingface/transformers`) must
produce compatible vectors or the whole thing silently returns garbage.
Before wiring Phase 2:

- Embed these 8 strings on **both** sides:
  `["a lonely lighthouse keeper", "generation ship voyage to a new world",
    "hard-boiled detective in a rain-soaked city", "coming-of-age on a farm",
    "epic fantasy war between gods", "quiet novel about grief",
    "space opera with a snarky AI", "Victorian ghost story"]`
- Assert every pairwise cosine(backend[i], browser[i]) ≥ 0.99.
- If it fails: stop using fastembed's built-in — vendor **one** ONNX file +
  tokenizer, load it with raw `onnxruntime` on the backend and
  `env.localModelPath` in the browser, matching pooling (mean) + L2-normalise
  by hand on both. Same file → guaranteed parity.

Keep the parity script in `backend/scripts/` for re-runs after any model change.

---

## Acceptance

- Token/model absent → no sidecar, no toggle, viewer unchanged (Keyword only).
- `cd backend && pytest` + `pytest -m corpus` green; viewer `npm test` +
  `npm run build` + `npm run lint` green (build size noted).
- Live: run Phase 0, then `embeddings/refresh` (loop) + `POST /library/embeddings`
  + `POST /library/index`. Then in the deployed viewer, Meaning-search
  "generation ship" / "detective noir" / "book about grief" and eyeball the
  top 10.
- One commit per phase. Update `SPEC.md` + `ROADMAP.md` + `prompts/README.md`
  + a new `project_bookbrain_semantic_search` memory.

## Gotchas

- MiniLM truncates at 256 tokens (~1000 chars) — the 1500-char index cap is
  already past that; don't bother trimming further.
- `fastembed` downloads its model to `~/.cache` on first use (offline CI will
  fail a test that calls it — gate those).
- The service worker is **same-origin only** (`covers.ts` / `sw.js`) — this is
  *why* the model is vendored, not CDN-loaded.
- Re-embedding all ~2.4k books is ~1–2 min of CPU on the backend, once. The
  nightly's incremental pass keeps it current after that.
- `@huggingface/transformers` pulls `onnxruntime-web`; make sure the lazy
  `import()` really keeps it out of the main chunk (check `npm run build`
  output — the main bundle must not jump).
