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

| AO Event Bridge (`ai/forward_event_to_ao.yml`) | Partial | 2026-11-26 | Clean SCM checkout test passed without ignored local configuration files. CasC explicitly enables launch variables and supplies TLS configuration. Live EDA-to-AO validation remains pending. |

### Workflows

| Component | Status | Last tested | Notes |
|---|---|---|---|
| AO incident investigation (`rhel-gitops-incident.yaml`) | Partial | 2026-11-26 | Added request scoping, repository mapping, independent evidence review, bounded targeted-diagnostic loop, final assessment, independent plan review, and explicit no-write branches. Importer substitutions, AO schemas, and routing checks using AO expression resolution passed. Live MCP/LLM validation is pending. |
| AO PR review (`rhel-gitops-pr-review.yaml`) | Partial | 2026-11-26 | Uses the same evidence/review harness while preserving PR identity and distinguishing revision requests from questions/approval. Importer substitutions, AO schemas, and routing checks passed. Live maintainer-feedback validation is pending. |

## Latest local validation

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
