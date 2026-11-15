import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'

const ctx = vm.createContext({ exports: {} })
vm.runInContext(
  ts.transpileModule(readFileSync('src/features/product-results/model.ts', 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText,
  ctx,
)
const { aggregateResults } = ctx.exports
for (const size of [0, 1, 9, 10, 11, 20, 26, 100, 1001]) {
  const rows = Array.from({ length: size }, (_, i) => ({
    attempt_id: String(i).padStart(5, '0'),
    song_source: 'builtin',
    segment_id: 'segment',
    ended_at: new Date(i * 1000).toISOString(),
    is_completed: i === 0,
    frontend_note_accuracy: i === 0 ? 0.9 : 1,
    completion: i === 0 ? 1 : 0.2,
  }))
  const groups = aggregateResults([...rows, ...rows], (p) => p >= 80)
  assert.equal(groups.length, size ? 1 : 0)
  if (size) {
    assert.equal(groups[0].rows.length, size)
    assert.equal(groups[0].accuracy, 0.9)
    assert.equal(groups[0].status, 'Succeeded')
  }
  for (let offset = 0; offset < size; offset += 10) {
    const slice = groups[0].rows.slice(offset, offset + 10)
    assert.ok(slice.length <= 10)
    assert.equal(slice.length, Math.min(10, size - offset))
  }
}
const partial = aggregateResults(
  [
    {
      attempt_id: 'one',
      song_source: 'local',
      song_id: 'a',
      ended_at: '2026',
      completion: 0.2,
      frontend_note_accuracy: 1,
      is_completed: false,
    },
  ],
  (p) => p >= 80,
)[0]
assert.equal(partial.accuracy, null)
assert.equal(partial.status, 'In Progress')
console.log('Product aggregation, deduplication, full/partial semantics and history sizes passed')
