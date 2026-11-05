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
const context = vm.createContext({
  exports: {},
  crypto,
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
    if (options.method === 'PUT') return { ok: false, status: 412 }
    return {
      ok: true,
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
const create = JSON.parse(requests[0].options.body)
assert.equal(create.schema_version, 2)
assert.equal(create.attempt_id, 'attempt')
assert.equal('activity' in create, false)
assert.equal('session_id' in create, false)
assert.equal(requests.filter((r) => r.options.method === 'PUT').length, 3)
const final = JSON.parse(requests.at(-1).options.body)
assert.deepEqual(final, { schema_version: 2, activity })
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
