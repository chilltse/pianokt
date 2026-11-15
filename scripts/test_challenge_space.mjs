import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import { JSDOM } from 'jsdom'
import ts from 'typescript'

const context = vm.createContext({ exports: {} })
vm.runInContext(
  ts.transpileModule(readFileSync('src/pages/challenge/spaceShortcut.ts', 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  }).outputText,
  context,
)
const dom = new JSDOM('<button>Settings</button><input>')
const { window } = dom
const shortcut = context.exports.createChallengeSpaceShortcut()
let blocked = false,
  actions = 0,
  clicks = 0
window.addEventListener('keydown', (e) => shortcut.keydown(e, blocked, () => actions++), true)
window.addEventListener('keyup', shortcut.keyup, true)
const button = window.document.querySelector('button')
button.addEventListener('keydown', (e) => {
  e.stopPropagation()
  clicks++
})
const key = (type, repeat = false, target = button) =>
  target.dispatchEvent(
    new window.KeyboardEvent(type, { code: 'Space', bubbles: true, cancelable: true, repeat }),
  )
key('keydown')
key('keydown', true)
key('keydown')
assert.equal(actions, 1)
assert.equal(clicks, 0, 'toolbar must not activate again when transport consumes Space')
key('keyup')
key('keydown')
key('keyup')
assert.equal(actions, 2, 'next press resumes through the supplied transport action')
blocked = true
key('keydown')
assert.equal(clicks, 1, 'modal controls retain Space')
blocked = false
key('keydown', true)
assert.equal(actions, 2, 'closing modal during a held key must not resume')
key('keyup')
key('keydown')
key('keyup')
assert.equal(actions, 3)
key('keydown', false, window.document.querySelector('input'))
key('keyup')
assert.equal(actions, 3, 'text editing must not start playback')
shortcut.reset()
console.log('Challenge Space capture, repeats, modal isolation and editing checks passed')
