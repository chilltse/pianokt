import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'

function load(file, imports, extra = '') {
  const exports = {}
  vm.runInNewContext(ts.transpileModule(readFileSync(file, 'utf8') + extra, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText, { exports, require: name => imports[name] ?? {}, crypto, Uint8Array, DOMException, AbortSignal, setTimeout })
  return exports
}
const cache = load('src/features/data/midiCache.ts', { 'idb-keyval': {} })
const bytes = new TextEncoder().encode('MThd test MIDI bytes').buffer
const hash = await cache.sha256(bytes)
const song = { midi_sha256: hash, storage_path: `midi/${hash}.mid` }
const disk = new Map()
let writes = 0, downloads = 0
const store = { get: async key => disk.get(key), set: async (key, value) => { writes++; disk.set(key, value) } }
const download = async () => { downloads++; await new Promise(resolve => setTimeout(resolve, 5)); return bytes }
const loader = cache.createMidiLoader(store, download)
await Promise.all([loader(song), loader(song), loader(song)])
assert.equal(downloads, 1)
assert.equal(writes, 1)
await loader(song)
assert.equal(downloads, 1)
// Recreate the loader: no process-local promise cache survives a page reload.
await cache.createMidiLoader(store, download)(song)
assert.equal(downloads, 1)
assert.equal(disk.size, 1)
const updated = new TextEncoder().encode('new MIDI version').buffer
const updatedHash = await cache.sha256(updated)
await cache.createMidiLoader(store, async () => { downloads++; return updated })({ midi_sha256: updatedHash, storage_path: 'new' })
assert.equal(downloads, 2)
assert.equal(disk.size, 2)
await assert.rejects(cache.createMidiLoader(store, async () => bytes)({ midi_sha256: 'a'.repeat(64), storage_path: 'corrupt' }), /mismatch/)
assert.equal(disk.size, 2)
const broken = { get: async () => { throw Error('IDB unavailable') }, set: async () => { throw Error('quota') } }
assert.equal(await cache.createMidiLoader(broken, async () => bytes)(song), bytes)
await assert.rejects(cache.createMidiLoader(broken, async () => { throw Error('offline') })(song), /offline/)
const controller = new AbortController()
const shared = cache.createMidiLoader({ get: async () => undefined, set: async () => {} }, download)
const cancelled = shared(song, controller.signal)
const survivor = shared(song)
controller.abort()
await assert.rejects(cancelled, { name: 'AbortError' })
assert.equal(await survivor, bytes)
const { createCatalog } = load('src/features/data/catalog.ts', { './midiCache': cache })
const row = { song_id: '70000000-0000-0000-0000-000000000001', segment_id: '80000000-0000-0000-0000-000000000001', segment_index: 1, title: 'Canon', difficulty: .5,
  midi_sha256: hash, storage_path: song.storage_path, duration_ms: 2000 }
let queries = 0
let cached
const directoryStore = { get: async () => cached, set: async rows => { cached = rows } }
const catalog = createCatalog(async () => { queries++; return [row] }, directoryStore)
await Promise.all([catalog.refresh(), catalog.refresh(), catalog.refresh()])
assert.equal(queries, 1)
assert.equal(catalog.resolve('70000000-0000-0000-0000-000000000001'), undefined)
assert.equal(catalog.resolve('80000000-0000-0000-0000-000000000001').midi_sha256, hash)
assert.equal(catalog.resolve('80000000-0000-0000-0000-000000000001').song_id, '70000000-0000-0000-0000-000000000001')
assert.equal(catalog.resolve('80000000-0000-0000-0000-000000000001').segment_index, 1)
const offline = createCatalog(async () => { throw Error('Supabase unavailable') }, directoryStore)
assert.equal((await offline.cached())[0].id, '80000000-0000-0000-0000-000000000001')
await assert.rejects(offline.refresh(), /unavailable/)
assert.equal((await offline.cached())[0].id, '80000000-0000-0000-0000-000000000001')
console.log('Song catalog/cache tests passed (directory dedupe, offline, persistence adapter, SHA, versioning, concurrency, quota, cancellation, segment identity)')
const history = load('src/features/challenge-history/api.ts', {})
const best = history.getBestAccuracyPerSong([
  { song_source: 'builtin', song_id: '70000000-0000-0000-0000-000000000001', segment_id: '80000000-0000-0000-0000-000000000001', score_source: 'aligned', accuracy_pct: 80 },
  { song_source: 'builtin', song_id: '70000000-0000-0000-0000-000000000001', segment_id: '80000000-0000-0000-0000-000000000001', score_source: 'aligned', accuracy_pct: 90 },
  { song_source: 'builtin', song_id: '70000000-0000-0000-0000-000000000001', segment_id: '80000000-0000-0000-0000-000000000002', score_source: 'aligned', accuracy_pct: 70 },
])
assert.equal(best.size, 2)
assert.equal(best.get('builtin/80000000-0000-0000-0000-000000000001').bestAccuracy, 90)
assert.equal(best.get('builtin/80000000-0000-0000-0000-000000000002').bestAccuracy, 70)
console.log('Segments of the same song retain separate practice scores')
const unavailableDirectory = createCatalog(async () => [row], {
  get: () => { throw Error('synchronous IndexedDB security exception') },
  set: async () => { throw Error('quota') },
})
assert.equal((await unavailableDirectory.refresh())[0].id, '80000000-0000-0000-0000-000000000001')
assert.equal((await unavailableDirectory.cached())[0].id, '80000000-0000-0000-0000-000000000001')
const aborted = new AbortController()
aborted.abort()
let abortedDownloads = 0
await assert.rejects(cache.createMidiLoader(broken, async () => { abortedDownloads++; return bytes })(song, aborted.signal), { name: 'AbortError' })
assert.equal(abortedDownloads, 0)

let initialized = 0
const songs = load('src/features/data/index.tsx', {
  '@/features/parsers': { parseMidi: bytes => ({ byteLength: bytes.length }) },
  '../persist/persistence': {
    initialize: async () => { initialized++ },
    getSongHandle: async id => id === 'local-file' ? { getFile: async () => ({ arrayBuffer: async () => bytes }) } : undefined,
  },
}, '\nexports.fetchSong = fetchSong;')
assert.equal((await songs.fetchSong('local-file', 'local')).byteLength, bytes.byteLength)
assert.equal(initialized, 1)
await assert.rejects(songs.fetchSong('missing', 'local'), /Could not get song/)
console.log('Local MIDI file-handle playback remains independent of the official catalog')

// A stale/invalid cached directory must not suppress fresh network data.
const invalidDirectory = createCatalog(async () => [row], {
  get: async () => [{ id: 'old.mid', source: 'builtin' }], set: async () => {},
})
assert.equal((await invalidDirectory.cached()).length, 0)
assert.equal((await invalidDirectory.refresh())[0].segment_id, row.segment_id)
const badServerDirectory = createCatalog(async () => [{ ...row, segment_index: 0 }], directoryStore)
await assert.rejects(badServerDirectory.refresh(), /Invalid official catalog/)
const twoSegments = createCatalog(async () => [
  { ...row, segment_id: '80000000-0000-0000-0000-000000000004', segment_index: 2 },
  { ...row, segment_id: '80000000-0000-0000-0000-000000000003', segment_index: 1 },
], { get: async () => [], set: async () => {} })
assert.deepEqual(Array.from(await twoSegments.refresh(), value => value.segment_index), [1, 2])
console.log('Invalid cached/server identities and stable segment ordering checks passed')
