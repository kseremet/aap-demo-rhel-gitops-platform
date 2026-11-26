# Automation Orchestrator investigation harness

This harness supports AAP 2.6 and the AO 2026.8 workflow format represented in the
reference documentation. Both incident and maintainer-feedback workflows use the
same investigation, evidence review, and publication gates. CrewAI remains a
separate backend. AO model selection is still controlled by `demo_ao_llm_model`. This value is the
provider model identifier (`model_id`, for example `gpt-4.1`), not the UI display
label. CasC queries that exact identifier and requires an enabled model.

## Investigation and review

```text
GitHub / EDA event
        |
Understand request and pin repository / PR context
        |
Collect targeted host evidence
        |
Trace repository variables, scope, and consuming System Role
        |
Analyze cause and alternatives
        |
Independent evidence review
        |
For each requested check (0 to 4): collect the missing observation
        |
Final assessment with the original and additional evidence
        |
Read-only AAP evidence validation against runtime tool traces
        |
        +-- no_change --> report why no repository edit is needed
        +-- blocked ----> report missing evidence / contradictions
        |
        +-- ready --> prepare complete variable-file edits
                             |
                      Independent plan review
                             |
                             +-- rejected --> report blockers; no writer call
                             |
                             +-- approved --> AAP validates and publishes
```

| Step | Responsibility |
|---|---|
| Request scoping | Read the issue or PR discussion; identify the host, actual requirement, repository, refs, and inspected commit. A question or approval alone does not request a revision. |
| Host investigation | Verify the target host and collect measurements relevant to the symptom. Do not diagnose the MCP container or equate a configuration mismatch with a proven outage. |
| Repository mapping | Establish active values, commented examples, inventory scope, baseline/group/host precedence, and the role consuming the result. |
| Causal analysis | Compare observed state, declared state, and policy. Consider reconciliation without a repository edit and other competing explanations. |
| Evidence review and loop | Challenge unsupported conclusions and request up to four specific, obtainable observations. The loop runs once for each check and aggregates results; it is skipped when there are no checks. |
| Final assessment | Resolve every finding or return blocked. Preserve the evidence, causal explanation, scope, alternatives, future validation, and limitations. |
| Edit planning and independent review | Inspect complete files at the pinned revision and check every proposed leaf change, affected host/group, and consuming variable. |

The condition gates use the visual builder's `exists` and `isEmpty` operators.
The four-check gate verifies that a fifth list item does not exist. The publication
gate requires approval and an empty blocker list. Do not replace these with raw
`len(...)` calls: the backend supports them, but the current visual editor can
serialize them into malformed nested variable references when saving.

The diagnostic agent references the named loop output
`evidence_loop.iteration_count` and the reviewer's original checklist, resolving
only the check at that zero-based index. Avoid the `loop.index` / `loop.item`
shorthand in saved prompts: the checked-out backend understands it, but the
frontend variable-reference validator rejects `loop` as an unknown node.

There is one diagnostic refinement cycle per run. Unresolved gaps do not start an
unbounded retry loop or force a PR. Each agent step has a 1,200-second timeout and
stops the branch on failure. The loop has an engine limit of five control turns:
a condition rejects more than four checks before the loop starts, and the current
AO engine checks its limit before the final completion turn. The AAP publication node has automatic retries disabled because a
blind retry after publication could create a second proposal.

Every agent has an explicit response schema, which is the authoritative output
structure. Prompts describe concise, role-specific handoff requirements rather
than duplicating the JSON schema. They require a self-contained evidence packet:
the checked-out AO runtime converts the agent's final answer into structured JSON
in a separate call, without the full tool transcript. A schema alone cannot recover
observations that the investigative answer omitted. The harness changes no AO
runtime source or global system prompt.

## Tool execution and evidence provenance

CasC discovers explicit, available read-only tool UUIDs from the two demo MCP
integrations. Repository steps receive GitHub tools, the host step receives Linux
tools, and cross-check steps receive both. Other project integrations and write
tools are excluded. The three initial collectors must record an actual tool call;
a missing call routes to a dedicated blocked terminal. A recorded call alone does
not establish a successful measurement.

The Linux MCP endpoint uses unauthenticated HTTP. To preserve the UI workaround,
CasC creates a dedicated **RHEL GitOps Linux MCP** HTTP Bearer Token credential
(named by `demo_ao_mcp_integration_name`) containing a non-secret dummy value.
Steps selecting Linux tools explicitly associate that credential with Linux MCP.
GitHub tools retain the **RHEL GitOps GitHub MCP** credential and its real GitHub
token. The dummy header satisfies AO's connection UI; Linux MCP does not validate
it, and it does not supply SSH credentials or fix host reachability. CasC preserves
these separate mappings on every workflow import.

