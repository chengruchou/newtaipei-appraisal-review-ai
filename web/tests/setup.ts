import "@testing-library/jest-dom/vitest";

// Node 24+/26 ships experimental global Web Storage that reads as `undefined` unless the
// runtime got `--localstorage-file`, and that global shadows jsdom's window storage in
// this environment. Tests need real Storage semantics, so install an in-memory one
// wherever the runtime left a hole. Harmless on runtimes where jsdom already provides it.
class MemoryWebStorage implements Storage {
  private store = new Map<string, string>();
  get length() {
    return this.store.size;
  }
  clear() {
    this.store.clear();
  }
  getItem(key: string) {
    return this.store.has(key) ? (this.store.get(key) as string) : null;
  }
  key(index: number) {
    return [...this.store.keys()][index] ?? null;
  }
  removeItem(key: string) {
    this.store.delete(key);
  }
  setItem(key: string, value: string) {
    this.store.set(key, String(value));
  }
}

for (const name of ["localStorage", "sessionStorage"] as const) {
  for (const target of [globalThis, globalThis.window].filter(Boolean) as object[]) {
    let present = false;
    try {
      const existing = (target as Record<string, unknown>)[name];
      present = existing != null && typeof (existing as Storage).getItem === "function";
    } catch {
      present = false;
    }
    if (!present) {
      Object.defineProperty(target, name, {
        value: new MemoryWebStorage(),
        configurable: true,
        writable: false,
      });
    }
  }
}
