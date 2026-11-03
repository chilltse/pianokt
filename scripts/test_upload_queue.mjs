import { spawnSync } from 'node:child_process'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import ts from 'typescript'
// Under node_modules so imports resolve the project's existing dependencies.
const dir = await mkdtemp(join(process.cwd(), 'node_modules/.pianokt-upload-test-'))
try {
  await writeFile(join(dir, 'package.json'), '{"type":"module"}')
  for (const name of ['upload-queue', 'upload-queue.test']) {
    const source = (await readFile(`src/features/challenge-history/${name}.ts`, 'utf8')).replaceAll("'./upload-queue'", "'./upload-queue.js'")
    await writeFile(join(dir, `${name}.js`), ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText)
  }
  process.exitCode = spawnSync(process.execPath, ['--test', join(dir, 'upload-queue.test.js')], { stdio: 'inherit' }).status ?? 1
} finally { await rm(dir, { recursive: true, force: true }) }
