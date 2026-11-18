import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'

function load(file, imports = {}) {
  const exports = {}
  vm.runInNewContext(
    ts.transpileModule(readFileSync(file, 'utf8').replaceAll('import.meta.env.DEV', 'true'), {
      compilerOptions: {
        target: ts.ScriptTarget.ES2022,
        module: ts.ModuleKind.CommonJS,
        jsx: ts.JsxEmit.ReactJSX,
      },
    }).outputText,
    { exports, require: (name) => imports[name] ?? {}, Uint8Array, Error },
  )
  return exports
}
const model = load('src/features/song-discovery/model.ts')
const songs = Array.from({ length: 23 }, (_, i) => ({
  id: `s${i}`,
  segment_id: `s${i}`,
  song_id: 'parent',
  source: 'builtin',
  title: `Piece — Segment ${i}`,
  composer: 'Chopin',
}))
assert.equal(model.searchSegments(songs, 'CHOPIN segment').length, 23)
assert.equal(model.searchSegments(songs, 'segment 22')[0].segment_id, 's22')
const history = load('src/features/challenge-history/api.ts', { '../../../shared/challenge-scoring.json': { default: JSON.parse(readFileSync('shared/challenge-scoring.json', 'utf8')) } })
assert.equal(history.isChallengeSuccess(79.99), false)
assert.equal(history.isChallengeSuccess(80), true)
const best = history.getBestAccuracyPerSong([
  {
    song_source: 'builtin',
    song_id: 'parent',
    segment_id: 's1',
    accuracy_pct: 80,
    score_source: 'aligned',
  },
  {
    song_source: 'builtin',
    song_id: 'parent',
    segment_id: 's2',
    accuracy_pct: 95,
    score_source: 'aligned',
  },
  { song_source: 'builtin', song_id: 'legacy.mid', accuracy_pct: 70, score_source: 'aligned' },
])
assert.equal(best.size, 3)
assert.equal(best.get('builtin/s1').bestAccuracy, 80)
assert.equal(best.get('builtin/s2').bestAccuracy, 95)
assert.ok(best.has('builtin/legacy.mid'))
let state, cleanup
const loads = new Map(),
  setups = [],
  players = []
class Player {
  store = {
    sub: (_, callback) => {
      this.onState = callback
      return () => {
        this.unsubscribed = true
        this.onState = null
      }
    },
  }
  getTime() {
    return this.time ?? 0
  }
  getDuration() {
    return 10
  }
  seek(time) {
    this.time = time
  }
  constructor() {
    players.push(this)
  }
  async setSong() {
    await new Promise((resolve) => setups.push(resolve))
  }
  play() {
    this.playing = true
    this.onState?.()
  }
  stop() {
    this.playing = false
    this.onState?.()
  }
  toggle() {
    this.playing = !this.playing
  }
  isPlaying() {
    return this.playing
  }
}
const { useSegmentPreview } = load('src/features/song-discovery/preview.ts', {
  react: {
    useRef: (value) => ({ current: value }),
    useState: (value) => [
      value,
      (next) => {
        state = next
      },
    ],
    useCallback: (fn) => fn,
    useEffect: (fn) => {
      cleanup = fn()
    },
  },
  jotai: { createStore: () => ({}) },
  '@/features/data': {
    fetchSong: (id, source, song) =>
      new Promise((resolve, reject) => loads.set(song.storage_path, { resolve, reject })),
  },
  '@/utils': { getKey: (id, source) => `${source}/${id}` },
  '@/features/parsers': { parseMidi: () => ({}) },
  '@/features/player/player': { Player },
  '@/features/synth/utils': { getAudioContext: () => ({ resume: async () => {} }) },
  '@/features/SongVisualization/utils': { getDefaultSongSettings: () => ({ metronome: {} }) },
})
const preview = useSegmentPreview()
const a = { ...songs[1], storage_path: 'a' },
  b = { ...songs[2], storage_path: 'b' }
