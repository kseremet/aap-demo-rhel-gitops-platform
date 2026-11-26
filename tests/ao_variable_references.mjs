// Run AO's real frontend variable-reference validator in isolation.
import fs from 'node:fs'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { stripTypeScriptTypes } from 'node:module'

const [source, scratch] = process.argv.slice(2)
// Only this dependency-free function is needed from edgeHelpers. Its source
// logic is unchanged; TypeScript-only references disappear during conversion.
const edgeHelpers = fs.readFileSync(path.join(source, 'edgeHelpers.ts'), 'utf8')
const upstream = edgeHelpers.slice(edgeHelpers.indexOf('export function getUpstreamNodeIds('))
if (!upstream.startsWith('export function')) throw new Error('Upstream helper not found')
fs.writeFileSync(path.join(scratch, 'upstream.mjs'), stripTypeScriptTypes(upstream, { mode: 'transform' }))
let validator = fs.readFileSync(path.join(source, 'validation/rules/validateVariableReferences.ts'), 'utf8')
validator = stripTypeScriptTypes(validator, { mode: 'transform' })
validator = validator.replace("'../../edgeHelpers'", "'./upstream.mjs'")
fs.writeFileSync(path.join(scratch, 'validator.mjs'), validator)
const { validateVariableReferences } = await import(pathToFileURL(path.join(scratch, 'validator.mjs')))
const workflows = JSON.parse(fs.readFileSync(0, 'utf8'))
process.stdout.write(JSON.stringify(workflows.map(workflow => validateVariableReferences(
  workflow.nodes,
  workflow.edges.map(edge => ({ source: edge.from, target: edge.to })),
  { triggers: workflow.triggers }
))))
