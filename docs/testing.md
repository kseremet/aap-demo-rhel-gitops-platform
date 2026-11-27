# Testing log

Tracks the CrewAI EE demo and retained VM fallback. **Partial** means isolated checks passed;
user confirmation of the complete incident and maintainer-feedback loop is pending.

**Status values:** `Not tested` · `Pass` · `Partial` · `Fail` · `Blocked`

## Status summary

| Component | Status | Last tested | Notes |
|---|---|---|---|
| CrewAI VM runtime | Partial | 2026-11-27 | Repository listing/search and corrected directory reads are deployed. User confirmed incident PR #8. Both scripts now use Phoenix tracing and quieter output. CrewAI 1.15.17, OpenAI 2.54.0, existing .env and external Linux MCP selection are preserved. |
| CrewAI incident and PR-review job templates | Partial | 2026-11-27 | Primary JTs 87/88 now run inside CrewAI EE 5 with API-token injection. Safe entrypoint jobs 6473/6475 passed. VM fallbacks 99/100 independently passed jobs 6470/6472 using the original SSH credential and default EE. A real EE incident-to-PR and feedback cycle remain pending. |
| EDA GitHub routing | Partial | 2026-11-27 | User confirmed redelivering the issues.opened event produced PR #8. Four offline event-contract checks cover issue investigation and feedback payloads; approval and agent-comment loop guards remain. Full feedback-loop validation is pending. |
| AAP Configuration as Code | Partial | 2026-11-27 | Full EE cutover CasC completed with localhost 179 ok/15 changed and ai-01 9 ok/0 changed; zero failures. EDA activation 36 is enabled/running. Platform project 82 is synced to codex/crewai-ee-cutover-20261127. Syntax and offline lint passed. |
| Managed inventory address refresh | Partial | 2026-11-27 | Offline checks cover AI/fleet address updates without changing membership or SSH options, and MCP alias updates preserving identity and unrelated aliases. Generated local files remain untouched. |
| Shared MCP services | Partial | 2026-11-27 | Original GitHub MCP container/port 8082 and Linux MCP deployment, SSH configuration and policy were preserved. Sidecar and port 8083 were removed. CrewAI discovered 23 Linux tools and read web-01 somaxconn successfully. |
| Phoenix dashboard and CrewAI tracing | Partial | 2026-11-27 | One internal Phoenix 22.1.0 pod is healthy under restricted-v2 with a bound 2 GiB PVC. Browser UI and synthetic crew/agent/tool/model hierarchy verified. Five child spans arrived while the run root was still open; the final root flushed at exit. Real incident tracing awaits user testing. |
| CrewAI execution environment and JT cutover | Partial | 2026-11-27 | User confirmed native build, smoke tests and push. Verified public amd64 image and pinned its digest in local CasC configuration. AAP verification job 6468 passed as UID 1000950000 with GitHub access, fresh clone, both imports, external MCP host read and a real model reply. Phoenix received all six spans with successful root status. Thirty regression checks passed; real incident/feedback testing remains pending. |

## Live EE cutover verification

- Image: `quay.io/koray/ee-rhel-gitops-crewai:1.15.17-1`, pinned to
  `sha256:03e015c98fcc8301d18cd78f60134d9cc9c053580486e102feae9515c169f686`.
  AAP pulled it successfully and ran the verification as a nonroot OpenShift UID.
- Verification JT 101/job 6468 authenticated to GitHub, cloned state revision
  `e049889c17d49c183bacf6cd8509af0a413ce569`, discovered 23 external MCP tools,
  read web-01's OS release and received READY from the configured model. It
  offered no publication tools and created no PR or managed-host change.
- Phoenix trace `ad72d2840ec1a8570777b8d52fcb44a5` contains the successful
  verification root, crew, agent, Linux tool and model spans.
- Primary incident job 6473 used `--help`; primary review job 6475 used an empty
  event. Both passed on localhost in EE 5. VM fallback jobs 6470/6472 passed the
  corresponding safe checks over SSH using default EE 2 and the original key.
