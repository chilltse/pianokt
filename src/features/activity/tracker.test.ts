import assert from 'node:assert/strict'
import test from 'node:test'
import { ActivityTracker, type Activity, type ActivityEvent } from './tracker.ts'

class MemoryStorage implements Storage {
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
  clear() {
    this.data.clear()
  }
  key(i: number) {
    return [...this.data.keys()][i] ?? null
  }
}
function setup() {
  let id = 0
  const sent: Activity[] = []
  const deps = {
    session: new MemoryStorage(),
    durable: new MemoryStorage(),
    now: () => '2026-01-01T01:00:00Z',
    uuid: () => `id-${++id}`,
    send: async (body: Activity) => {
      sent.push(body)
    },
  }
  return { deps, sent }
}
function event(id: string, seq: number, type: ActivityEvent['event_type']): ActivityEvent {
  return {
    event_id: id,
    event_sequence: seq,
    event_type: type,
    occurred_at: '2026-01-01T01:00:00Z',
    song_time_sec: 2,
    elapsed_playing_sec: 1,
    metadata: {},
  }
}
const settle = () => new Promise((resolve) => setTimeout(resolve, 0))

test('different songs share visit; attempt sequence increases', async () => {
  const { deps, sent } = setup()
  const tracker = new ActivityTracker('u', deps, false, deps.now())
  const a = tracker.begin('a', 'song-a', 'builtin', deps.now())
  tracker.record(a, event('e1', 0, 'play_started'))
  tracker.record(a, event('e2', 1, 'exited'))
  const b = tracker.begin('b', 'song-b', 'builtin', deps.now())
  tracker.record(b, event('e3', 0, 'play_started'))
  await settle()
  await tracker.flush()
  const records = sent.filter((r) => r.attempt)
  assert.equal(new Set(records.map((r) => r.visit.id)).size, 1)
  assert.equal(b.visit_attempt_sequence, 2)
  assert.equal(deps.durable.length, 0)
})

test('reload retains visit and interrupts the previous take', async () => {
  const { deps, sent } = setup()
  const first = new ActivityTracker('u', deps, false, deps.now())
  const a = first.begin('a', 'song', 'builtin', deps.now())
  first.record(a, event('e1', 0, 'play_started'))
  await settle()
  await first.flush()
  const originalVisit = sent[0].visit.id
  const second = new ActivityTracker('u', deps, true, deps.now())
  await settle()
  await second.flush()
  assert.equal(sent.at(-1)?.visit.id, originalVisit)
  assert.ok(sent.some((r) => r.events[0]?.event_type === 'interrupted'))
  assert.equal(second.begin('b', 'song', 'builtin', deps.now()).visit_attempt_sequence, 2)
})

test('new tab never reuses copied sessionStorage visit', async () => {
  const { deps, sent } = setup()
  new ActivityTracker('u', deps, false, deps.now())
  await settle()
  new ActivityTracker('u', deps, false, deps.now())
  await settle()
  assert.notEqual(sent[0].visit.id, sent[1].visit.id)
})

test('offline events persist and replay only for their owner', async () => {
  const { deps, sent } = setup()
  let online = false
  deps.send = async (body) => {
    if (!online) throw Error('offline')
    sent.push(body)
  }
  const a = new ActivityTracker('user-a', deps, false, deps.now())
  const context = a.begin('a', 'song', 'builtin', deps.now())
  a.record(context, event('e1', 0, 'play_started'))
  a.record(context, event('e2', 1, 'paused'))
  await settle()
  assert.equal(deps.durable.length, 3)
  online = true
  const b = new ActivityTracker('user-b', deps, false, deps.now())
  await settle()
  await b.flush()
  assert.ok([...deps.durable.data.keys()].every((k) => k.startsWith('pianokt-activity-v2:user-a:')))
  await a.flush()
  assert.equal(deps.durable.length, 0)
  assert.ok(sent.some((r) => r.events[0]?.event_type === 'paused'))
})

test('an in-flight heartbeat cannot delete a newer close signal', async () => {
  const { deps } = setup()
  let release: (() => void) | undefined
  deps.send = () =>
    new Promise<void>((resolve) => {
      release = resolve
    })
  const tracker = new ActivityTracker('u', deps, false, deps.now())
  tracker.touch('pagehide')
  release?.()
  await settle()
  assert.equal(deps.durable.length, 1)
  assert.equal(JSON.parse([...deps.durable.data.values()][0]).visit.state, 'pagehide')
})

test('background upload packets cannot reopen a visit after sign-out', async () => {
  const { deps } = setup()
  const tracker = new ActivityTracker('u', deps, false, deps.now())
  const context = tracker.begin('a', 'song', 'builtin', deps.now())
  tracker.record(context, event('e1', 0, 'play_started'))
  tracker.touch('signed_out')
  const first = tracker.packet(context, [])
  const second = tracker.packet(context, [])
  assert.equal(first.visit.state, 'signed_out')
  assert.deepEqual(first.visit, second.visit)
  await settle()
})
