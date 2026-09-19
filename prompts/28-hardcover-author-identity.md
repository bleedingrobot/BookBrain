# Task 28 — Hardcover author identity: stop forking one person into two rows

Run as its own fresh session. **Read first:** `prompts/25-hardcover-integration.md`
(constraints + posture), `prompts/27-new-and-upcoming-releases.md` Part 2
(the per-author Hardcover pass this rides on), the `project-bookbrain-hardcover`
and `project-bookbrain-review-followups` memories, and `SPEC.md`
§ "Identification pipeline (2026)".

## The problem

`_find_or_create_author` (`book_repository.py`) matches on
`normalize_person_name`. That folds `J.R.R. Tolkien` / `Tolkien, J.R.R.` and
drops a lone middle initial (`Iain M. Banks` → `iain banks`). `repair_forked_authors.py`
then merges same-key groups — **but only with a shared book or ISBN**, because
`J. Smith` and `John Smith` also share a key.

Two standing failures survive that (from the Stage J notes, ~2 auto-organise
wrong today):

1. **Same person, no shared book.** `Iain M. Banks` (the SF novels) and
   `Iain Banks` (the literary novels) share a `normalize_person_name` key but
   zero books, so the repair script `SKIP`s them as "could be a different
   person". They stay two rows forever.
2. **Pen name ↔ legal name, different key.** `Robert Galbraith` and
   `J.K. Rowling` don't share a key at all, so they're never even compared.

Hardcover's `authors` graph knows both of these. This task uses it.

## What Hardcover exposes (verified live 2026-09-09)

`authors` fields include `id, name, slug, alias_id, canonical_id,
alternate_names, books_count, identifiers, bio, contributions`.

- **`canonical_id`** — Hardcover's own duplicate-row pointer. Three
  `Richard A. Knaak` rows all carry `canonical_id: 191045`.
- **`alias_id`** — pen-name → real-identity link. `Iain M. Banks`
  (Hardcover canonical id 1205498) has `alias_id: 95997`, which is the
  `Iain Banks` row. `Robert Galbraith` (id 200048) has `alias_id: 80626`
  **and** `alternate_names: ["J. K. Rowling"]`.
- **`alternate_names`** — every spelling Hardcover has seen for that person
  (`["Iain Banks", "Iain M Banks", "BANKS, IAIN M., 1954-2013", …]`).
- **House pseudonyms with several real authors are NOT linked.**
  `Richard Awlinson` (Forgotten Realms, written by Ciencin / Denning /
  Lowder) is a plain standalone `authors` row — `alias_id`, `canonical_id`,
  `alternate_names` all empty. This task will **not** touch those, and
  shouldn't: "Richard Awlinson" is a defensible thing to file them under.

### Resolving a name → a stable "person id"

Given the `authors` rows Hardcover returns for a name, the canonical person is:

1. pick the best row for the name — highest `books_count` (tie → lowest `id`);
2. if it has `canonical_id`, hop to that row;
3. if the result has `alias_id`, hop to that row;
4. the id you land on is the **person id**.

Spot-checks the implementation must reproduce:
`Iain M. Banks` → **95997**; `Iain Banks` → **95997**;
`Robert Galbraith` → **80626**; `Richard A. Knaak` → **191045**;
`Richard Awlinson` → **483649** (unchanged, no hops).

**Validate the chain live first.** Try selecting it in one query —
```graphql
authors(where: {name: {_eq: $name}}) {
  id name books_count canonical_id alias_id
  canonical { id alias_id }
  alias { id canonical_id }
}
```
— and if a depth cap bites, fall back to a second
`authors(where: {id: {_in: [...]}})` batch to finish the walk. A dozen probe
calls against the real token is fine.

---

## Phase 1 — resolve + repair the existing library (low risk, no scan-path change)

### Backend

- **Migration** (the established 1-nullable-ADD-COLUMN pattern, `alembic
  upgrade head`, no rebuild): `authors.hardcover_person_id INTEGER` — the
  resolved person id from the walk above. **Do not** reuse `authors.hardcover_json`
  (prompts/27 Part 2 owns that for `{books: […]}`) or `hardcover_synced_at`
  (owns the new-releases staleness window).