- The EE currently follows `demo_variables.yml` and uses `gpt-4o-mini`. The
  preserved VM uses `gpt-5.6-luna`; both point to the same provider endpoint.
  This difference was reported to the user before a real incident test.
- Deployment source was published on `codex/crewai-ee-cutover-20261127` using a
  separate Git index. The original checkout, staging area and pending local
  changes were preserved; the three pre-existing inventory/fact changes were
  excluded from the deployment branch. The local ignored demo configuration now
  selects EE mode, the immutable image and this platform SCM branch.

## CrewAI EE preparation verification

- The separate `context/crewai/execution-environment.yml` uses the supported
  RHEL 9 EE base and an isolated Python 3.12 virtual environment for CrewAI.
  Direct dependency versions match the working VM. The build archive contains
  only the seven documented build inputs and no project credentials.
- Offline checks cover both runtime selections, literal credential injector
  templates, non-secret JT configuration, independent VM fallback definitions,
  argument-array execution and cleanup of per-job working directories.
- The two EE entrypoints run on localhost, use fresh private repository clones,
  and retain external Linux MCP access and Phoenix tracing. CasC and AAP cleanup
  definitions are prepared but have not been applied for the EE cutover.
- Live JTs remain on the working VM. Existing modified inventory/fact files and
  the working VM agent/tracing source files were verified unchanged during EE
  preparation. Nothing was committed or pushed.
- The user will build and push on an x86_64 Linux machine, then provide the image
  reference and smoke-test results. Native image validation and Controller image
  pull, credential injection, connectivity and incident/feedback checks will
  precede completion of the AAP cutover.
- The first native build failed in Builder's `assemble` step while compiling
  inherited `systemd-python` requirements because `Python.h` was missing. The
  EE definition now installs the compiler, Python 3.12 headers and system-library
  development headers in `prepend_builder`. The updated build context and archive
  are regenerated; a native rebuild and image smoke tests remain pending.

## Phoenix and navigation verification

- Twenty-four offline regression checks passed, including repository discovery,
  traversal/symlink confinement, trace flushing on failure, disabled tracing and
  internal single-writer Phoenix deployment. CasC syntax and offline lint passed.
- A synthetic CrewAI task used the real repository listing/search tools and an
  HTTP-mocked OpenAI client. Phoenix received the crew, agent, tools and model
  spans before the enclosing run ended. The complete trace contained six spans
  without errors. No live model request, host remediation or PR publication was
  performed during tracing validation.
- The dashboard is internal at `http://phoenix.koray.svc.cluster.local:6006`.
  Browser access through Telepresence was verified. No public Route, separate
  collector, external database or cloud account was introduced.
- Both entrypoints share the tracing helper and pinned instrumentation through
  the same role tasks used by initial setup and CasC runtime updates. Phoenix
  setup and data-preserving cleanup playbooks are documented in `setup.md`.
- The existing environment fingerprint and CrewAI/OpenAI versions were verified
  after deployment; external MCP discovery still returned 23 tools. Original
  modified inventory/fact files were checked byte-for-byte against their backups.
- The final repeated CasC apply changed no Phoenix resources, packages, settings
  or VM files. Only the temporary in-memory inventory registration reported a
  change. The final helper's successful root-span status was verified as `OK`.

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

- Validate one real incident through the EE with Phoenix tracing enabled, then
  confirm a human feedback event updates that same PR without an agent-comment loop. The user
  already confirmed the incident-to-PR path produced PR #8 before tracing was enabled.
- Review webhook deliveries during the short EDA maintenance window and redeliver
  any missed events. The configured token returned HTTP 403 for delivery auditing;
  use the repository webhook UI or a credential with webhook-read permission.
- Keep the VM fallback available until the real EE incident and feedback paths
  are confirmed working. Confirm the desired model if the EE should match the
  VM instead of the current declared `gpt-4o-mini` setting.