const tick = () => new Promise((resolve) => setImmediate(resolve))
const first = preview.toggle(a)
loads.get('a').resolve(new ArrayBuffer(0))
await tick()
const second = preview.toggle(b)
loads.get('b').resolve(new ArrayBuffer(0))
await tick()
setups[1]()
await second
assert.equal(players[1].playing, true)
setups[0]()
await first
assert.equal(players[0].playing, false)
await preview.toggle(b)
assert.equal(players[1].playing, false)
await preview.toggle(b)
assert.equal(players[1].playing, true)
preview.stop()
assert.equal(players[1].playing, false)
const third = preview.toggle(a)
cleanup()
loads.get('a').resolve(new ArrayBuffer(0))
await third
assert.equal(players.length, 2)
const fourth = preview.toggle(a)
loads.get('a').reject(new Error('download failed'))
await fourth
assert.equal(state.status, 'error')
const retry = preview.toggle(a)
loads.get('a').resolve(new ArrayBuffer(0))
await tick()
setups[2]()
await retry
assert.equal(players[2].playing, true)
preview.stop()
assert.equal(players[2].unsubscribed, true)
const local = preview.toggle({ id: 's1', source: 'local', storage_path: 'local' })
loads.get('local').resolve({})
await tick()
setups[3]()
await local
assert.equal(state.id, 'local/s1')
players[3].time = 10
players[3].stop()
assert.equal(state.status, 'paused')
await preview.toggle({ id: 's1', source: 'local' })
assert.equal(players[3].time, 0)
cleanup()
assert.equal(players[3].unsubscribed, true)
console.log(
  'PASS: search, segment history, official/local playback, races, pause/resume, end/restart, cleanup and retry',
)

// Exercise actual React components and actual SWR optimistic cache/rollback with
// a controlled Supabase transport. No mock implementation of mutate is used.
const { JSDOM } = await import('jsdom')
const dom = new JSDOM('<div id="root"></div>', { url: 'http://localhost' })
globalThis.window = dom.window
globalThis.document = dom.window.document
globalThis.HTMLElement = dom.window.HTMLElement
globalThis.IS_REACT_ACT_ENVIRONMENT = true
const React = await import('react')
const jsx = await import('react/jsx-runtime')
const { createRoot } = await import('react-dom/client')
const swr = await import('swr')
let userId = 'user-a',
  failWrite = false,
  writes = 0,
  releaseWrite
const database = new Map([
  ['user-a', []],
  ['user-b', []],
])
const transport = []
const client = {
  from(table) {
    assert.equal(table, 'piano_segment_marks', 'No recommendation requests allowed')
    const filters = {}
    let operation = 'select',
      value
    const query = {
      select() {
        return query
      },
      eq(key, val) {
        filters[key] = val
        return query
      },
      delete() {
        operation = 'delete'
        return query
      },
      upsert(row, options) {
        operation = 'insert'
        value = row
        assert.equal(options.ignoreDuplicates, true)
        return query
      },
      then(resolve) {
        if (operation === 'select')
          return Promise.resolve(
            resolve({
              data: (database.get(filters.user_id) ?? []).map((segment_id) => ({ segment_id })),
            }),
          )
        writes++
        const owner = value?.user_id ?? filters.user_id
        transport.push({ operation, owner, segment: value?.segment_id ?? filters.segment_id })
        return new Promise((done) => {
          releaseWrite = () => {
            if (!failWrite) {
              const current = database.get(owner)
              database.set(
                owner,
                operation === 'insert'
                  ? [...new Set([...current, value.segment_id])]
                  : current.filter((id) => id !== filters.segment_id),
              )
            }
            resolve({ error: failWrite ? new Error('save failed') : null })
            done()
          }
        })
      },
    }
    return query
  },
}
const realHooks = load('src/features/song-discovery/hooks.ts', {
  react: React,
  swr,
  '@/features/auth': { useOptionalAuth: () => (userId ? { user: { id: userId } } : null) },
  '@/features/auth/supabase': { supabase: client },
})
let catalogSongs = songs
const previewCalls = []
const listPreview = {
  state: { id: '', status: 'idle' },
  toggle: (song) => previewCalls.push(song),
  stop() {},
}
const ui = load('src/features/song-discovery/index.tsx', {
  react: React,
  'react/jsx-runtime': jsx,
  'lucide-react': Object.fromEntries(
    ['Heart', 'LoaderCircle', 'Pause', 'Play', 'RotateCcw'].map((name) => [
      name,
      (props) => React.createElement('svg', { ...props, 'data-icon': name }),
    ]),
  ),
  '@/features/data/library': { useFullSongCatalog: () => ({ data: [] }), useCatalogStatus: () => ({ data: catalogSongs }) },
  '@/utils': { getKey: (id, source) => `${source}/${id}`, formatTime: () => '0:10' },
  './hooks': realHooks,
  './model': model,
})
let compactMode = false
let hook,
  selected = 0
