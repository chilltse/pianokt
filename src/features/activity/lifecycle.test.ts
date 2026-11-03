import assert from 'node:assert/strict'
import test from 'node:test'

test('page visibility, auth refresh, BFCache return and account switches respect visit ownership', async () => {
  class MemoryStorage {
    data = new Map<string, string>()
    get length() {
      return this.data.size
    }
    getItem(k: string) {
      return this.data.get(k) ?? null
    }
    setItem(k: string, v: string) {
      this.data.set(k, v)
    }
    removeItem(k: string) {
      this.data.delete(k)
    }
    key(i: number) {
      return [...this.data.keys()][i] ?? null
    }
  }
  const names = [
    'window',
    'document',
    'localStorage',
    'sessionStorage',
    'performance',
    'fetch',
  ] as const
  const descriptors = names.map(
    (name) => [name, Object.getOwnPropertyDescriptor(globalThis, name)] as const,
  )
  const win = Object.assign(new EventTarget(), { setInterval: () => 1 })
  const doc = Object.assign(new EventTarget(), { visibilityState: 'visible' })
  const values = {
    window: win,
    document: doc,
    localStorage: new MemoryStorage(),
    sessionStorage: new MemoryStorage(),
    performance: { getEntriesByType: () => [] },
    fetch: async () => ({ ok: true }),
  }
  for (const name of names)
    Object.defineProperty(globalThis, name, { configurable: true, value: values[name] })
  const api = await import('./index.ts')
  try {
    api.setActivitySession({ user: { id: 'user-a' }, access_token: 'token-a' })
    const first = api.beginAttempt('a1', 'song', 'builtin', '2026-01-01T00:00:00Z')!
    const firstVisit = api.activityPacket(first, [])!.visit.id
    doc.visibilityState = 'hidden'
    doc.dispatchEvent(new Event('visibilitychange'))
    doc.visibilityState = 'visible'
    doc.dispatchEvent(new Event('visibilitychange'))
    api.setActivitySession({ user: { id: 'user-a' }, access_token: 'refreshed-a' })
    assert.equal(api.activityPacket(first, [])!.visit.id, firstVisit)
    win.dispatchEvent(new Event('pagehide'))
    assert.equal(api.activityPacket(first, [])!.visit.state, 'pagehide')
    const shown = new Event('pageshow')
    Object.defineProperty(shown, 'persisted', { value: true })
    win.dispatchEvent(shown)
    const second = api.beginAttempt('a2', 'other-song', 'builtin', '2026-01-01T00:01:00Z')!
    assert.notEqual(api.activityPacket(second, [])!.visit.id, firstVisit)
    assert.equal(second.visit_attempt_sequence, 1)
    // Background upload for the old visit must never be rebound to a new visit/user.
    assert.equal(api.activityPacket(first, [])!.visit.id, firstVisit)
    api.setActivitySession({ user: { id: 'user-b' }, access_token: 'token-b' })
    assert.equal(api.activityPacket(second, [])!.owner_id, 'user-a')
    const third = api.beginAttempt('b1', 'song', 'builtin', '2026-01-01T00:02:00Z')!
    assert.equal(api.activityPacket(third, [])!.owner_id, 'user-b')
    api.setActivitySession(null)
    await new Promise((resolve) => setTimeout(resolve, 0))
  } finally {
    for (const [name, descriptor] of descriptors) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor)
      else Reflect.deleteProperty(globalThis, name)
    }
  }
})
