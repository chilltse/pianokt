// Run without Bun; use the project's installed TypeScript compiler to erase types.
import { spawnSync } from 'node:child_process'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import ts from 'typescript'

const dir = await mkdtemp(join(tmpdir(), 'pianokt-activity-test-'))
try {
  await writeFile(join(dir, 'package.json'), '{"type":"module"}')
  for (const name of ['tracker', 'tracker.test', 'index', 'lifecycle.test']) {
    const source = (await readFile(`src/features/activity/${name}.ts`, 'utf8'))
      .replaceAll("'./tracker.ts'", "'./tracker.js'")
      .replaceAll("'./tracker'", "'./tracker.js'")
      .replaceAll("'./index.ts'", "'./index.js'")
      .replaceAll('import.meta.env.VITE_PIANOKT_API_URL', '"https://activity.test"')
    await writeFile(
      join(dir, `${name}.js`),
      ts.transpileModule(source, {
        compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
      }).outputText,
    )
  }
  const result = spawnSync(
    process.execPath,
    ['--test', join(dir, 'tracker.test.js'), join(dir, 'lifecycle.test.js')],
    {
      stdio: 'inherit',
    },
  )
  process.exitCode = result.status ?? 1
} finally {
  await rm(dir, { recursive: true, force: true })
}
