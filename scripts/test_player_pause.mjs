// Exercise the real Player methods without loading browser audio/MIDI modules.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'

const source = ts.transpileModule(readFileSync('src/features/player/player.ts', 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText
let now = 1005
const context = vm.createContext({
  exports: {},
  require: () => ({}),
  performance: { now: () => now },
  clearInterval() {},
})
vm.runInContext(source, context)
const { Player } = context.exports
for (const rate of [0.5, 1, 2]) {
  let state = 'Playing'
  const player = Object.create(Player.prototype)
  Object.assign(player, {
    currentSongTime: 10,
    lastIntervalFiredTime: 1000,
    state: 'state',
    bpmModifier: 'rate',
    store: {
      get: (key) => (key === 'state' ? state : rate),
      set: (_key, value) => {
        state = value
      },
    },
    getSong: () => ({ notes: [] }),
    clearCountdown_: () => {},
    stopAllSounds: () => {},
  })
  now = 1005
  const beforePause = player.getTime()
  player.pause()
  assert.equal(player.getTime(), beforePause, 'pause must retain sub-tick position')
  now = 3000
  assert.equal(player.getTime(), beforePause, 'exit after pause must not rewind or advance')
  player.pause()
  assert.equal(player.getTime(), beforePause, 'repeated pause must be stable')
  state = 'Playing'
  player.lastIntervalFiredTime = now
  assert.equal(player.getTime(), beforePause, 'resume must start at the frozen position')
  now += 10
  assert.ok(player.getTime() > beforePause)
}
console.log('Player pause/exit/resume clock regressions passed at 0.5x, 1x and 2x.')