- **`hardcover_new_releases_service`** already visits every stale author once
  a night with one GraphQL call. Add a second root field to that same request
  (Hardcover allows ≤5 top-level queries/request — still one HTTP call, no
  extra quota):
  ```graphql
  authors(where: {name: {_eq: $name}}) { id name books_count canonical_id alias_id … }
  ```
  Resolve the person id (helper `resolve_person_id(rows) -> int | None`, its
  own unit-tested function) and write `Author.hardcover_person_id`.
  **Guard against a bad name match:** only store the id when the BookBrain
  author's name normalises (`normalize_person_name`) to the Hardcover row's
  `name` **or** one of its `alternate_names`. A fuzzy Hardcover hit that isn't
  really this person must leave `hardcover_person_id` null, not guess.

- A dedicated `POST /api/library/authors/resolve-identity?limit=&stale_days=`
  route mirroring the Phase-3 refresh routes, for an ad-hoc backfill run.
  (Or extend the existing `new-releases/refresh` to do both — pick one, note
  it in the commit.)

### `repair_forked_authors.py`

Add a **second pass** after the existing key-group logic:

- Group every `Author` with a non-null `hardcover_person_id` by that id.
- For each group of ≥2, merge into one canonical row **regardless of
  `normalize_person_name` key and without requiring a shared book** — the
  Hardcover identity assertion (plus the name guard above) is the
  corroboration.
- Canonical row = highest book count, tie → the name that appears verbatim in
  Hardcover's `name`/`alternate_names`, tie → lowest id. Repoint books, delete
  the emptied rows, backfill `sort_name`, same as the existing pass.
- Print each merge; keep it dry-run-by-default with `--write`.

### Tests

- `test_hardcover_new_releases_service.py` (respx): `resolve_person_id` for
  the four verified cases + the house-pseudonym no-op; the name guard rejects
  a wrong-person Hardcover hit; the field lands on `Author.hardcover_person_id`.
- `test_repair_forked_authors` (or wherever that script is covered): a
  cross-key merge driven purely by a shared `hardcover_person_id`; the
  no-shared-book case that the old pass skipped now merges; two genuinely
  different people with null ids are left alone.

### Acceptance

- Token unset → `hardcover_person_id` stays null everywhere, repair script
  behaves exactly as before.
- `cd backend && pytest` + `pytest -m corpus` green; the corpus's
  `iain-m-banks` / pen-name rows (if any) improve or hold.
- Live: run the identity backfill, then `repair_forked_authors.py` (dry run,
  eyeball), then `--write`. Report the merges.

---

## Phase 2 — stop new scans forking in the first place (optional, deeper)

Only after Phase 1 is in and the backfill looks right.

- `HardcoverProvider` gains `resolve_person_id(name) -> int | None` (cached
  per scan, same token bucket).
- `_find_or_create_author`: after the `normalize_person_name` match misses,
  resolve the incoming name's person id and match an existing `Author` by
  `hardcover_person_id` before creating a new row. Write the id onto the row
  it creates/returns.
- Costs one Hardcover call per genuinely new author in a scan — bounded, and
  degrades to today's behaviour on any failure (`None` → create the row as
  now). `hash_evidence` unaffected (author identity isn't in the AI prompt
  hash).
- Tests: a scan that credits `Iain M. Banks` reuses an existing `Iain Banks`
  row; a Hardcover failure falls back to a fresh row.

---

## Follow-ons (not this task)

- `alternate_names` → author aliases table (like `SeriesAlias`), consulted by
  `_find_or_create_author` before any Hardcover call.
- `authors.bio` + `born_year` / `death_year` + books-you-own → a small author
  page in the library-viewer.

## Constraints / gotchas

- **Backend restart on Windows** — no `--reload` (stale workers); the memory
  has the kill-list + `Start-Process -WindowStyle Hidden` recipe.
- Hardcover paid tier is 50k/day — folding identity into the existing
  per-author call adds zero quota; a standalone backfill is ~1 call/author
  (~450), trivial.
- One commit per phase. Update `prompts/25` + `prompts/README.md` +
  `ROADMAP.md` + the `project-bookbrain-hardcover` memory as each lands.
