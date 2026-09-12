# Task 22 — `librarySync.ts`: rebuild only on a stale sync token (REVIEW-2026-09-08 F4)

Read `prompts/README.md`. `library-viewer`-only; ships on push to `main`.

## Why

`library-viewer/src/lib/librarySync.ts:152-162`:

```ts
try {
  const { changes, newStartPageToken } = await listAllChanges(token, existing.pageToken)
  ...
} catch {
  // The sync token can go stale — Drive only retains change history for
  // a limited window. Fall back to a full rebuild ...
  return { cache: await fullRebuild(token, libraryFolderId), rebuilt: true }
}
```

The bare `catch` can't tell a **stale sync token** (the one thing a rebuild
actually fixes) from a transient 500, a rate-limit, or offline. Every one of
those triggers a full `listLibraryTree` walk — hundreds of Drive calls on the
2,200-book library — and swallows the real error so `useLibrary`'s `runSync`
never sees it (no "sync failed, retry" surfaced, no auth-expiry detection on
this path).

This subsystem has already silently lost books twice (`6856d0a`, and the
"library-viewer library-viewer sync fix" that added the 24h auto-rebuild) —
it deserves precise error handling.

## Goal

- In `drive.ts` (`listAllChanges` / wherever the Drive `changes.list` fetch
  lives), detect the stale-token case explicitly: Drive returns **HTTP 410**
  with an error `reason` of `pageTokenExpired` (sometimes surfaced as a 400
  with that reason — check both). Throw a distinguishable error for it, e.g.
  `class StalePageTokenError extends Error`.
- In `syncLibrary`, catch **only** `StalePageTokenError` → full rebuild
  (that's legitimate and expected). Let every other error propagate to
  `runSync`, which already has `flagAuthError` + a user-facing message path.
- Keep the existing "no cache / wrong folder / 24h old" → rebuild branches
  untouched.

## Gotchas

- `isAuthError` in `drive.ts` matches on message substrings ("sign-in
  expired", "401"). A 410 pageTokenExpired must NOT match that — it's not an
  auth problem.
- The 24h auto-rebuild (`AUTO_REBUILD_INTERVAL_MS`) stays as the backstop for
  silent drift — don't remove it.
- `librarySync.test.ts` exists — extend it: a `StalePageTokenError` from the
  changes fetch → `rebuilt: true`; a generic `Error` → the call rejects (no
  silent rebuild).

## Acceptance

- A simulated 410/`pageTokenExpired` → `syncLibrary` returns
  `{ rebuilt: true }`.
- A simulated 500 / network error → `syncLibrary` **rejects** (does not
  silently rebuild), and `useLibrary` shows a retryable error state.
- `cd library-viewer && npm run build && npx vitest run && npm run lint` green.

One commit, push to `main`.