Before importing workflows, CasC refreshes the existing managed Linux MCP SSH
aliases from running VMIs. It preserves the ConfigMap's policy, SSH identity, and
unrelated entries and does not rewrite generated local inventory files. Custom
external MCP endpoints remain responsible for their own host addressing. Projected
ConfigMap changes can take a short time to reach the MCP pod.

Every reported observation includes an exact `tool_output_excerpt`. The workflows
send the original runtime invocation wrappers, including loop/assessor outputs,
to **JT - AO Evidence Validation**. This read-only AAP job requires matched,
successful `tool_call`/`tool_result` pairs, rejects tool errors, checks the commit
against a real `get_commit` result, requires file reads pinned to that commit,
and requires identity and supporting measurements for the actual target host.
Each claimed excerpt and tool name must match a successful runtime result.
For structured JSON results, a selected object of unchanged returned fields is
compared by key and typed value, allowing JSON spacing and subset formatting.
The validator accepts AO's `functions.` display prefix and complete short scalar
outputs; short substrings and excerpts attributed to a different file are rejected.
Agent-written reasoning and `tools_used` summaries cannot supply this proof.

Both `ready` and `no_change` require this validator's positive verdict, a confirmed
assessment, and no final blocking findings. Each original reviewer blocker needs
an explicit resolution naming that finding and supported evidence IDs. An empty
follow-up checklist does not clear blockers. Missing observations must produce
`blocked`, including when no repository edit can yet be justified. The writer
repeats the same evidence validation at the mutation boundary. These checks prove
trace provenance and declared scope, not every semantic inference an agent makes;
independent plan review and human review remain required.

## AAP publication contract

`JT - AO GitOps Action` receives `ao_plan`, `ao_report`, `ao_review`,
`ao_execution_id`, `ao_evidence`, and `ao_evidence_review`. Older workflows
without runtime evidence are rejected before GitHub access.
The standalone playbook retains its local `vars_files`, with empty fallbacks when
ignored files are absent from an AAP SCM checkout. CasC binds repository identity,
API URL, and branch prefix into the stored job template; credentials supply secrets.
The EDA bridge uses the same fallback and explicitly accepts event variables on launch.

The writer supports one to four YAML variable files under
`group_vars/<group>/*.yml` or `host_vars/<target>/*.yml`. This is intentionally a
RHEL desired-state variables workflow. Tasks, roles, documentation, arbitrary root
files, nested paths, host filesystem paths, and other hosts' overlays are rejected.
New files are supported only for a justified overlay under the diagnosed host;
the agent must verify absence and the existing variable loader's consumption.
Changing the reconciliation implementation requires a separate reviewed change.

Each planned file contains:

- `path`, complete `content`, and `expected_sha` from the inspected Git blob.
- `expected_sha: null` only for a new target-host overlay verified as absent.
- A causal `reason` and `evidence_ids` referring to the final assessment.
- `setting_changes`, with a mapping-key `path` array and exact `before` / `after`
  values for every changed YAML leaf. An absent leaf has `before: null`.

The writer reads all files at `read_commit` before any GitHub mutation. It rejects
stale branch/file SHAs, truncated contents, duplicate YAML keys, aliases, no-op
edits, undeclared changes, removal of existing keys, replacement of mappings, and
loss of existing comments. It also reads the pinned `tasks/prepare_vars.yml` and
rejects changed top-level variables that this loader does not consume through its
existing defaulted-input merge pattern. A different loader requires an explicit
writer-contract extension. A commented example can be retained alongside a new
active declaration. List values are treated as complete leaves; their complete
before/after values must be explicitly reviewed.

After validation, the writer creates one tree based on the inspected commit,
then one commit containing every change. New proposals receive a fresh branch;
PR revisions retain the existing head and require the same open PR/repository/base.
A revision uses a non-force fast-forward ref update, so a concurrent divergent
update is rejected. No per-file commits or writes to the default branch occur.

The PR body is rendered from the final structured assessment, not a model-written
one-line summary. It includes incident context, observations and sources, diagnosis,
reconciliation and scope, exact proposed values, alternatives, pending validation,
limitations, automated-review findings, and the AO execution / inspected commit.
A revision refreshes the body and posts a marked summary comment that EDA ignores.
AAP artifacts expose the resulting PR URL and commit.

These checks validate provenance identifiers, scope, and the exact YAML delta.
They do not prove that a model's host observation is true or that a policy is
approved. Tool results, independent review, and human review remain necessary.
Publication is atomic for the file changes, not a transaction spanning every
GitHub API call: a failure after creating the branch or advancing the ref can
leave the commit published without a completed PR/report update. Inspect AAP
output and GitHub before manually relaunching such a failed publication.

## Apply and live-test

Changes in the local checkout do not alter deployed workflows or the AAP project.
Make the revised platform files available to the project's SCM source using your
normal review process before running its AAP job; local uncommitted files are not
available to a remote AAP project sync. The workflow and writer contracts must be
updated together.

From this platform repository, apply AO and AAP configuration together:

