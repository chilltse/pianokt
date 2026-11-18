import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
const context = { exports: {}, setTimeout, clearTimeout }
vm.runInNewContext(ts.transpileModule(readFileSync('src/features/product-results/readTimeout.ts','utf8'), {compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText, context)
const { withHistoryTimeout } = context.exports
assert.deepEqual(await withHistoryTimeout(Promise.resolve([])), [])
await assert.rejects(withHistoryTimeout(new Promise(()=>{}), 5), /timed out/)
await assert.rejects(withHistoryTimeout(Promise.reject(new Error('offline'))), /offline/)
console.log('History empty/success, stalled read timeout and failure checks passed')
