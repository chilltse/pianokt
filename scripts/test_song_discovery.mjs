import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
function load(file, imports = {}) {
  const exports = {}
  vm.runInNewContext(ts.transpileModule(readFileSync(file, 'utf8'), {
    compilerOptions: {target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS},
  }).outputText, {exports, require: name => imports[name] ?? {}, Uint8Array})
  return exports
}
const model = load('src/features/song-discovery/model.ts')
const songs = Array.from({length: 23}, (_, i) => ({id: `s${i}`, segment_id: `s${i}`, song_id: 'parent', source: 'builtin', title: `Piece — Segment ${i}`, composer: 'Chopin'}))
assert.equal(model.searchSegments(songs, 'CHOPIN segment').length, 23)
assert.equal(model.searchSegments(songs, 'segment 22')[0].segment_id, 's22')
assert.equal(model.recommendationSegments({is_demo: true, items: [{segment_id: 's1'}]}, songs).length, 0)
assert.equal(model.recommendationSegments({items: [{segment_id: 's1'}]}, songs).length, 0)
const recommended = model.recommendationSegments({is_demo: false, items: [{segment_id: 's2'}, {segment_id: 'missing'}, {segment_id: 's1'}, {segment_id: 's2'}, {song_id: 'parent'}]}, songs)
assert.equal(recommended.map(s => s.id).join(','), 's2,s1')
const history = load('src/features/challenge-history/api.ts')
const best = history.getBestAccuracyPerSong([
  {song_source: 'builtin', song_id: 'parent', segment_id: 's1', accuracy_pct: 80, score_source: 'aligned'},
  {song_source: 'builtin', song_id: 'parent', segment_id: 's2', accuracy_pct: 95, score_source: 'aligned'},
  {song_source: 'builtin', song_id: 'legacy.mid', accuracy_pct: 70, score_source: 'aligned'},
])
assert.equal(best.size, 3)
assert.equal(best.get('builtin/s1').bestAccuracy, 80)
assert.equal(best.get('builtin/s2').bestAccuracy, 95)
assert.ok(best.has('builtin/legacy.mid'))
let state, cleanup
const loads = new Map(), setups = [], players = []
class Player {
  store = {sub: () => () => {}}
  constructor() { players.push(this) }
  async setSong() { await new Promise(resolve => setups.push(resolve)) }
  play() { this.playing = true }
  stop() { this.playing = false }
  toggle() { this.playing = !this.playing }
  isPlaying() { return this.playing }
}
const {useSegmentPreview} = load('src/features/song-discovery/preview.ts', {
  react: {useRef: value => ({current: value}), useState: value => [value, next => {state = next}], useCallback: fn => fn, useEffect: fn => {cleanup = fn()}},
  jotai: {createStore: () => ({})},
  '@/features/data/catalog': {loadOfficialMidi: song => new Promise((resolve, reject) => loads.set(song.storage_path, {resolve, reject}))},
  '@/features/parsers': {parseMidi: () => ({})},
  '@/features/player/player': {Player},
  '@/features/SongVisualization/utils': {getDefaultSongSettings: () => ({metronome: {}})},
})
const preview = useSegmentPreview()
const a = {...songs[1], storage_path: 'a'}, b = {...songs[2], storage_path: 'b'}
const tick = () => new Promise(resolve => setImmediate(resolve))
const first = preview.toggle(a)
loads.get('a').resolve(new ArrayBuffer(0)); await tick()
const second = preview.toggle(b)
loads.get('b').resolve(new ArrayBuffer(0)); await tick()
setups[1](); await second
assert.equal(players[1].playing, true)
setups[0](); await first
assert.equal(players[0].playing, false)
await preview.toggle(b); assert.equal(players[1].playing, false)
await preview.toggle(b); assert.equal(players[1].playing, true)
preview.stop(); assert.equal(players[1].playing, false)
const third = preview.toggle(a)
cleanup(); loads.get('a').resolve(new ArrayBuffer(0)); await third
assert.equal(players.length, 2)
const fourth = preview.toggle(a)
loads.get('a').reject(new Error('download failed')); await fourth
assert.equal(state.status, 'error')
const retry = preview.toggle(a)
loads.get('a').resolve(new ArrayBuffer(0)); await tick(); setups[2](); await retry
assert.equal(players[2].playing, true)
preview.stop()
console.log('Song discovery: search, recommendation filtering, segment history, playback races, pause/resume, cleanup and retry passed')
let marked = [], authenticated = true, failWrite = false, writes = 0, releaseWrite
const mutations = []
const swrResult = {get data() {return marked}, mutate: async fn => {marked = fn(marked)}}
const client = {from: table => {
  const query = {eq() {return query}, then(resolve) {return new Promise(done => {releaseWrite = () => {resolve({error: failWrite ? new Error('save failed') : null}); done()}})}}
  return {delete: () => {writes++; mutations.push(['delete', table]); return query}, upsert: (value, options) => {writes++; mutations.push(['insert', table, value, options]); return query}}
}}
const hooks = load('src/features/song-discovery/hooks.ts', {
  react: {useState: value => [value, () => {}]},
  swr: {default: () => swrResult},
  '@/features/auth': {useOptionalAuth: () => authenticated ? {user: {id: 'auth-user'}} : null},
  '@/features/auth/supabase': {supabase: client},
})
const marks = hooks.useSegmentMarks()
const write = marks.toggle('s1'); await tick()
await marks.toggle('s1'); assert.equal(writes, 1)
releaseWrite(); await write
assert.equal(marked.join(','), 's1')
assert.equal(mutations[0][2].user_id, 'auth-user')
assert.equal(mutations[0][3].ignoreDuplicates, true)
const write2 = marks.toggle('s2'); await tick(); releaseWrite(); await write2
assert.equal(marked.join(','), 's1,s2')
failWrite = true
const failed = marks.toggle('s1'); await tick(); releaseWrite(); await failed
assert.equal(marked.join(','), 's1,s2')
failWrite = false
const removal = marks.toggle('s1'); await tick(); releaseWrite(); await removal
assert.equal(marked.join(','), 's2')
authenticated = false
await hooks.useSegmentMarks().toggle('s3'); assert.equal(writes, 4)
console.log('Segment marks: auth identity, separate segments, duplicate-click guard, idempotent insert, deletion and failure preservation passed')
