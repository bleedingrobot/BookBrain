# Task 06 — Title collisions merge distinct books and feed them to the bulk trash (P0)

Read `prompts/README.md` first for shared context.

## Why

`normalize_title` (`backend/app/services/text_match.py`) strips everything from
the first `:` / `;` onward and any trailing parenthetical. So every book whose
EPUB `<dc:title>` is `"<Series>: <Book Title>"` — a very common format for
store-bought / release-group epubs — normalizes down to just the series word:

```
'Mistborn: The Final Empire'      -> 'mistborn'
'Mistborn: The Well of Ascension' -> 'mistborn'
'Star Wars: Heir to the Empire'   -> 'starwars'
'Star Wars: Dark Force Rising'    -> 'starwars'
```

`book_repository.resolve_book` matches an incoming title against existing books
**by the same author** using `normalize_title` equality
(`book_repository.py:54-68`), so the second book resolves to the first book's
row. Confirmed end to end against the real services + an in-memory DB:

```
book1 id 1  canonical 'Mistborn: The Final Empire'
book2 id 1  canonical 'Mistborn: The Final Empire'   <-- SAME ROW
detect_same_book_duplicates flagged: 1
  d1 a.epub  organised
  d2 b.epub  duplicate / same_book
```

Downstream:

1. `duplicate_service.detect_same_book_duplicates` runs at the end of **every**
   scan and rebuild, groups files by `book_id`, and flags all but the best as
   `status=duplicate` / `same_book`. Automatic, no user action. It also re-flags
   already-`organised` files (`_ACTIVE_STATUSES` includes `organised`).
2. `status=duplicate` files drop out of organize and the library index, so the
   **family viewer silently loses N-1 of every affected series**.
3. `clear_duplicates` (single bulk "Clear duplicates" button on the Duplicates
   page and the Dashboard checklist) then `trash_file`s the Drive file and
   hard-deletes the `File` row + its `Review`/`Operation`/`AIDecision`/
   `BookCandidate`/`MetadataSource` rows.

`review_service.correct` and `sticky_resolution.resolve_corrected_book_id` reach
the same `resolve_book` path, so a manual correction can also trigger the merge
(already noted in the `project_bookbrain_ai_series_hallucination` memory for the
`/correct` case; this task is the `detect_same_book_duplicates` + bulk-trash
escalation).

## Goal

Two independent layers so one bug can't cost the user books:

1. **Stop the wrong merge.** In `resolve_book`, match an incoming title against
   an existing `Book` row using a *stricter* normalization than the one used for
   confidence scoring — fold case / punctuation / leading articles, but do **not**
   strip `:` / `;` subtitles or trailing parentheticals. `"Mistborn: The Final
   Empire"` and `"Mistborn: The Well of Ascension"` must resolve to different
   rows. Keep the loose `normalize_title` for `confidence_service` only, where a
   false "match" merely adds points rather than merging identity.
   - Watch the other `normalize_title` call sites: `confidence_service.score`
     (keep loose), `identification_service.titles_match` / `_find_isbn_match`
     (scoring/corroboration — keep loose), `reident_audit_service` consensus
     checks (keep loose). Only the *row-identity* decision in `resolve_book`
     (and anywhere else that decides "is this the same Book row") needs to be
     strict.
2. **Make `same_book` detection and clearing conservative.**
   `detect_same_book_duplicates` should require more than `book_id` equality
   before flagging `same_book` — also require a strict-title match (and consider
   a size sanity check). `clear_duplicates` / the Duplicates UI should not sweep
   `same_book` rows into the same one-click bulk trash as exact-content
   (`sha256`) duplicates — either exclude them, or require a separate explicit
   confirm that lists them by title.

## Where it goes

- `backend/app/services/text_match.py` — a new strict-title normalizer (e.g.
  `normalize_title_strict`) or a flag on the existing one.
- `backend/app/services/book_repository.py` — `resolve_book` row match.
- `backend/app/services/duplicate_service.py` — `detect_same_book_duplicates`
  guard; possibly `clear_duplicates` / `list_duplicate_groups`.
- `backend/app/services/review_service.py`, `sticky_resolution.py` — inherit the
  `resolve_book` change; add a regression test for the correct-merge case.
- `frontend/src/pages/Duplicates.tsx` — fix the misleading "detected by content
  hash, not filename" header, and split/guard the `same_book` clear path.
- Migration only if you add a column; a pure logic change needs none.

## Acceptance criteria

- New test: two same-author books with titles sharing a pre-colon prefix resolve
  to **two** `Book` rows and are **not** flagged `same_book`.
- New test: `review_service.correct`-ing one book to a title that shares a
  pre-colon prefix with another does not merge them.
- Existing test that legitimately relies on loose title matching for *scoring*
  still passes (don't regress `confidence_service` / `identification_service`).
- Exact-content (`sha256`) duplicate detection and clearing is unchanged.
- `same_book` rows are not trashable via the same single click as `sha256` rows
  (separate confirm, or excluded).
- `cd backend && pytest` green; `cd frontend && npm run build` green.
- ROADMAP updated; update the `project_bookbrain_ai_series_hallucination` memory
  note with the strict/loose split.
- Committed and pushed.

## Gotchas

- The real library is Calibre-origin (title and series usually stored
  separately), so the incidence there may be lower than for freshly-torrented
  books — but the nightly run auto-organizes torrented books, and
  `detect_same_book_duplicates` runs every night. Don't downgrade this on
  "probably rare".
- Don't over-correct: `"Leviathan Wakes: Book One of The Expanse"` and
  `"Caliban's War: Book Two of The Expanse"` are genuinely different and already
  resolve fine — a strict normalizer must not merge those either, and must still
  let a genuine re-upload of the *same* title (differing only in casing /
  punctuation) reuse its row.
- `normalize_words` (used for author/series) is a separate function — leave it
  alone unless you find the same identity-vs-scoring split there.
- Books already merged in James's real DB won't un-merge themselves — mention in
  the report that a `run_rebuild` (or manual `/correct`) is needed to split
  existing casualties, and check whether rebuild actually re-splits them once the
  matcher is fixed.
