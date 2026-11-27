# Testing log

Tracks the VM-based CrewAI demo. **Partial** means isolated checks passed;
user confirmation of the complete incident and maintainer-feedback loop is pending.

**Status values:** `Not tested` · `Pass` · `Partial` · `Fail` · `Blocked`

## Status summary

| Component | Status | Last tested | Notes |
|---|---|---|---|
| CrewAI VM runtime | Partial | 2026-11-27 | Existing Python 3.12 environment, CrewAI imports, authenticated GitHub reads, configured-model completion and Linux MCP diagnostics succeeded. Agent scripts and runtime configuration are preserved. |
| CrewAI incident and PR-review job templates | Partial | 2026-11-27 | Both original templates, AI inventory, machine credential and entrypoint playbooks are retained. AAP ad hoc job 6460 successfully ran the existing incident entrypoint with --help using that inventory and credential. Full incident-to-PR and same-PR revision validation remains pending. |
| EDA GitHub routing | Partial | 2026-11-27 | Demo activation switched to CrewAI. Four offline event-contract checks cover issue investigation and review/inline/top-level feedback payloads; approval and agent-comment loop guards remain. |
| AAP Configuration as Code | Partial | 2026-11-27 | CrewAI-only CasC completed successfully (144 ok, 6 changed, 0 failed). Controller and EDA synced the cleanup revision, and the activation is running with direct CrewAI routes and no AO payload variables. Syntax and offline lint passed. |
| Managed inventory address refresh | Partial | 2026-11-27 | Offline checks cover AI/fleet address updates without changing membership or SSH options, and MCP alias updates preserving identity and unrelated aliases. Generated local files remain untouched. |
| Shared MCP services | Partial | 2026-11-27 | Original GitHub MCP container/port 8082 and Linux MCP deployment, SSH configuration and policy were preserved. Sidecar and port 8083 were removed. CrewAI discovered 23 Linux tools and read web-01 somaxconn successfully. |

## Cleanup verification

- The recovery tag is `ao-demo-before-crewai-cleanup-20261127`. Private configuration,
  resource exports and uncommitted-file backups are kept outside the repository.
- Removed the two demo AO workflows and dedicated EDA trigger account, three
  unshared AO integrations and their credentials, five live AO-only AAP job
  templates, and unused Controller credential objects/types. Sidecar maintenance
  templates declared in the repository were not present in live AAP.
- Removed the compatibility sidecar, its code volume/ConfigMap, annotation and
  port 8083. Original GitHub MCP container configuration is unchanged. Removed
  only the retired adapter hostname from AO's native HTTP allowlist.
- Preserved AO itself and every unrelated workflow definition. The shared
  **RHEL GitOps AAP** integration, its management credential and owning project
  remain because another user's `new-workflow` uses that integration. The
  script-node setting remains enabled because `test-workflow` uses script nodes.
- Removed AO-only code, workflows, patches, tests, variables and CasC declarations.
  CrewAI templates and entrypoint playbooks are unchanged. The replacement
  inventory refresh resolves live addresses in memory, including `ai-01`.
- Eleven focused regression checks, CasC syntax checking and offline lint passed.
  Live AAP job 6460 successfully imported the existing incident entrypoint with
  `--help` using the retained AI inventory and machine credential. The configured
  CrewAI model and authenticated GitHub reads succeeded; post-cleanup MCP reads
  succeeded and deployed script/configuration hashes stayed unchanged.
  The three pre-existing modified inventory/fact files were verified against
  their original SHA-256 checksums.
- No managed-host configuration change, new investigation, PR publication,
  package upgrade or CrewAI containerization was performed during cleanup.

## Open issues

- Confirm one controlled GitHub issue produces a CrewAI PR, then a human feedback
  event updates that same PR without an agent-comment loop.
- Review webhook deliveries during the short EDA maintenance window and redeliver
  any missed events. The configured token returned HTTP 403 for delivery auditing;
  use the repository webhook UI or a credential with webhook-read permission.
- CrewAI containerization is a separate follow-up; the current runtime remains VM-based.
