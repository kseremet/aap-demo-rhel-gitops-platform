# Testing log

Tracks testing progress for this demo. For procedural verification steps, see [setup.md](setup.md).

**Status values:** `Not tested` · `Pass` · `Partial` · `Fail` · `Blocked`

## Status summary

### Setup playbooks

| Component | Status | Last tested | Notes |
|---|---|---|---|
| AI control VM configuration (`setup/04_configure_ai_vm.yml`) | Not tested | — | Ansible-managed container deployment and MCP client selection are pending validation. |

### Roles

| Component | Status | Last tested | Notes |
|---|---|---|---|
| AI control (`ai_control`) | Not tested | — | Containerized MCP resources and local/container mode selection are pending validation. |

### Job templates

| Component | Status | Last tested | Notes |
|---|---|---|---|
| AO GitOps Action (`ai/run_ao_gitops_action.yml`) | Partial | 2026-11-26 | Reviewed plan/report contract, pinned branch/blob checks, consumed-variable and exact YAML delta validation, full PR reporting, and atomic multi-file commit publication implemented. Offline fake-API tests cover create/revise and zero mutations on preflight failure. Syntax and offline lint passed; live AAP/GitHub validation is pending. |

| AO Evidence Validation (`ai/validate_ao_evidence.yml`) | Partial | 2026-11-26 | Read-only runtime provenance, commit/host scope, exact excerpts, typed JSON fields, and blocker resolution checks implemented. Local Ansible execution returns routable valid/blocked artifacts. Live CasC creation succeeded after refreshing the platform Project; live AAP job execution remains pending. |

| AO Event Bridge (`ai/forward_event_to_ao.yml`) | Partial | 2026-11-26 | Clean SCM checkout test passed without ignored local configuration files. CasC explicitly enables launch variables and supplies TLS configuration. Live EDA-to-AO validation remains pending. |

### Workflows

| Component | Status | Last tested | Notes |
|---|---|---|---|
| AO incident investigation (`rhel-gitops-incident.yaml`) | Partial | 2026-11-26 | Added request scoping, repository mapping, independent evidence review, bounded targeted-diagnostic loop, final assessment, independent plan review, and explicit no-write branches. Importer substitutions, AO schemas, and routing checks using AO expression resolution passed. Deployed AO API validation passed with zero findings. Isolated GitHub and Linux tool calls succeeded after repairing stale SSH aliases; full incident-to-PR validation remains pending. |
| AO PR review (`rhel-gitops-pr-review.yaml`) | Partial | 2026-11-26 | Uses the same evidence/review harness while preserving PR identity and distinguishing revision requests from questions/approval. Importer substitutions, AO schemas, and routing checks passed. Explicit read-only tools, runtime evidence validation, and blocker gates added. Deployed AO API validation passed; live maintainer-feedback validation remains pending. |

## Latest local validation

- 2026-11-26: Diagnosed the later `review_evidence` failure in execution
  `c3658946-ba73-4c9d-bd02-2d7fb68e653f`: backend logs show HTTP 422 because
  its resolved prompt was 74,300 characters and the invocation API permits
  10,000. Removed duplicated raw invocation wrappers from model handoffs,
  added deterministic compact JSON preparation and an explicit size guard
  before every agent, and retained complete original traces for AAP validation.
  All 144 local regression tests passed; both workflow draft validations returned
  zero findings. Script nodes were initially disabled. After explicit informed
  administrator approval, enabled the Developer Preview setting through AO's
  existing admin-settings ConfigMap and rolled the workflow worker. The isolated
  preparation node completed with an 8,130-character prompt. The final reviewed
  instructions use 8,412 characters; isolated review execution
  `e96cf36f-220b-4d08-a3e0-130debd294d1` completed and returned two focused
  follow-up checks. AAP validation/publication remained skipped. Focused lint,
  syntax, importer/schema, and routing checks passed. Reapplying the approved
  configuration reported zero changes. CasC management is explicitly opt-in
  and supports disabling the setting again. Full incident/PR
  feedback validation remains pending; no publication job was launched.

- 2026-11-26: Deployed a GitHub MCP text compatibility sidecar on the existing
  GitHub MCP Service hostname, port 8083. Kept AO source, image, operator settings,
  and allowlist unchanged. Preserved the original MCP port and pinned its
  upstream container to the already-running digest. AO integration validation
  succeeded and refreshed the five read-only tools, preserving their UUIDs.
  Isolated first-node execution `ba1d8122-2a26-427d-b401-bef31e0548bb`
  completed its scope step with four real GitHub calls and the complete policy
  YAML in its trace; no checkpoint error occurred. All downstream nodes were
  skipped. All 129 platform tests passed, including 18 adapter HTTP/SSE and
  normalization cases. Focused Ansible lint and CasC/cleanup syntax checks
  passed. A second live setup run reported zero changes. Full incident/PR
  feedback, maintenance JTs, and teardown remain pending maintainer testing.

