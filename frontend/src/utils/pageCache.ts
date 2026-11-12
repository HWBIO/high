const pageCache = new Map<string, unknown>()

export function readPageCache<T>(key: string, fallback: T): T {
  return pageCache.has(key) ? (pageCache.get(key) as T) : fallback
}

export function writePageCache<T>(key: string, value: T) {
  pageCache.set(key, value)
}

export function clearPageCache() {
  pageCache.clear()
}
