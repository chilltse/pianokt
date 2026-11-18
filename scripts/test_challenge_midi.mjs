import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
const context = vm.createContext({ exports: {} })
vm.runInContext(ts.transpileModule(readFileSync('src/pages/challenge/midiSafety.ts', 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText, context)
const { midiSettingsBlockReason: blocked, usedWebMidi } = context.exports
const safe = { playing: false, countingDown: false, recording: false, finalizing: false, openNotes: false }
assert.equal(blocked(safe), null)
for (const flag of Object.keys(safe)) assert.ok(blocked({ ...safe, [flag]: true }), flag)
assert.equal(usedWebMidi([]), false)
assert.equal(usedWebMidi([{ type: 'note_on', velocity: 100, timestampSource: 'browser_keyboard' }]), false)
assert.equal(usedWebMidi([{ type: 'note_off', velocity: 100, timestampSource: 'web_midi' }]), false)
assert.equal(usedWebMidi([{ type: 'note_on', velocity: 0, timestampSource: 'web_midi' }]), false)
assert.equal(usedWebMidi([{ type: 'note_on', velocity: 1, timestampSource: 'web_midi' }]), true)
console.log('Challenge MIDI safety and telemetry checks passed')
