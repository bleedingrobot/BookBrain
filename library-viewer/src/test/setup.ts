import { beforeEach } from 'vitest'
import { IDBFactory } from 'fake-indexeddb'

// A fresh in-memory IndexedDB per test — librarySync.ts and bookCache.ts both
// use it for real (localStorage isn't roomy enough for either's data), and
// Node has no indexedDB of its own to test against.
beforeEach(() => {
  globalThis.indexedDB = new IDBFactory()
})

// Minimal localStorage for the node test environment — the sent-tracker and
// index/settings modules all persist through it.
class MemStorage implements Storage {
  private m = new Map<string, string>()
  get length() {
    return this.m.size
  }
  key(i: number): string | null {
    return [...this.m.keys()][i] ?? null
  }
  getItem(k: string): string | null {
    return this.m.has(k) ? this.m.get(k)! : null
  }
  setItem(k: string, v: string): void {
    this.m.set(k, String(v))
  }
  removeItem(k: string): void {
    this.m.delete(k)
  }
  clear(): void {
    this.m.clear()
  }
}

globalThis.localStorage = new MemStorage()

beforeEach(() => localStorage.clear())
