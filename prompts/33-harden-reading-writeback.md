# Task 33 — harden the Hardcover reading write-back (REVIEW-2026-09-10 F1/F2/F4/F7)

Read `prompts/README.md`, `prompts/30-reading-status.md` (the licence framing),
`prompts/31-hardcover-more.md` Part I, and the `project-bookbrain-reading-status`
memory. Backend-only + a couple of viewer-test touches. Nothing here calls
Anthropic. **Do not run a real backfill / real-Drive write** to test — the unit
tests + a scratch `respx` check are enough; ask James before hitting the live
route.

Four related fixes, all in the reading-sync path. F1 is a **P1** (writes the
wrong book to James's real Hardcover account); the rest are P2 correctness.

---

## F1 — `_resolve_book_id` can resolve (and then mark read) the WRONG book

`app/services/hardcover_reading_service.py:178-203`.

```python
async def _resolve_book_id(http, token, bucket, change) -> int | None:
    isbn = (change.get("isbn13") or "").strip()
    if isbn:
        ... return bid            # ISBN path — fine
    ...
    data = await hardcover_graphql(http, token, _BOOK_SEARCH, {"q": q}, bucket)
    hits = ...["hits"] or []
    for hit in hits[:1]:
        doc = hit.get("document") ...
        return int(doc["id"])      # <-- top search hit, ZERO similarity check
```

`apply_pending` feeds that id straight into `_apply_status` /
`_apply_progress`, which run `insert/update_user_book` (+ today's
`last_read_date`) on James's real shelf.

**Why it's the common path, not the edge:** most owned `Book` rows have no
ISBN identifier, so `change.isbn13` (from the viewer's `row.isbn`) is usually
`null` → the unchecked search runs. And `App.tsx`'s reader `onClose`
auto-marks a book read at ≥ `FINISHED_FRACTION` with no explicit user action,
so a wrong match ships silently.

The identification pipeline already treats this class carefully — SPEC §5a:
an ISBN match still requires `title_similarity >= 0.80`. The write-back needs
an equivalent gate, and more so because it writes an *external* account.

### Goal

- After the search, accept `doc["id"]` only when **both**:
  - `normalize_title(doc["title"])` equals `normalize_title(change["title"])`,
    **or** the change title equals the Hardcover title with a leading
    `"<one-or-more words>: "` prefix stripped (Hardcover often stores
    `"The Dresden Files: Storm Front"`); a helper like
    `_title_matches(a, b)` in `text_match` or local.
  - some author on the hit (`doc.get("author_names")` /
    `contributions`) `normalize_person_name`-matches `change["author"]`.
    If the change has no author, fall back to title-only match but require
    it to be *exact* after normalisation (no prefix slack).
- On no confident match → return `None`. `apply_pending` already routes
  `book_id is None` to `failed`, which keeps the change queued (F2 then
  bounds the retries).
- `logger.info("reading write-back: no confident Hardcover match for %r by %r "
  "(top hit was %r)", title, author, doc.get("title"))` on rejection, so
  it's greppable.

### Tests

`tests/test_hardcover_reading_service.py`: a search that returns a
plausible-but-wrong top hit (`"Death Masks: A Dresden Files Collection"` by
`"Jim Butcher"` when the change is `"Death Masks"` / `"Jim Butcher"` → still
accepted via prefix rule; `"Grave Peril"` by `"Jim Butcher"` when the change
is `"Death Masks"` → rejected, `book_id is None`, change ends in `failed`).

---

## F2 — a permanently-unresolvable change is retried every sync forever

`app/services/library_index_service.py:915-926` (`regenerate_reading`).

```python
done = set(result["applied"])
left = [c for c in pending if c["driveFileId"] not in done]
```

A change that keeps landing in `failed` (book genuinely not on Hardcover, or
F1's new check rejects it) stays in `bookbrain-reading-pending.json` forever —
2 Hardcover calls per sync, per stuck change, and the queue never drains.

### Goal

- When writing `left` back, carry an `attempts` int on each change
  (`{**c, "attempts": c.get("attempts", 0) + 1}` for the ones that failed
  this run; leave applied ones dropped as now).
- Drop a change from `left` once `attempts >= 5`, `logger.warning(
  "reading write-back: giving up on %r after %d attempts", c.get("title"),
  attempts)`.
- The viewer writes changes without `attempts`; `apply_pending` /
  `_read_pending_reading` must tolerate it being absent (they already
  filter on `driveFileId` being a str — just don't choke on the extra key).

### Tests

`tests/test_library_index_service.py` (or a focused `regenerate_reading` test
if one gets added): a pending change that `apply_pending` returns as `failed`
5 times is present in `left` on runs 1–4 and gone on run 5.

---

## F4 — a persistently-partial reading pull freezes the sidecar silently

`app/services/library_index_service.py:950-965` (added `c9516e9`).

The "don't overwrite a good sidecar with a smaller partial one" guard is
right, but if `fetch_reading` returns `complete=False` *every* night (one page
fails each run), `bookbrain-reading.json` never updates and the only trace is
`reading: skipped` — same log line as "no token". James sees stale
read/unread state forever.

### Goal

- Track consecutive partial runs. Simplest: a `settings` key
  (`reading_partial_streak`, via `SettingsRepository`) bumped on a
  partial-and-skipped run, reset to 0 on any successful write.
- After `>= 3`: write the partial payload anyway, with `payload["partial"] =
  True`, and `logger.warning`. (A slightly-incomplete sidecar that updates
  beats a frozen one once it's clearly not a one-off.)
- Viewer: `reading.ts` `Reading.partial?: boolean`; when true, a one-line
  muted note under the "Read"/"Want" filter chips or on the stats screen —
  *"Hardcover sync was incomplete — some books may show as unread."* Keep it
  small.

### Tests

Backend: 3 partial runs in a row → the 3rd writes with `partial: True` and the
streak resets after a subsequent complete run. Viewer: `normaliseReading`
parses `partial`.

---

## F7 — a missed reading match is silent

`app/services/library_index_service.py:build_reading_payload` (~823-853).

After F1's narrator fix, the residual gap: a *read* Hardcover row that fails
both ISBN and `_norm_key` match just increments `unmatched["read"]` with no
trace — so "why is <owned book> showing unread" has no breadcrumb.

### Goal

- In the `drive is None` branch, when `row["status"] == "read"`: strip a
  leading `"<words>: "` prefix from `normalize_title(row["title"])`, and if
  that (or the raw normalised title) is a **substring** of any
  `normalize_title(t)` in the owned-files set (or vice versa),
  `logger.info("reading: read book %r by %r looks owned but didn't match "
  "(closest owned title: %r)", ...)`.
- Keep it to a bounded scan (the owned set is ~2.5k titles; a set-membership
  substring check over it once per unmatched-read row is fine — cap the log
  at, say, the first 20 hits so a bad run doesn't spam).
- Optional stretch: a real fuzzy fallback (reuse whatever
  `text_match`/`confidence_service` uses for `title_similarity`) that
  *matches* rather than just logs, gated behind a high threshold (≥ 0.9) and
  an author check. Only if it's clean — the log alone satisfies the finding.

### Tests

`build_reading_payload`: an owned `"Storm Front"` + a read Hardcover row
`"The Dresden Files: Storm Front"` with no ISBN → currently unmatched; assert
the INFO log fires (caplog) — and, if you did the stretch, that it now
matches.

---

## Acceptance criteria

- `_resolve_book_id` rejects a low-similarity search hit; a wrong "mark read"
  no longer reaches Hardcover.
- A change that can't be resolved is dropped after 5 attempts, not retried
  forever.
- 3+ consecutive partial pulls surface (a `partial` flag + a viewer note),
  not silent.
- An unmatched read-status Hardcover row that looks owned is logged with both
  titles.
- `cd backend && python -m pytest -q` and `-m corpus` green.
- `cd library-viewer && npm test && npm run build && npm run lint` green.
- One commit. Update `prompts/README.md`, `REVIEW-2026-09-10.md` (mark F1/F2/
  F4/F7 done), the `project-bookbrain-reading-status` memory.

## Gotchas

- **Backend restart on Windows** — no `--reload`; kill by port
  (`netstat -ano | grep :8000` → `taskkill //F //PID <winpid>`, *not* the
  `ps` unix pid). A stale `multiprocessing` child can keep serving old code.
- Hardcover paid tier is 50k/day — the extra `normalize_*` work is local, no
  new calls. Don't add a per-hit detail fetch; use what the search doc
  already returns (`title`, `author_names`).
- `apply_pending`'s `HardcoverRateLimited` → `break` path must stay: a
  daily-limit mid-run keeps committed work and stops.
- Don't touch the `contributions(where: {_or: [...]})` author filter — that's
  the `c9516e9` fix, working.
