// Exercise the installed visual editor's actual port conversion functions.
import fs from 'node:fs'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { stripTypeScriptTypes } from 'node:module'
const [source,scratch] = process.argv.slice(2)
let code=fs.readFileSync(path.join(source,'edgeHelpers.ts'),'utf8')
// Only framework enum constants are mocked; both conversion functions and the
// switch-port classifier are loaded unchanged from AO's source checkout.
code=code.replace("import { EdgeHandleEnum } from '@syntara/contracts'",`const EdgeHandleEnum={TRUE:'true',FALSE:'false',APPROVED:'approved',REJECTED:'rejected',LOOP:'loop',DONE:'done',END:'end',DEFAULT:'default',SOURCE:'source'}`)
code=code.replace("'./switchCaseHelpers'","'./switchCaseHelpers.mjs'")
fs.writeFileSync(path.join(scratch,'edgeHelpers.mjs'),stripTypeScriptTypes(code,{mode:'transform'}))
fs.writeFileSync(path.join(scratch,'switchCaseHelpers.mjs'),stripTypeScriptTypes(fs.readFileSync(path.join(source,'switchCaseHelpers.ts'),'utf8'),{mode:'transform'}))
const {v2PortToHandle,handleToV2Port}=await import(pathToFileURL(path.join(scratch,'edgeHelpers.mjs')))
const workflows=JSON.parse(fs.readFileSync(0,'utf8'))
process.stdout.write(JSON.stringify(workflows.map(w=>({
 edges:w.edges.map(e=>({...e,from_port:handleToV2Port(v2PortToHandle(e.from_port))})),
 invalidNamedPorts:['ready','no_change','blocked'].map(p=>handleToV2Port(v2PortToHandle(p))??null)
}))))
