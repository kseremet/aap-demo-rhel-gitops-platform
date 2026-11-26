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

## AAP publication contract

`JT - AO GitOps Action` receives four values: `ao_plan`, `ao_report`, `ao_review`,
and `ao_execution_id`. Older workflows supplying only `ao_plan` are rejected.
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
rerun VM provisioning or redeploy MCP servers. From the updated platform checkout:

```bash
ansible-playbook playbooks/casc.yml --syntax-check
ansible-playbook playbooks/casc.yml --vault-id @prompt
```

The importer updates workflows with matching names in place. Deleting them is
optional and removes their execution/version history. Export them first if you
want a clean recreation. Delete only `rhel-gitops-incident-investigation` and
`rhel-gitops-pr-review`; retain their project, integrations, and credentials.

After CasC completes:

1. Sync the AAP `rhel-gitops-platform` project (or your configured project name)
   and confirm its SCM revision contains the new writer and filter plugin.
2. Sync the EDA `RHEL GitOps EDA` project if needed and confirm the GitHub rulebook
   activation is enabled. CasC refreshes the bridge's AO service-account credential.
3. Open both imported AO workflows and publish their latest versions. An existing
   published version is not automatically replaced just by saving an update.
4. Create a new issue in the state repository with the `ai-investigate` label and
   the same `web-01` / `net.core.somaxconn` incident wording used for CrewAI.
5. Follow EDA to `JT - AO Event Bridge`, the AO investigation, and then
   `JT - AO GitOps Action` if the assessment is ready and independent review approves.
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
expression parser and serializer to verify saved conditions retain their meaning.

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