function Page() {
  hook = realHooks.useSegmentMarks()
  return React.createElement(
    React.Fragment,
    null,
    React.createElement(ui.SegmentMarkButton, { song: songs[0], marks: hook }),
    React.createElement(ui.SongDiscovery, {
      compact: compactMode,
      songs: catalogSongs,
      marks: hook,
      preview: listPreview,
      onSelect: () => {
        selected++
      },
    }),
  )
}
let root = createRoot(document.getElementById('root'))
let cache = new Map()
const config = { provider: () => cache, dedupingInterval: 0, shouldRetryOnError: false }
const render = async () => {
  await React.act(async () => {
    root.render(React.createElement(swr.SWRConfig, { value: config }, React.createElement(Page)))
    await tick()
  })
}
const flush = async () => {
  await React.act(async () => {
    await tick()
  })
}
const button = (label) => document.querySelector(`button[aria-label="${label}"]`)
const click = async (element) => {
  assert.ok(element)
  await React.act(async () => {
    element.click()
    await tick()
  })
}
const finish = async () => {
  await React.act(async () => {
    releaseWrite()
    await tick()
  })
}
await render()
await flush()
assert.ok(document.body.textContent.includes('Personalized recommendations coming soon.'))
assert.equal(document.querySelectorAll('section:last-child tbody tr').length, 10)
await click(button('Mark Piece — Segment 0'))
assert.equal(
  document.querySelectorAll('button[aria-label="Saving Piece — Segment 0"][aria-pressed="true"]')
    .length,
  3,
)
assert.equal(database.get('user-a').length, 0, 'optimistic update precedes server response')
await React.act(async () => {
  await hook.toggle('s0')
})
assert.equal(writes, 1)
await finish()
assert.deepEqual(database.get('user-a'), ['s0'])
assert.equal(document.querySelectorAll('button[aria-label="Unmark Piece — Segment 0"]').length, 3)
await click(button('Mark Piece — Segment 1'))
await finish()
assert.deepEqual(database.get('user-a'), ['s0', 's1'])
failWrite = true
await click(button('Unmark Piece — Segment 0'))
assert.equal(button('Saving Piece — Segment 0').getAttribute('aria-pressed'), 'false')
await finish()
assert.equal(button('Unmark Piece — Segment 0').getAttribute('aria-pressed'), 'true')
assert.ok(document.body.textContent.includes('save failed'))
failWrite = false
await click(button('Unmark Piece — Segment 0'))
await finish()
assert.deepEqual(database.get('user-a'), ['s1'])
failWrite = true
await click(button('Mark Piece — Segment 0'))
assert.equal(button('Saving Piece — Segment 0').getAttribute('aria-pressed'), 'true')
await finish()
assert.ok(button('Mark Piece — Segment 0'))
failWrite = false
await click(button('Play Piece — Segment 1'))
assert.equal(selected, 0, 'Preview does not select row')
assert.equal(previewCalls[0].segment_id, 's1')
// Unmount/remount with an empty SWR cache simulates a refresh or another device.
await React.act(async () => root.unmount())
cache = new Map()
root = createRoot(document.getElementById('root'))
await render()
await flush()
assert.ok(button('Unmark Piece — Segment 1'))
userId = 'user-b'
await render()
await flush()
assert.equal(hook.marks.size, 0)
await click(button('Mark Piece — Segment 2'))
await finish()
assert.deepEqual(database.get('user-b'), ['s2'])
userId = null
await render()
await flush()
const count = writes
await click(button('Mark Piece — Segment 0'))
assert.equal(writes, count)
assert.ok(document.body.textContent.includes('Please sign in'))
userId = 'user-a'
await render()
await flush()
assert.ok(button('Unmark Piece — Segment 1'))
// A pending old-account request must never change the new account's cache or error UI.
await click(button('Mark Piece — Segment 0'))
userId = 'user-b'
await render()
await flush()
await finish()
assert.equal(hook.marks.has('s0'), false)
assert.equal(hook.marks.has('s2'), true)
userId = 'user-a'
await render()
await flush()
assert.equal(hook.marks.has('s0'), true)
const next = () => [...document.querySelectorAll('button')].find((b) => b.textContent === 'Next')
await click(next())
await click(next())
assert.ok(document.body.textContent.includes('Page 3 of 3 · 23 results'))
assert.equal(document.querySelectorAll('section:last-child tbody tr').length, 3)
const input = document.querySelector('input[aria-label="Search songs"]')
await React.act(async () => {
  Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(
    input,
    'chopin segment 22',
  )
  input.dispatchEvent(new window.Event('input', { bubbles: true }))
  await tick()
})
assert.ok(document.body.textContent.includes('Page 1 of 1 · 1 results'))
assert.ok(
  document.querySelector('section:nth-of-type(2)').textContent.includes('Piece — Segment 1'),
  'search does not filter marks',
)
await React.act(async () => {
  Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(input, '')
  input.dispatchEvent(new window.Event('input', { bubbles: true }))
  await tick()
})
await click(next())
await click(next())
catalogSongs = songs.slice(0, 2)
await render()
assert.ok(document.body.textContent.includes('Page 1 of 1 · 2 results'))
assert.equal(
  transport.every((row) => ['user-a', 'user-b'].includes(row.owner)),
  true,
)
compactMode = true
await render()
assert.ok(document.body.textContent.includes('Recommended for You'))
assert.ok(!document.body.textContent.includes('Marked Songs'))
const favoritesFilter = [...document.querySelectorAll('button')].find(b => b.textContent.startsWith('Favorites ('))
assert.ok(favoritesFilter)
await click(favoritesFilter)
assert.equal(favoritesFilter.getAttribute('aria-pressed'), 'true')
assert.ok(document.querySelectorAll('tbody tr').length <= 10)
await React.act(async () => root.unmount())
// Render the actual Challenge page: history actions require explicit segment identity.
let modalProps,
  stops = 0
