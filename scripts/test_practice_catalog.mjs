import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'

const source = readFileSync('src/pages/songs/features.ts', 'utf8')
for (const flag of [undefined, 'false', 'true']) {
  const context = { exports: {} }
  vm.runInNewContext(
    ts.transpileModule(
      source.replace(
        'import.meta.env.VITE_ENABLE_PRACTICE_IMPORTS',
        JSON.stringify(flag) ?? 'undefined',
      ),
      {
        compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
      },
    ).outputText,
    context,
  )
  const songs = [
    { source: 'builtin', segment_id: 'official' },
    { source: 'local' },
    { source: 'base64' },
    { source: 'builtin' },
  ]
  const enabled = context.exports.practiceImportsEnabled
  assert.equal(enabled, flag === 'true')
  const selected = context.exports.practiceSongs(songs, enabled)
  assert.equal(selected.length, enabled ? 4 : 1)
  assert.equal(selected[0], songs[0])
  assert.equal(songs.length, 4, 'Never delete imported entries')
}
const page = readFileSync('src/pages/songs/page.tsx', 'utf8')
assert.ok(!page.includes('prefetchSongs'))
assert.ok(!page.includes('checkbox'))
assert.ok(page.includes('practiceImportsEnabled &&'))
assert.ok(page.includes('ManageFoldersForm'))
assert.ok(page.includes('mode="play"'))
console.log('Practice official-only catalog, opt-in restoration and prefetch removal passed')
