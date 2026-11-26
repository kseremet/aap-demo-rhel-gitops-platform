// Exercise AO's actual visual-editor parser/serializer without installing its UI.
// TypeScript is transformed into an isolated scratch directory by Node 24+.
import fs from 'node:fs'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { stripTypeScriptTypes } from 'node:module'

const [source, scratch] = process.argv.slice(2)
for (const name of ['parser', 'serializer', 'defaults', 'operators', 'normalizer', 'tokenizer', 'types']) {
  let code = fs.readFileSync(path.join(source, `${name}.ts`), 'utf8')
  // UUID generation has no effect on expression syntax or semantics.
  code = code.replace("import { v4 as uuidv4 } from 'uuid'", "import { randomUUID as uuidv4 } from 'node:crypto'")
  code = stripTypeScriptTypes(code, { mode: 'transform' })
  code = code.replace(/(from\s+['"])(\.\/[^'"]+)(['"])/g, '$1$2.mjs$3')
  fs.writeFileSync(path.join(scratch, `${name}.mjs`), code)
}
const { parseExpression } = await import(pathToFileURL(path.join(scratch, 'parser.mjs')))
const { serializeExpression } = await import(pathToFileURL(path.join(scratch, 'serializer.mjs')))
const expressions = JSON.parse(fs.readFileSync(0, 'utf8'))
const results = expressions.map(expression => {
  const parsed = parseExpression(expression)
  if (!parsed.root) throw new Error(`Visual editor cannot parse: ${expression}`)
  const once = serializeExpression(parsed, { forBackend: true })
  const twice = serializeExpression(parseExpression(once), { forBackend: true })
  return { expression, once, twice }
})
process.stdout.write(JSON.stringify(results))
