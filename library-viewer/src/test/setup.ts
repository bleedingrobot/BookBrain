import { beforeEach } from 'vitest'

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