listPreview.stop = () => {
  stops++
}
const productModel = load('src/features/product-results/model.ts')
let historyOverride = null
const productLibrary = load('src/features/product-results/ChallengeLibrary.tsx', {
  react: React, 'react/jsx-runtime': jsx,
  '@/features/song-discovery': ui,
  '@/features/song-discovery/model': model,
  '@/features/challenge-history': { isChallengeSuccess: n => n >= 80 },
  '@/utils': { formatTime: () => '0:10' },
  './model': productModel,
  './store': { useProductResults: () => historyOverride ?? ({ rows: [
    {attempt_id:'a',song_source:'builtin',segment_id:'s1',midi_sha256:songs[0].midi_sha256,ended_at:'2026-11-15',completion:1,is_completed:true,frontend_note_accuracy:.91},
    {attempt_id:'b',song_source:'builtin',song_id:'legacy',ended_at:'2026-11-14',completion:.2,is_completed:false,frontend_note_accuracy:null},
  ] }) },
})
const Challenge = load('src/pages/challenge-songs/page.tsx', {
  '@/features/product-results/ChallengeLibrary': productLibrary,
  react: React,
  'react/jsx-runtime': jsx,
  '@/components': { AppBar: () => null, MarketingFooter: () => null, Sizer: () => null },
  '@/features/auth': { useRequireAuth: () => ({ user: { id: userId }, loading: false }) },
  '@/features/challenge-history': {
    ...history,
    isChallengeSuccess: (n) => n >= 80,
    listChallengeRecordings: async () => ({
      data: [
        {
          song_source: 'builtin',
          song_id: 'parent',
          segment_id: 's1',
          song_title: 'Segment one',
          accuracy_pct: 91,
          score_source: 'aligned',
        },
        {
          song_source: 'builtin',
          song_id: 's2',
          song_title: 'Legacy title',
          accuracy_pct: 70,
          score_source: 'aligned',
        },
      ],
    }),
  },
  '@/features/data/library': { useSongManifest: () => songs },
  '@/features/song-discovery': { ...ui, useSegmentPreview: () => listPreview },
  '@/features/song-discovery/hooks': realHooks,
  '@/features/SongPreview': {
    SongPreviewModal: (props) => {
      modalProps = props
      return null
    },
  },
  '@/utils': { getKey: (id, source) => `${source}/${id}` },
  'react-router': { Link: ({ children }) => React.createElement('span', null, children) },
}).default
root = createRoot(document.getElementById('root'))
await React.act(async () => {
  root.render(React.createElement(swr.SWRConfig, { value: config }, React.createElement(Challenge)))
  await tick()
})
await flush()
assert.ok(document.body.textContent.includes('Continue challenge'))
assert.ok(document.querySelector('article').textContent.includes('Best Accuracy · 91.0%'))
assert.ok(document.body.textContent.includes('Recommended for You'))
assert.ok(!document.body.textContent.includes('Featured Songs'))
assert.equal(document.querySelectorAll('section').length >= 3, true)
await click([...document.querySelectorAll('[role="tab"]')].find(b=>b.textContent==='Challenged Songs'))
const historyRows = document.querySelectorAll('article')
assert.equal(historyRows.length, 2)
assert.ok(historyRows[1].textContent.includes('Unavailable segment'))
assert.equal(historyRows[1].querySelectorAll('button[aria-label^="Play"]').length, 0)
await click(historyRows[0].querySelector('button[aria-label^="Play"]'))
assert.equal(modalProps.show, false)
await click([...historyRows[0].querySelectorAll('button')].find(b=>b.textContent==='Replay'))
assert.equal(modalProps.songMeta.segment_id, 's1')
assert.equal(modalProps.mode, 'challenge')
assert.equal(stops, 1)
await click([...document.querySelectorAll('[role="tab"]')].find(b=>b.textContent==='All Songs'))
for (const [state, message] of [
  [{rows:[],loading:true}, 'Loading your most recent challenge'],
  [{rows:[],loading:false,error:'failed'}, 'Unable to load your most recent challenge'],
  [{rows:[],loading:false}, 'Your most recent challenge will appear here.'],
]) {
  historyOverride = state
  await React.act(async () => {
    root.render(React.createElement(swr.SWRConfig, { value: config }, React.createElement(Challenge)))
    await tick()
  })
  assert.ok(document.body.textContent.includes(message))
  assert.ok(document.body.textContent.includes('Recommended for You'))
  for (const button of document.querySelectorAll('button')) {
    assert.ok(button.classList.contains('cursor-pointer'), button.textContent)
    if (button.disabled) assert.ok(button.classList.contains('disabled:cursor-not-allowed'))
  }
}
await React.act(async () => root.unmount())
dom.window.close()
console.log(
  'PASS: Challenge history shares actions, excludes unresolved legacy identities, and stops inline preview before modal',
)
console.log(
  'PASS: real React/SWR optimistic UI, shared sections, persistence reload, rollback, auth isolation, anonymous guard, preview event, search and pagination; recommendations placeholder only',
)