```bash
ansible-playbook playbooks/casc.yml --vault-id @prompt
```

### Test the published platform revision

When the supporting services and integrations are already configured, do not
rerun VM provisioning. CasC refreshes the managed MCP SSH aliases from the current
VM addresses; it does not redeploy the server. From the updated platform checkout:

```bash
ansible-playbook playbooks/casc.yml --syntax-check
# Use --vault-id @prompt only when vault.yml is encrypted.
ansible-playbook playbooks/casc.yml
```

The importer updates workflows with matching names in place. Deleting them is
optional and removes their execution/version history. Export them first if you
want a clean recreation. Delete only `rhel-gitops-incident-investigation` and
`rhel-gitops-pr-review`; retain their project, integrations, and credentials.

Because this revision adds a new playbook, sync the AAP platform Project to the
new commit before CasC if Controller validates playbook availability during job
template creation.

After CasC completes:

1. Confirm CasC created **JT - AO Evidence Validation** alongside the writer.
   Sync the AAP `rhel-gitops-platform` project (or your configured project name)
   and confirm its SCM revision contains the new writer and filter plugin.
2. Sync the EDA `RHEL GitOps EDA` project if needed and confirm the GitHub rulebook
   activation is enabled. CasC refreshes the bridge's AO service-account credential.
3. Open both imported AO workflows and publish their latest versions. An existing
   published version is not automatically replaced just by saving an update.
4. Create a new issue in the state repository with the `ai-investigate` label and
   the same `web-01` / `net.core.somaxconn` incident wording used for CrewAI.
5. Follow EDA to `JT - AO Event Bridge`, the AO investigation, and then
   `JT - AO Evidence Validation`, then `JT - AO GitOps Action` only if the
   evidence is verified, the assessment is ready, and independent review approves.
   No-change and blocked outcomes intentionally do not create a PR.
6. Inspect the complete PR report and effective YAML change. Add a scoped revision
   request to that PR and verify the feedback workflow updates the same branch.
   Keep the PR unmerged until you are ready to run normal reconciliation.

For a local-only writer check, use the tests below. They run the actual playbook
against a loopback-only fake GitHub API with dummy credentials. No deployment,
managed-host changes, real GitHub writes, or live model requests are involved.

```bash
python3.13 -m venv /tmp/ao-harness-check
/tmp/ao-harness-check/bin/pip install -r tests/requirements.txt
PATH=/tmp/ao-harness-check/bin:$PATH /tmp/ao-harness-check/bin/pytest -q tests/
```

When AO's source exists under the parent workbench's `references/syntara`, the
workflow tests additionally validate importer substitutions against AO's schemas
and resolve routing expressions using AO's actual evaluator. Without that optional
checkout, those checks are skipped; writer tests and local graph/contract checks
still run. With Node 24+ available, an additional check uses AO's actual visual
expression parser and serializer to verify saved conditions retain their meaning,
and its frontend variable-reference validator to check every workflow parameter.

Mock only the trigger event. Pinned agent outputs are not fresh runtime evidence.

For live validation, use the same incident wording and repository revision as the
CrewAI comparison. Inspect every agent's final output and actual tool arguments /
results. Check these outcomes:

1. A justified incident produces an effective variable change and a complete PR,
   with measured runtime state distinguished from the desired policy.
2. A missing diagnostic or ambiguous policy produces blocked with specific gaps.
3. Runtime drift with correct declared intent produces no_change when a Git edit
   is unnecessary; the harness does not launch reconciliation itself.
4. A scoped maintainer revision changes the existing PR branch and refreshes the
   explanation. An approval or question alone produces no file changes.
5. A deliberately stale file, rejected review, or invalid second file produces no
   GitHub mutations during preflight. An unrelated README replacement is rejected.
6. Repeated new incidents create separate branches; review comments do not trigger
   recursive EDA runs.

Local checks do not establish live success. Record the results in `docs/testing.md`
only after the maintainer confirms the testing-log update.

## Runtime findings from the comparison incident

On 2026-11-26, AO 1.3.0 accepted isolated tool calls with and without response
schemas. GitHub `get_commit` returned the actual repository commit. The earlier
incident had made zero tool calls and repeated fabricated host/policy claims.
The harness now makes calls mandatory and rejects unsupported outcomes.

Live diagnostics also found stale Linux MCP SSH aliases after VM address changes;
Linux MCP authorized the calls but SSH timed out against the old addresses.
The aliases were refreshed from live VMIs, and the same repair is now part of CasC.
A subsequent AO call identified `web-01-gitops`, and the updated bounded host
prompt measured the actual somaxconn value `65535` through Linux MCP.
A separate isolated workflow-node test exposed an AO/LangGraph error:
`Type is not msgpack serializable: ToolMessage`. Keep this as an AO runtime issue
if it recurs; do not treat failed tool handling as a measured incident result.
The platform demo does not patch AO's installed Python packages or global prompt.
