# Task 20 — Co-author identity: make it deterministic, unblock the repair (REVIEW-2026-09-08 F1 + F2)

> **DONE 2026-09-08.** James chose **policy 1 — file under the primary author**.
> - `text_match.looks_solo` / `is_collaboration` (moved out of the repair
>   script). `book_repository._display_name`: a collaboration credit is stored
>   under `primary_author_name(name)`; `_find_or_create_author` upgrades an
>   existing "A & B" row to the clean solo form on the next match.
> - `corpus_harness.author_matches` now also matches on the primary author (the
>   triangulated truth is inconsistent about co-write display strings). **Author
>   precision 94.8% → 96.6%**, exact 81.4% → 83.1%; baseline re-stamped.
> - `repair_forked_authors.py` reworked: solo-name variants merge only with a
>   shared book/ISBN (still); a collaboration credit whose primary is the
>   canonical always folds in. Canonical = the shortest clean solo name. Its
>   dry-run on the real DB proposes 25 merges (Koontz collabs → "Dean Koontz",
>   the six GRRM anthology credits → "George R. R. Martin", etc.); `Dean R.
>   Koontz`/`Iain M. Banks`/`David L. Robbins` still SKIP (no shared book).
>   **NOT `--write`-run** — James runs it and eyeballs.
> - `test_book_repository.py` +2, `test_text_match.py` +1,
>   `test_repair_forked_authors.py` reworked. 593 backend + corpus green.

Read `prompts/README.md` for shared context. **Ask James the policy question
below before writing code.**

## Why

`book_repository._find_or_create_author` (`backend/app/services/book_repository.py:110`)
matches on `text_match.normalize_person_name`, which collapses a collaboration
credit to its first author's key:

```
normalize_person_name("Dean Koontz, Kevin J. Anderson")  -> "dean koontz"
normalize_person_name("Dean Koontz")                     -> "dean koontz"
```

So a new scan of a Koontz/Anderson book attaches it to the **solo** `Dean
Koontz` `Author` row (118 books) and files it in `Dean Koontz/`, dropping the
collaborator from the library structure. On the real DB there are **23 author
keys where a solo row and a collaboration row coexist**, 566 books under them
(they were created pre-Stage-J, when word-set matching kept them apart). The
eval corpus scores this behaviour as a **miss** (`margaret-weis-tra`,
`tony-diterlizzi-h`, `richard-awlinson`).

And `scripts/repair_forked_authors.py` — meant to clean genuine forks like
`Dean Koontz` / `Dean R. Koontz` (same person, different byline era) — was
guarded in `6768d3e` to skip any group mixing solo + collaboration credits,
which is now *every* group with a real fork. It's a no-op on the real library.

## The policy question for James

When a book is credited to "A & B" (or "A, B"), should BookBrain:

1. **File it under A** (the primary author) — simplest, matches how many
   readers shelve; the collaborator is visible in the book's stored title/
   metadata but not the folder. *(This is roughly today's accidental
   behaviour.)*
2. **File it under "A & B"** as its own author entity — accurate, but
   fragments an author's shelf (Sanderson's one co-write sits away from his 12
   solo books).
3. **File it under A, but keep "A & B" as the display name on that book** —
   needs a per-book author-credit field the schema doesn't have.

Recommend **(1)** for a personal library, made *deterministic*: a co-authored
book always resolves to the primary author's row, and the row's display name
is always the clean solo name.

## Goal (assuming policy 1)

### A. `text_match`

- Move the `_looks_solo(name)` logic out of `scripts/repair_forked_authors.py`
  into `text_match` (it already draws the exact line: no co-author separator,
  and `primary_author_name` keeps every significant word).
- Add `is_collaboration(name) -> bool` = `not _looks_solo(name)`.

### B. `_find_or_create_author`

- Key stays `normalize_person_name` (so `"Dean Koontz, Kevin J. Anderson"`
  still resolves to the `dean koontz` entity — that's policy 1).
- **On create**, if the incoming name `is_collaboration`, store the *primary*
  clean name (`primary_author_name(name)`) as `Author.name`, not the raw
  credit. So the row is always "Dean Koontz", never "Dean Koontz, Kevin J.
  Anderson".
- **On match**, if the existing row's name `is_collaboration` but the incoming
  name is solo (or a cleaner collaboration), upgrade `Author.name` to the
  clean solo form. (This is the one case Stage J deliberately avoided — but
  the corpus regressed on *rewriting to a co-author list*; rewriting *to the
  clean solo name* is what the triangulated truth actually wants. Re-run
  `pytest -m corpus` and confirm author precision does not drop.)

### C. `scripts/repair_forked_authors.py`

Rework the grouping:
- Group by `normalize_person_name` key.
- Within a group, **only merge the rows that `_looks_solo`** (`Dean Koontz` +
  `Dean R. Koontz` merge; the three Koontz collaboration rows are left alone
  *unless* B's clean-name rule would fold them — decide per the policy).
- Canonical = the **shortest** clean solo name in the merge set (not the
  longest string), tie-break on lowest id.
- Keep the existing "shared book/ISBN" corroboration gate.
- Run its dry-run against the real DB, paste the proposed merges into the
  commit message, do **not** `--write` it (James eyeballs + runs it himself).

## Gotchas

- Stage J's memo: rewriting a co-author *list* onto a row regressed the corpus
  (author 94.8 → 93.1). Rewriting to the clean *solo* name is different — but
  **prove it with `pytest -m corpus`**, don't assume.
- `_find_or_create_author` loads all authors per call (F6 / prompt 24) — don't
  make that worse; a name rewrite is a field set on an already-loaded row.
- Two genuinely different people can share a `normalize_person_name` key
  ("J. Smith" / "John Smith"). The repair's shared-book gate handles it; keep
  it.

## Acceptance

- A fresh scan of a co-authored book resolves to the primary author's row,
  and that row's `name` is the clean solo form — deterministically, regardless
  of scan order. Test in `test_book_repository.py` (new — `book_repository`
  currently has none).
- `pytest -m corpus` author precision ≥ the current 94.8%.
- `repair_forked_authors.py --write` (dry-run in the prompt) merges
  `Dean R. Koontz` into `Dean Koontz` and leaves `Martha Wells` /
  `Martha Wells, Aaron de Orive` correct per the chosen policy.
- `cd backend && pytest` green.

One commit. Update ROADMAP (Stage J follow-up resolved) and the memories
`project-bookbrain-review-followups` + `project-bookbrain-identification-accuracy-push`.
