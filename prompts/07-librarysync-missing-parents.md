# Task 07 — Viewer incremental sync can drop a file when a change record omits `parents` (P1)

Read `prompts/README.md` first for shared context. This one is **library-viewer
only** — no backend, no admin frontend.

## Why

`library-viewer/src/lib/librarySync.ts` keeps a local cache of the library's
files and updates it incrementally from Drive's changes feed (`applyChanges`).
For every live, non-folder change it does:

```ts
const parentKnown = (file.parents ?? []).some((p) => folderIds.has(p))
if (parentKnown && isSupportedEbook(file.name)) {
  filesById.set(file.id, { id: file.id, name: file.name })
} else {
  filesById.delete(file.id) // moved out of the library, renamed away from .epub, etc.
}
```

The `else` branch fires — **deleting the file from the cache** — not only when
the file was genuinely moved/renamed, but also when the change record simply has
no `parents` field. `drive.ts` requests `parents`
(`changes(...file(id,name,mimeType,parents,trashed))`), but Drive does not
guarantee it on every change delta. When it's missing for a file that's still in
the library, the incremental sync silently removes it and advances the page
token past the change, so nothing re-reports it — it stays invisible until the
24h `AUTO_REBUILD_INTERVAL_MS` full walk runs.

Same shape as the "brand-new nested folder" bug this file's own comments
describe. The 24h rebuild is a genuine backstop, so this is P1, but the window is
a day and there's no user-visible signal.

## Goal

Distinguish "this change says the file left the library" from "this change
carries no placement info":

- `change.removed === true` or `change.file.trashed` → remove (unchanged).
- `file.parents` is a **non-empty array with no known folder** → remove
  (genuinely moved out) (unchanged).
- `file.parents` is `undefined` (field absent) → **leave the existing cache
  entry untouched**. Don't add a brand-new file we've never seen without a known
  parent, but don't evict one we already have.
- `file.parents` is `[]` (explicitly empty) → treat as "moved out", same as
  today (a file with genuinely zero parents isn't in our tree).

Apply the same care to the folder-removal pass just above
(`librarySync.ts:105-110`): a folder change with `parents` absent shouldn't
evict a folder that's already in `folderIds`.

## Where it goes

- `library-viewer/src/lib/librarySync.ts` — `applyChanges`.
- `library-viewer/src/lib/drive.ts` — only if you want to distinguish
  `undefined` vs `[]` more explicitly in the `DriveChange` type.

## Acceptance criteria

- New unit test in `src/lib/` (there's no `librarySync.test.ts` yet — add one):
  a cached file receives a change whose `file` has no `parents` key → the file is
  **still in the returned cache**.
- New test: a cached file receives a change with `parents: ['some-other-folder']`
  (not in `folderIds`) → removed (unchanged behaviour).
- New test: `removed: true` and `trashed: true` still remove (unchanged).
- New test: a folder already in `folderIds` that gets a `parents`-less change is
  not evicted.
- `cd library-viewer && npm run build && npx vitest run && npm run lint` green.
- ROADMAP updated; add/extend a memory note on the librarySync drift class.
- Committed and pushed (this deploys the viewer via the Pages workflow — make
  sure the build + tests are green first).

## Gotchas

- Don't break the existing multi-pass folder-resolution loop (the nested
  new-folder fix) — only change the *eviction* conditions, not the *addition*
  logic.
- `isSupportedEbook` is still the right gate for *adding* a file; just don't let
  a missing-`parents` change reach the `delete` path.
- The cache is `localStorage` JSON — no schema migration concerns, but a
  malformed cache should still fall back to a full rebuild (it already does in
  `loadCache`).
