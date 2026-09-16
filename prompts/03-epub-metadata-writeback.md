# Task 3 — Write resolved metadata + cover into the EPUB

Read `prompts/README.md` first for shared context.

## Why

Organize fixes the **filename** and the DB, but a Kobo (and Calibre, and every
other reader) shows the EPUB's **embedded OPF metadata** — which is often the
exact junk that caused the misidentification. The family reads on Kobos, so
"the app knows it's *Throne of Glass #2*" doesn't help them if the device shelf
still says "queen of shadows - unknown". Close that loop: after identification,
write the canonical `title` / `author` / `series` / `series_number` and embed the
resolved cover image into the `.epub` file itself.

SPEC §3 explicitly deferred this from v1 ("No EPUB metadata repair/writing —
read-only parsing only"). This task **reverses that** — update SPEC.md.

## Scope

- `.epub` and `.kpub` only (kpub is epub-structured). **Not** `.cbz` — skip
  comics. Files that arrived as `.mobi/.rtf/.txt` are already converted to epub by
  the time they're organised, so they're covered.
- Write **EPUB 3** `<meta property="belongs-to-collection">` for series **and**
  the legacy `<meta name="calibre:series">` / `series_index` pair, so both modern
  and older readers (Kobo) pick it up. Verify what Kobo actually reads —
  historically it's the calibre: legacy tags.
- Embed the cover the same one `cover_service` already extracts/serves (≤320px is
  fine for a shelf thumbnail, but prefer writing the original-resolution cover if
  it's still in the epub; only inject when the epub genuinely lacks one or has a
  broken reference).
- Preserve everything else in the OPF. Don't reflow, don't touch content files,
  don't re-compress chapters. Rewrite the zip: copy every entry through, replace
  only `content.opf` (or whatever the rootfile is) and the cover image entry,
  keep `mimetype` first and stored (uncompressed) per the EPUB spec.

## The critical gotcha — sha256 stickiness

`files.sha256` is the key for **exact-duplicate detection** AND **correction
stickiness** (SPEC §1: sticky corrections are keyed by `sha256` alone, so the
same content under multiple file rows shares a correction). If you rewrite the
epub, its hash changes and both of those break.

You must:
1. Recompute and update `files.sha256` (and `size_bytes`) after the rewrite.
2. Re-point any `reviews` / sticky-correction rows keyed on the **old** hash to
   the new one — or, cleaner, run the metadata write **before** the sticky-
   correction lookup would ever matter again and make the pipeline tolerant of
   the hash change (a corrected book that gets re-scanned should still match).
3. Make sure the duplicate detector doesn't now flag the rewritten file as a new
   book, and doesn't treat two copies you rewrote identically as dupes of each
   other incorrectly (they legitimately *are* the same book — that's fine).
4. `operations` row for the rewrite so it's in the activity log. Undo is
   probably impractical (we don't keep the original bytes) — either keep a
   `.orig` backup in Drive, or make the operation explicitly non-undoable and say
   so in the SPEC. Decide with a bias toward *not* silently losing the original;
   Drive keeps revision history, so check whether an in-place update via the
   Drive API preserves the prior revision (the provider already uses this trick
   for mobi/rtf conversions — see `provider.py` "swap a converted mobi/rtf").

## Where it hooks in

- New provider capability: `app/providers/epub/writer.py` —
  `write_metadata(epub_bytes, meta, cover_bytes|None) -> bytes`. Pure function,
  heavily unit-tested with fixture epubs (`backend/tests/epub_fixtures.py`).
- New service method, probably on `organize_service` or a dedicated
  `metadata_writeback_service`, called as a step of organize when `dry_run` is
  off — OR as a separate opt-in "Fix embedded metadata" button on the Library
  page + a bulk endpoint (mirrors how covers/descriptions work). **Recommend the
  separate opt-in path first** — lower blast radius than wiring it into every
  organize, and it can be run over the existing ~2200-book library once.
- Frontend: a "Fix embedded metadata" button + progress line on the Library page,
  same pattern as "Generate covers".

## Acceptance criteria

- `write_metadata` round-trips: output is a valid epub (openable by `ebooklib` and
  by the existing safe parser), `mimetype` entry still first + stored, all
  original content entries byte-identical, OPF has the new title/author/series,
  cover embedded and referenced.
- A rewritten file's `files.sha256` / `size_bytes` are updated and sticky
  corrections for that content still resolve.
- Duplicate detection behaves sanely across a rewrite.
- Bulk endpoint + job status + frontend button, resumable (skip files already at
  the right metadata — store a marker or compare).
- `cd backend && pytest` green; `cd frontend && npm run build` green.
- SPEC.md §3 updated; ROADMAP updated; memory note added.
- Committed and pushed.

## Test carefully

- An epub with no existing cover.
- An epub with a cover referenced by `<meta name="cover">` → manifest item.
- An epub whose rootfile isn't at `OEBPS/content.opf`.
- An epub with existing (wrong) calibre:series tags — must be replaced, not
  duplicated.
- A book with no series — must not write empty series tags.
- Re-running the job on an already-fixed file — no-op, no hash churn.
