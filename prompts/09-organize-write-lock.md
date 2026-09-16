# Task 09 — `OrganizeService._write_lock` isn't reset between tests; write locks don't serialise cross-service (P2)

Read `prompts/README.md` first for shared context.

## Why

`backend/tests/conftest.py::_reset_shared_singletons` resets five module-level
`asyncio.Lock`s / caches between tests and its docstring presents that as the
complete set:

```
book_repository.reset_book_write_lock()
get_folder_path_cache().clear()
reset_series_merge_write_lock()
reset_nightly_lock()
reset_metadata_writeback_lock()
```

It misses `OrganizeService._write_lock` (`organize_service.py:184`), an instance
attribute on the module-level singleton `_organize_service`. Today only
`test_run_scan_auto_organizes_eligible_files_after_scanning` acquires it via the
singleton, so no failure surfaces. The next test that exercises scan→auto-organize
(or `get_organize_service()` directly) through the singleton will hit
`RuntimeError: <asyncio.Lock> is bound to a different event loop` — the exact
failure the other five resets prevent. `pytest-asyncio` gives each test its own
loop; a lock binds to the loop of its first `acquire()`.

Related: there are now three independent commit-serialisation locks
(`book_repository._book_write_lock`, `OrganizeService._write_lock`,
`series_merge_service._write_lock`). Each only serialises its own service, so the
"slow Windows fsync holds SQLite's write lock past `busy_timeout`" scenario the
locks exist to prevent is still reachable when a manual organize overlaps a
nightly scan's tail, or a series merge overlaps either.

## Goal

Pick one:

**A (minimal):** add `reset_organize_write_lock()` to `organize_service.py` and
call it from `_reset_shared_singletons`. Update the fixture docstring's "complete
list" claim.

**B (better, recommended):** have `OrganizeService._organize_file` and
`series_merge_service.apply_series_merge` commit under the **shared**
`book_repository.get_book_write_lock()` instead of their own private locks —
`review_service` already does exactly this. Delete the two private locks and
their `reset_*` helpers. One lock, reset in one place, and cross-service commits
actually serialise.

Prefer B unless it measurably worsens organize throughput in the existing
concurrency tests (it shouldn't — the lock is only held around `session.commit()`,
and SQLite already serialises writers).

## Where it goes

- `backend/app/services/organize_service.py`
- `backend/app/services/series_merge_service.py` (if B)
- `backend/tests/conftest.py`
- `backend/app/services/book_repository.py` — only if B needs the lock's scope
  documented differently.

## Acceptance criteria

- A new test that runs two scan→auto-organize passes (or two
  `get_organize_service().organize_eligible_files(...)` calls) in separate test
  functions both pass — i.e. the singleton lock survives a loop change.
- `cd backend && pytest` green, including the existing
  `test_organize_service` / `test_scan_service` concurrency tests
  (`test_process_files_concurrently_*`).
- If B: `grep -rn "_write_lock" backend/app/services/` shows only the shared
  one (plus nightly's, which is a genuinely separate concern).
- Committed and pushed. ROADMAP note.

## Gotchas

- `nightly._nightly_lock` is a different thing (an "is a nightly run already
  going in this process" guard, not a commit serialiser) — leave it.
- `test_organize_service.py` constructs fresh `OrganizeService()` instances in
  several places — those get their own fresh lock and are fine; don't rewrite
  them unless B makes the instance lock vanish entirely.
- If you go with B, `apply_series_merge` currently does its Drive moves *outside*
  its lock and only commits inside it — keep that shape (don't hold the shared
  lock across `asyncio.to_thread(provider.move_and_rename, ...)`).
