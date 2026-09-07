# Task 23 — Tests for `cover_service` (REVIEW-2026-09-08 F5)

Read `prompts/README.md`. Test-only, no behaviour change.

## Why

`backend/app/services/cover_service.py` (259 lines) has **no test**. It:

- extracts a cover image from an EPUB, resizes it to a 320px thumbnail (PIL),
- computes a 64-bit perceptual hash (`imagehash.phash`) → `files.cover_phash`,
- uploads the thumbnail to the Drive `covers/` folder, writes a `.nocover`
  marker for EPUBs with no cover,
- `regenerate_covers` backfills `cover_phash` from *existing* Drive `.jpg`s
  without re-downloading the book.

All of it runs unattended on every nightly run, and the pHash feeds Library
Audit's "near-identical cover art" panel. A regression here is silent until
someone notices covers stopped updating.

(`drive_service` and `job_run_service` are also untested but are thin and
exercised transitively via the nightly + auth tests — `cover_service` is the
real gap.)

## Goal

`backend/tests/test_cover_service.py`, following the fake-provider pattern from
`test_organize_service.py` / `test_backup_service.py`:

- A fake `DriveProvider` capturing `upload_new_file` / `list_files_in_folder`
  / `create_folder` calls, plus a stub for whatever it uses to fetch cover
  bytes.
- A tiny valid PNG built in-memory (`PIL.Image.new(...).save(buf, "PNG")`) —
  and a `build_epub` fixture variant with an embedded cover (see
  `tests/epub_fixtures.py`).

Cover at least:
1. **Happy path** — an EPUB with a cover → a 320px-ish thumbnail uploaded to
   `covers/`, `files.cover_phash` set to a 16-hex string.
2. **No cover** — an EPUB with no cover image → a `.nocover` marker, no
   thumbnail, `cover_phash` left `NULL`.
3. **Resumable** — a second `regenerate_covers` run skips a book that already
   has a `covers/<id>.jpg`, and backfills its `cover_phash` from the existing
   jpg **without** a book download (assert the download stub was not called).
4. **A broken/corrupt cover image** — doesn't crash the run; that book is
   counted as failed, the rest proceed.
5. `cover_phash` is stable — same image in twice → same hash.

## Gotchas

- `imagehash` pulls `numpy` + `scipy` (already a dep via `pip install -e .`).
- Don't hit Drive or download real books — everything through the fake
  provider + in-memory images.
- Match the existing count-dict shape `regenerate_covers` returns
  (`{done, nocover, rehashed, failed}`) so the assertions are precise.

## Acceptance

- `cd backend && pytest tests/test_cover_service.py` green, ≥ 5 cases.
- `cd backend && pytest` still green (no import cycle, no shared-fixture
  breakage).

One commit.