- 2026-11-26: The maintainer confirmed combined CasC completed successfully.
  Incident invocation `4065cd0e-697a-48db-84a7-f59af4bcea44` then failed during
  checkpoint serialization. Pod logs confirmed actual GitHub tool calls.
  Reproduced the exact TypeError with an embedded MCP text resource's `AnyUrl`
  artifact and confirmed the old adapter also hides the file body from the model.
  Plain/error ToolMessages and the installed tool-error handler serialized
  successfully in isolated checks. Prepared an AO provider source patch; nine
  offline checks passed against the exact container dependencies, including
  visible resource text, streaming events, checkpoint round-trip, binary
  preservation, and error propagation. Patch application/compilation checks
  passed in a disposable source copy. No installed application files or running
  services were modified; AO image rollout is pending a separate decision.
- Corrected the AAP evidence validator to accept the installed GitHub MCP's `sha`
  revision argument as well as `ref`, with exact-commit and conflicting-alias
  rejection. Seven new regression cases cover the argument variants. All 111
  platform regression tests passed. Nine separate AO compatibility checks passed
  in memory using the installed MCP adapter factory and a fake MCP session,
  including JSON metadata aliases. The maintainer selected source-patch handoff;
  no AO rollout will be performed by this session. Full live incident/PR and
  maintainer-feedback runs remain pending; the compatibility workaround below
  avoids requiring that runtime rollout.

- 2026-11-26: Fixed AAP template creation failing with "Playbook not found for
  project." The platform Project was still synced to an October 19 revision,
  before the evidence-validator playbook existed. Added `update_project: true`
  and `wait: true` to its CasC declaration so the projects role refreshes the
  catalog before job templates are processed, even when Project settings have
  not changed. Ran the actual project and job-template CasC roles against AAP:
  20 tasks passed, two changed, zero failures. AAP synced to `365ed84`, exposed
  `playbooks/ai/validate_ao_evidence.yml`, and created **JT - AO Evidence
  Validation**. No workflow, managed-host job, or publication job was launched.
  Combined CasC syntax and focused offline lint of the AAP playbook/CasC vars
  passed with vendored collections excluded. The unfiltered AAP playbook lint
  reports ten pre-existing naming/line-length findings in the installed
  `infra.aap_configuration` collection; no vendor files were changed. Full CasC
  completion and evidence-validator job execution remain pending maintainer
  live testing.

- 2026-11-26: Preserved the Linux connection UI workaround in CasC with a
  dedicated non-secret HTTP Bearer Token credential named by the Linux MCP
  integration. Linux-only steps receive its connection; mixed-tool steps receive
  separate Linux and GitHub connections; GitHub-only steps retain only GitHub.
  All 27 workflow checks, including concrete importer credential bindings and
  frontend/backend validation, passed. Both deployed AO workflow validations
  returned zero findings; CasC syntax, offline lint, and diff checks passed.
  Created the dedicated dummy credential and confirmed an isolated AO Linux
  tool call using it returned `web-01-gitops`. Full workflow imports/publication
  remain a maintainer live-test step. Prompts, schemas, routing, and evidence
  gates are unchanged. The GitHub API token is not reused for Linux MCP.

- 2026-11-26: Final regression suite: **104 passed**. Combined CasC and
  evidence-validator syntax checks, offline Ansible lint (zero failures/warnings),
  both deployed AO workflow-validation requests (zero findings), and diff checks
  passed. Full live AAP/GitHub and maintainer-feedback testing remains pending.

- 2026-11-26: Added explicit per-stage read-only tool selections, mandatory
  collector tool-call gates, bounded tool prompts, raw runtime evidence handoffs,
  and a read-only AAP provenance validator before both ready/no-change routes.
  The writer repeats provenance validation before GitHub access. Reviewer blockers
  require explicit evidence-backed resolutions. Local checks cover fabricated
  evidence, failed/missing results, unpinned reads, incorrect hosts, short scalar
  results, display tool namespaces, unresolved blockers, and actual AAP artifact
  output. All four observations from the final live host probe also matched their
  successful runtime tool outputs. Both workflow definitions passed the deployed AO 1.3.0 validation API
  with zero errors/warnings; local syntax, frontend/backend expression, and lint
  checks passed. Full AAP incident-to-PR and maintainer-feedback runs remain pending.
