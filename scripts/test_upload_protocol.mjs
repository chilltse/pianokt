// Exercise the production transport serializer and local-lab guard without a browser.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'

function compile(path, lab = false) {
  const source = readFileSync(path, 'utf8')
    .replaceAll('import.meta.env.VITE_PIANOKT_API_URL', '"https://api.test"')
    .replaceAll('import.meta.env.DEV', String(lab))
    .replaceAll('import.meta.env.VITE_LOCAL_ALIGNMENT_LAB', JSON.stringify(String(lab)))
  return ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText
}
const requests = []
let activePuts = 0
let maxActivePuts = 0
const context = vm.createContext({
  exports: {},
  crypto,
  performance,
  AbortSignal,
  console,
  Uint8Array,
  TextEncoder,
  atob,
  require: () => ({
    supabase: {
      auth: {
        getSession: async () => ({
          data: { session: { user: { id: 'owner' }, access_token: 'token' } },
        }),
      },
    },
  }),
  fetch: async (url, options) => {
    requests.push({ url, options })
    if (options.method === 'GET') return { ok: false, status: 404, headers: new Headers(), text: async () => 'Not found' }
    if (options.method === 'PUT') {
      activePuts++
      maxActivePuts = Math.max(maxActivePuts, activePuts)
      await new Promise((resolve) => setTimeout(resolve, 10))
      activePuts--
      return { ok: false, status: 412, headers: new Headers() }
    }
    assert.equal(activePuts, 0, 'finalize must wait for every PUT')
    return {
      ok: true,
      headers: new Headers(),
      json: async () =>
        url.endsWith('/attempts')
          ? {
              performance_url: 'https://gcs.test/p',
              reference_url: 'https://gcs.test/r',
              performance_events_url: 'https://gcs.test/e',
            }
          : { id: 'attempt' },
    }
  },
})
vm.runInContext(compile('src/features/challenge-history/gcs.ts'), context)
const activity = {
  owner_id: 'owner',
  attempt: { attempt_id: 'attempt' },
  visit: { id: 'visit' },
  events: [{ event_sequence: 0, elapsed_playing_sec: 0.12345678901234567 }],
}
const result = await context.uploadSealedRecording(
  {
    schemaVersion: 2,
    songSource: 'builtin',
    songId: 'song',
    songTitle: null,
    durationSec: 1,
    midiBase64: 'TQ==',
    referenceMidiBase64: 'Ug==',
    activity,
    performanceEvents: { schemaVersion: 1, events: [], transportSegments: [] },
  },
  'owner',
  'attempt',
)
assert.equal(result.id, 'attempt')
assert.equal(maxActivePuts, 3)
const create = JSON.parse(requests.find((r) => r.url.endsWith('/attempts')).options.body)
assert.equal(create.schema_version, 2)
assert.equal(create.attempt_id, 'attempt')
assert.equal('activity' in create, false)
assert.equal('session_id' in create, false)
assert.equal(requests.filter((r) => r.options.method === 'PUT').length, 3)
const final = JSON.parse(requests.at(-1).options.body)
assert.deepEqual(final, { schema_version: 3, activity })
assert.equal(requests.at(-1).url, 'https://api.test/attempts/attempt/finalize')

const lab = vm.createContext({
  exports: {},
  window: {},
  require: () => ({
    ActivityTracker: class {
      constructor() {
        throw new Error('Local lab must not create cloud activity')
      }
    },
  }),
})
vm.runInContext(compile('src/features/activity/index.ts', true), lab)
lab.exports.setActivitySession({ user: { id: 'owner' }, access_token: 'token' })
assert.equal(lab.exports.beginAttempt('a', 'song', 'builtin', 'now'), null)
console.log(
  'PASS: v2 sealed upload protocol, 412 continues to finalize verification, local lab disables activity.',
)

// A recording sealed while replay is in progress wakes another pass immediately.
let replayCalls = []
let releaseReplay
context.testReplay = async (owner) => {
  replayCalls.push(owner)
  if (replayCalls.length === 1) await new Promise((resolve) => { releaseReplay = resolve })
  return []
}
vm.runInContext('queue = { replay: testReplay }', context)
const running = context.exports.resumePendingUploads('owner')
await context.exports.resumePendingUploads('next-owner')
releaseReplay()
await running
assert.deepEqual(replayCalls, ['owner', 'next-owner'])
console.log('PASS: replay wakes for newly queued work and uses the current owner.')

// A failed PUT must wait for the other uploads to settle and never finalize.
let completedPuts = 0
let finalizations = 0
context.fetch = async (url, options) => {
  if (options.method === 'GET') return { ok: false, status: 404, headers: new Headers(), text: async () => 'Not found' }
  if (options.method === 'PUT') {
    await new Promise((resolve) => setTimeout(resolve, url.endsWith('/p') ? 1 : 15))
    completedPuts++
    return { ok: !url.endsWith('/p'), status: url.endsWith('/p') ? 500 : 200, headers: new Headers() }
  }
  if (url.endsWith('/finalize')) finalizations++
  return { ok: true, headers: new Headers(), json: async () => ({
    performance_url: 'https://gcs.test/p', reference_url: 'https://gcs.test/r',
  }) }
}
const failed = await context.uploadSealedRecording({
  schemaVersion: 2, songSource: 'builtin', songId: 'song', songTitle: null,
  durationSec: 1, midiBase64: 'TQ==', referenceMidiBase64: 'Ug==', activity,
}, 'owner', 'attempt')
assert.match(failed.error, /500/)
assert.equal(completedPuts, 2)
assert.equal(finalizations, 0)
console.log('PASS: failed parallel PUT waits for siblings and prevents finalize.')

// After an accepted receipt, recovery polls only: no re-sign/PUT/finalize.
let receiptChecks = 0
context.fetch = async (_url, options) => {
  assert.equal(options.method, 'GET')
  receiptChecks++
  return { ok: true, headers: new Headers(), json: async () => ({
    id: 'attempt', status: receiptChecks === 1 ? 'ACCEPTED' : 'PROCESSING',
    validated_at: receiptChecks === 1 ? null : '2026-11-05T00:00:00Z',
  }) }
}
const retained = await context.uploadSealedRecording({}, 'owner', 'attempt')
assert.equal(retained.validated, false)
const verified = await context.uploadSealedRecording({}, 'owner', 'attempt')
assert.equal(verified.validated, true)
console.log('PASS: accepted receipts poll without reupload; only verified files allow cleanup.')

// Missing server objects can be repaired from the retained immutable bundle.
let repairedPuts = 0
context.fetch = async (url, options) => {
  if (options.method === 'GET') return { ok: true, headers: new Headers(), json: async () => ({
    id: 'attempt', status: 'RETRY', validated_at: null, error_code: 'NotFound',
  }) }
  if (options.method === 'PUT') {
    repairedPuts++
    return { ok: true, headers: new Headers() }
  }
  return { ok: true, headers: new Headers(), json: async () => url.endsWith('/attempts') ? {
    performance_url: 'https://gcs.test/p', reference_url: 'https://gcs.test/r',
  } : { id: 'attempt', status: 'RETRY', validated_at: null } }
}
const repair = await context.uploadSealedRecording({
  schemaVersion: 2, songSource: 'builtin', songId: 'song', songTitle: null,
  durationSec: 1, midiBase64: 'TQ==', referenceMidiBase64: 'Ug==', activity,
}, 'owner', 'attempt')
assert.equal(repairedPuts, 2)
assert.equal(repair.validated, false)
console.log('PASS: missing files can be repaired without deleting recovery bytes.')
