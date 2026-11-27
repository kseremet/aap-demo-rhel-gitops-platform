# Testing log

Tracks the CrewAI EE demo and retained VM fallback. **Partial** means isolated checks passed;
the EE incident-to-PR path is confirmed, while the maintainer-feedback loop is pending.

**Status values:** `Not tested` · `Pass` · `Partial` · `Fail` · `Blocked`

## Status summary

| Component | Status | Last tested | Notes |
|---|---|---|---|
| CrewAI VM runtime | Partial | 2026-11-27 | Both scripts use gpt-6-luna with the Responses API. The shared verifier passed for both production LLM factories, each executing a real repository tool and consuming its receipt. Existing credentials, provider, endpoint, MCP settings and package versions were preserved. |
| CrewAI incident and PR-review job templates | Partial | 2026-11-27 | User confirmed EE incident job 6481 created PR #9. Primary JTs 87/88 and verifier 101 now select responses through CasC. Independent VM fallbacks 99/100 retain their SSH credential and default EE. Maintainer-feedback execution remains pending. |
| EDA GitHub routing | Partial | 2026-11-27 | User confirmed redelivering issues.opened produced PR #9 through the EE. EDA was re-enabled by CasC. Feedback payload regression checks and loop guards pass; a real maintainer-feedback run remains pending. |
| AAP Configuration as Code | Partial | 2026-11-27 | Responses CasC completed with localhost 179 ok/11 changed and ai-01 11 ok/0 changed; zero failures. Initial runtime deployment changed four VM resources. Platform project 82 uses the existing deployment branch. Syntax and production-profile offline lint passed for affected playbooks, excluding vendored collections. |
| Managed inventory address refresh | Partial | 2026-11-27 | Offline checks cover AI/fleet address updates without changing membership or SSH options, and MCP alias updates preserving identity and unrelated aliases. Generated local files remain untouched. |
| Shared MCP services | Partial | 2026-11-27 | Original GitHub MCP container/port 8082 and Linux MCP deployment, SSH configuration and policy were preserved. Sidecar and port 8083 were removed. CrewAI discovered 23 Linux tools and read web-01 somaxconn successfully. |
| Phoenix dashboard and CrewAI tracing | Partial | 2026-11-27 | Real incident job 6481/PR #9 was traced; it exposed 17 Chat Completions rejections recovered by disabling reasoning. After explicit Responses selection, EE and VM verification traces each contain 15 OK spans, including four Response spans and both required tool calls, without ChatCompletion spans or errors. |
| CrewAI execution environment and JT cutover | Partial | 2026-11-27 | User confirmed the image build/push and real EE incident PR #9. Read-only verification job 6484 passed in 21.487 seconds as UID 1000950000: GitHub, fresh clone, 23 external MCP tools, web-01 read and both agent LLMs using Responses with real tool calls. Thirty-five regression checks pass. No EE rebuild was required; maintainer feedback remains pending. |

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
- The initial EE/VM model difference was subsequently resolved at the user's
  request: both now use `gpt-6-luna`, as verified below.
- Deployment source was published on `codex/crewai-ee-cutover-20261127` using a
  separate Git index. The original checkout, staging area and pending local
  changes were preserved; the three pre-existing inventory/fact changes were
  excluded from the deployment branch. The local ignored demo configuration now
  selects EE mode, the immutable image and this platform SCM branch.

## Shared gpt-6-luna verification

- The user selected `gpt-6-luna` for both runtimes. The local CasC setting,
  example configuration, role/EE defaults and script fallback default agree.
  CasC now reconciles only `AI_LLM_MODEL` in the existing VM environment.
- AAP verification job 6478 used `gpt-6-luna` and returned READY after successful
  GitHub, repository and external MCP checks. Phoenix trace
  `6f327bb5aff629e507a1394e69549261` contains six spans without errors.
- Both VM script imports reported `gpt-6-luna`, and the VM returned READY through
  the normal configured client. Phoenix trace
  `2ccb5e0093d6567575d1380bbe58b59e` contains the successful model-check spans.
- Per-setting fingerprints proved that only `AI_LLM_MODEL` changed in `.env`;
  credentials, provider, endpoint and MCP configuration were preserved. The EE
  image is unchanged and no rebuild was needed. Primary JTs 87/88 and verifier
  101 store the new model; VM fallbacks 99/100 retain default EE 2 and SSH access.
- Thirty regression checks, CasC syntax and offline lint passed. EDA remains
  enabled/running. No incident investigation, PR publication or host remediation
  was performed. Existing inventory/fact edits and the original Git checkout and
  staging area remain intact; updates use the existing deployment branch.
- Repeating the runtime-only CasC apply made zero changes on the VM (10 ok,
  0 changed, 0 failed); only temporary in-memory inventory registration changed.

## Responses API verification

- User-confirmed incident job 6481 created PR #9, activating only the proposed
  web-server somaxconn policy. Its Phoenix trace exposed 17 Chat Completions
  rejections; CrewAI recovered by retrying with reasoning effort set to none.
  The previous model-only readiness probe did not exercise tool compatibility.
- Both production LLM factories now use the shared `demo_ai_llm_api` setting,
  defaulting to `responses`. CasC injects it into both EE JTs and verifier 101,
  and reconciles `AI_LLM_API` on the retained VM. No AO changes or EE rebuild.
- The smoke test requires each production LLM factory to execute a read-only
  repository tool and repeat a fresh receipt available only in the tool result.
  Offline checks reject missing tool execution and unused/fabricated results.
- EE verification job 6484 passed at source revision
  `83ca8a3e7750d8c967047544864ac216958161fb`. Phoenix trace
  `89f4ffeed7e559d46308debb97f67a04` has 15 OK spans, including four
  Response spans, two verification tool calls and the external MCP read.
- The same verifier passed on the VM. Phoenix trace
  `458b7f57a4cc93ea38972408c85d88d6` also has 15 OK spans and four
  Response spans. Neither trace contains ChatCompletion spans or errors.
- Full CasC completed without failures. Repeating the runtime update through
  CasC made zero VM changes. Per-setting fingerprints prove all pre-existing
  VM environment values are unchanged; only `AI_LLM_API=responses` was added.
  CrewAI 1.15.17 and OpenAI 2.54.0 are preserved.
- Thirty-five regression checks, CasC syntax and affected-playbook offline lint
  passed. Vendored collection lint findings were excluded; their sources were
  not modified. The original Git checkout/index and existing inventory/fact
  edits are preserved. Deployment uses the existing separate branch/index.
- These checks offered no PR-publishing or remediation tools. The full incident
  and maintainer-feedback flow with the new API remains for user live testing.

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

- The user confirmed the real EE incident-to-PR path with Phoenix in PR #9.
  Retest the incident with Responses and confirm a human feedback event updates
  that same PR without an agent-comment loop.
- Review webhook deliveries during the short EDA maintenance window and redeliver
  any missed events. The configured token returned HTTP 403 for delivery auditing;
  use the repository webhook UI or a credential with webhook-read permission.
- Keep the VM fallback available until the real EE incident and feedback paths
  are confirmed working. Both runtimes now use the requested `gpt-6-luna`.