- Live read-only AO diagnostics confirmed tools are callable with and without
  response schemas. GitHub returned the real main SHA. Linux MCP authorized calls
  but SSH used stale pod-network addresses. Refreshed only the four managed SSH
  aliases from live VMIs; policy and SSH identity were preserved. AO subsequently
  returned `web-01-gitops`, and the bounded updated host prompt read the actual
  somaxconn value `65535`. CasC now refreshes aliases without rewriting local
  generated inventory. The UI's Linux "Set up connection" label reflects the
  intentionally absent bearer credential, not an authentication failure.
- An isolated multi-tool workflow test exposed AO's
  `Type is not msgpack serializable: ToolMessage` error path; an unbounded
  diagnostic probe also reached the agent recursion limit. Added per-step tool
  budgets and sequential-read instructions. AO runtime failures remain blocking;
  no installed AO package or global system prompt was changed.


- 2026-11-26: Replaced frontend-rejected `loop.index` / `loop.item` references with
  the named loop's iteration count and the original reviewer checklist. All 21
  workflow checks passed, including AO's actual frontend variable-reference
  validator, editor condition round-trips, and backend routing for zero/one/four
  checks. A negative regression reproduces the reported unknown-loop alert.
  Only follow-up prompts changed; schemas and loop routing are unchanged.
  Live UI verification remains pending.

- 2026-11-26: Replaced raw `len(...)` condition calls with visual-builder-compatible
  `exists` / `isEmpty` gates in both workflows. All 19 workflow checks passed,
  including real frontend parser/serializer round-trips followed by backend
  evaluation for zero/four/five checks, approval/rejection, and unresolved blockers.
  Only the two conditions per workflow changed; response schemas and other workflow
  data are unchanged. Live UI save/run verification remains pending.

- 2026-11-26: Removed embedded JSON contracts from all 26 agentic prompts and
  replaced them with concise role-specific handoff requirements. Response schemas
  and all other workflow data are unchanged. All 17 workflow importer/schema and
  routing checks passed, along with CasC syntax and diff checks. Live agent behavior
  after this prompt cleanup remains pending.

- 2026-11-26: Seven model-selection regression cases passed against a local fake
  AO catalog API. Discovery now queries exact provider `model_id[eq]`, rather than
  a display label or the first unfiltered page, and requires the model to be enabled.
  Missing, disabled, duplicate, and wrong-provider records are rejected. CasC syntax,
  offline lint, and diff checks passed. Live catalog resolution remains pending.

- 2026-11-26: All 52 offline regression checks passed. New cases run both PR
  creation/revision and the EDA bridge from SCM-style checkouts without the ignored
  Vault and demo variable files. Added empty fallbacks while retaining local
  `vars_files`; AAP credentials and CasC supply execution settings.
- Combined CasC syntax, offline lint, and diff checks passed again. Deployment and
  live end-to-end testing were not run. Generated lab files remain local.

- 2026-11-23: 49 offline regression checks passed (`tests/`): writer rejection cases,
  actual playbook execution against a loopback-only fake GitHub API, both workflow
  imports and AO schemas, expression resolution and routing, diagnostic bounds, and the platform state-repository variable binding.
- Combined `playbooks/casc.yml` and writer syntax checks, offline lint of the writer
  and AO configuration entry point, and `git diff --check` passed.
- No deployment, live model request, managed-host change, or real GitHub write ran.
  The three generated lab inventory/facts files were preserved. README status is WIP
  while the redesigned harness awaits end-to-end validation.

## Open issues

- The user confirmed a successful manual MCP-over-HTTP tool call to `web-01`; the Ansible-managed deployment and CrewAI integration have not yet been run end to end.
- Live-test separate branches for repeated incidents and atomic revisions on the existing PR. AAP publishes all file changes in one commit, but later PR/report API failures can leave publication partially complete; inspect GitHub before relaunching.
- Make the updated writer available to the AAP platform project and apply both workflow and JT configuration through `playbooks/casc.yml` together. Uncommitted local code is not available to a remote AAP SCM sync.
- Live-test both workflows with the configured model: verify actual host/tool targeting, policy evidence, precedence/scope, reviewer-requested checks, no-change/blocked/rejected outcomes, complete reporting, and non-recursive feedback handling. See [ao-harness.md](ao-harness.md). Local checks do not establish runtime success.

- The AO runtime currently fails on embedded MCP file resources and omits their
  actual text from model input. The tested fix is prepared under
  `patches/automation-orchestrator/` but requires an AO backend image rollout;
  workflow imports alone cannot repair it. The GitHub MCP text compatibility
  sidecar now avoids this defect without an AO rollout; the isolated first node
  passed. Keep workflow status Partial until full incident/PR feedback tests are
  confirmed.
