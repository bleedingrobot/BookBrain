// One primitive for the viewer-written Drive sidecars (news-dismissed,
// readnext-snoozed, reading-pending, wishlist, activity-log). Each of these
// is read → modify → written back, from any of the household's devices, with
// no Drive-side locking.
//
// `makeSidecar` gives:
//   • cachedNow()  — synchronous, from localStorage (useState initialiser)
//   • fetch()      — modifiedTime-gated read + cache refresh
//   • sync()       — fetch, fold the local cache back in, seed the sidecar
//                    when the cache held more (for a localStorage-only install)
//   • write(update)— serialised read-modify-write with a concurrency guard:
//                    after writing we re-read and, if `merge` shows the server
//                    now holds something our write dropped, we reconcile and
//                    write once more.
//
// `merge` MUST be a proper join (commutative, associative, idempotent) — a
// set/keyed union — for the guard to converge. It catches a sibling write
// that lands *after* ours (the common direction) and, combined with the
// per-file serialised chain, same-tab races. The tiny residual: two devices
// that each complete a full read→write inside the other's ~1s window, where
// the second silently wins — acceptable for a 3-person household writing
// rarely, and still far better than the unconditional overwrite it replaces.
//
// REVIEW-2026-09-10 F3/F6 (prompts/34).

import { readJsonFile, writeJsonFile } from './drive'

export interface SidecarSpec<T> {
  filename: string
  cacheKey: string
  empty: T
  parse: (raw: unknown) => T
  serialise: (value: T) => unknown
  // Combine our just-written value with the server's current copy. Required
  // for `write` to be concurrency-safe; omit only for a sidecar where a lost
  // update genuinely doesn't matter.
  merge?: (mine: T, remote: T) => T
  // Value equality for the reconcile check. Default: stable JSON compare of
  // `serialise(a)` vs `serialise(b)`.
  equal?: (a: T, b: T) => boolean
}

interface CacheShape<T> {
  folderId: string
  modifiedTime: string | null
  value: T
}

const chains = new Map<string, Promise<unknown>>()

export function makeSidecar<T>(spec: SidecarSpec<T>) {
  const eq =
    spec.equal ??
    ((a: T, b: T) => stableStringify(spec.serialise(a)) === stableStringify(spec.serialise(b)))

  function readCache(): CacheShape<T> | null {
    try {
      const raw = localStorage.getItem(spec.cacheKey)
      if (!raw) return null
      const parsed = JSON.parse(raw) as { folderId?: string; modifiedTime?: string | null; value?: unknown }
      if (typeof parsed.folderId !== 'string') return null
      return {
        folderId: parsed.folderId,
        modifiedTime: parsed.modifiedTime ?? null,
        value: spec.parse(parsed.value),
      }
    } catch {
      return null
    }
  }

  function writeCache(folderId: string, modifiedTime: string | null, value: T): void {
    try {
      localStorage.setItem(spec.cacheKey, JSON.stringify({ folderId, modifiedTime, value: spec.serialise(value) }))
    } catch {
      /* private mode / quota — the sidecar is still the source of truth */
    }
  }

  function cachedNow(): T {
    return readCache()?.value ?? spec.empty
  }

  async function fetch(token: string, folderId: string): Promise<T> {
    const cached = readCache()
    try {
      const found = await readJsonFile<unknown>(token, folderId, spec.filename)
      if (!found) {
        // No file yet. Keep a same-folder cache (it may hold local-only edits
        // a `write` will seed); otherwise empty.
        return cached && cached.folderId === folderId ? cached.value : spec.empty
      }
      if (cached && cached.folderId === folderId && cached.modifiedTime === found.modifiedTime) {
        return cached.value
      }
      const value = spec.parse(found.content)
      writeCache(folderId, found.modifiedTime, value)
      return value
    } catch {
      return cached && cached.folderId === folderId ? cached.value : spec.empty
    }
  }

  // Like fetch, but folds `cachedNow()` in via `merge` and — if the cache
  // held anything the sidecar didn't — seeds the sidecar (through `write`, so
  // it gets the same guard). For a device that made local-only edits before
  // the sidecar existed.
  async function sync(token: string, folderId: string): Promise<T> {
    if (!spec.merge) return fetch(token, folderId)
    let remote: T = spec.empty
    let modifiedTime: string | null = null
    try {
      const found = await readJsonFile<unknown>(token, folderId, spec.filename)
      if (found) {
        remote = spec.parse(found.content)
        modifiedTime = found.modifiedTime
      }
    } catch {
      return cachedNow()
    }
    const merged = spec.merge(cachedNow(), remote)
    writeCache(folderId, modifiedTime, merged)
    if (eq(merged, remote)) return merged
    return write(token, folderId, () => merged)
  }

  async function write(token: string, folderId: string, update: (current: T) => T): Promise<T> {
    const prior = chains.get(spec.filename) ?? Promise.resolve()
    const run = prior.then(() => doWrite(token, folderId, update))
    chains.set(
      spec.filename,
      run.catch(() => undefined),
    )
    return run
  }

  async function doWrite(token: string, folderId: string, update: (current: T) => T): Promise<T> {
    let result: T = spec.empty

    // The initial read gets its own guard: a transport failure here must NOT
    // be read as "the sidecar is empty" — that collapses the whole set (every
    // dismissed article reappears, every queued change is lost). Instead apply
    // the change on top of the last-known-good cache and keep it local; the
    // next successful sync/write reconciles it to Drive. We don't write blind
    // (no ETag on Drive v3, so we could clobber a sibling).
    let found: { id: string; modifiedTime: string; content: unknown } | null
    try {
      found = await readJsonFile<unknown>(token, folderId, spec.filename)
    } catch {
      const cached = readCache()
      const base = cached && cached.folderId === folderId ? cached.value : spec.empty
      result = update(base)
      writeCache(folderId, cached?.modifiedTime ?? null, result)
      return result
    }

    try {
      const current = found ? spec.parse(found.content) : spec.empty
      result = update(current)
      let id = await writeJsonFile(token, folderId, spec.filename, spec.serialise(result), found?.id ?? null)

      // Concurrency guard: re-read; if a sibling device wrote in the gap,
      // `merge` recovers what we'd have dropped. One extra write at most.
      if (spec.merge) {
        const after = await readJsonFile<unknown>(token, folderId, spec.filename)
        const remote = after ? spec.parse(after.content) : spec.empty
        const reconciled = spec.merge(result, remote)
        if (!eq(reconciled, remote)) {
          id = await writeJsonFile(token, folderId, spec.filename, spec.serialise(reconciled), after?.id ?? id)
          result = reconciled
        } else {
          result = reconciled
        }
      }

      const latest = await readJsonFile<unknown>(token, folderId, spec.filename).catch(() => null)
      writeCache(folderId, latest?.modifiedTime ?? null, result)
      return result
    } catch {
      // Best-effort: cache the optimistic value so the UI stays consistent.
      writeCache(folderId, readCache()?.modifiedTime ?? null, result)
      return result
    }
  }

  return { cachedNow, fetch, sync, write }
}

// Order-stable JSON for the equality check (object keys sorted; arrays as-is).
function stableStringify(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(',')}]`
  if (value && typeof value === 'object') {
    const keys = Object.keys(value as Record<string, unknown>).sort()
    return `{${keys.map((k) => `${JSON.stringify(k)}:${stableStringify((value as Record<string, unknown>)[k])}`).join(',')}}`
  }
  return JSON.stringify(value) ?? 'null'
}
