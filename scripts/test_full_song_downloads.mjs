// Run after the read-only live download/hash audit, over its exact saved bytes.
import {readFileSync,readdirSync} from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
import {createRequire} from 'node:module'
import assert from 'node:assert/strict'
const require=createRequire(import.meta.url)
const context={exports:{},require:name=>name==='@tonejs/midi'?require(name):{isBlack:n=>[1,3,6,8,10].includes(n%12)},Uint8Array,console}
vm.runInNewContext(ts.transpileModule(readFileSync('src/features/parsers/parse-midi.ts','utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText,context)
const root=process.argv[2]??'data/full-song-backfill/downloads'
const files=readdirSync(root).filter(p=>p.endsWith('.mid'))
assert.ok(files.length>0)
for(const file of files){const song=context.exports.default(new Uint8Array(readFileSync(root+'/'+file)));assert.ok(song.notes.length>0,file);assert.ok(Object.keys(song.tracks).length>0,file)}
console.log(`PASS actual frontend parseMidi: ${files.length} full songs`)
