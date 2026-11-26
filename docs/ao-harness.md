# Automation Orchestrator investigation harness

Both workflows use the same three-agent harness on the installed AO version:

```text
Event or maintainer feedback
  -> AAP: pinned repository snapshot and read-only target facts
  -> Agent 1: investigate cause, measurements and durable alternatives
  -> Agent 2: propose exact desired-state edits
  -> AAP: bind evidence and compute exact typed YAML changes
  -> Agent 3: independently review diagnosis, scope and exact edits
  -> AAP: deterministic evidence validation
       ready + approved -> AAP writer -> PR for human review
       verified no_change -> explanation
       otherwise -> specific blocked report
```

There is no diagnostic loop or separate model step for each report section.
An agent failure stops the run. The writer has automatic retries disabled.
AO response schemas define structure; prompts describe concise role requirements.
The configured model remains `demo_ao_llm_model` (the provider model identifier,
for example `gpt-4.1`). No AO source or image modification is required.

## Facts, proposals, and approval

The harness distinguishes measured current state, active declarations, and a
recommended future state. A maintainer need not have already approved that future
state before the workflow can **propose** it. For example, an observed somaxconn
value can be proposed as an explicit host override if the diagnosis explains why
keeping it is preferable to reconciliation back to the baseline. The PR clearly
states its assumptions and that human approval is pending. It never merges or
applies its own proposal.

The shared prompts cover daily events rather than a fixed somaxconn recipe:
configuration drift, service faults, filesystem pressure, and resource capacity.
Agents should consider recurrence, retention, leaks, growth, safer alternatives,
and the narrowest justified scope. They may propose only variables consumed by
the existing desired-state repository. An unsupported change needs a separate
implementation; the workflow must explain the limitation rather than invent a
variable or execute a command on a managed host.

For LV expansion, the AAP foundation measures filesystem usage, mounted device,
LV/VG identity and VG free capacity. The validator rejects shrinking, missing or
inconsistent mappings, and growth beyond measured headroom (with a rounding
reserve). The writer binds the proposed size to the measured LV through the
repository's baseline/group/host storage merge and permits target-host storage
scope only. An LV proposal therefore needs an existing managed volume and backing
capacity; it cannot assume that an underlying virtual disk will expand itself.
A disk-pressure event alone does not establish that expansion is the best remedy.

## Read-only foundation and strict evidence gates

**JT - AO Collect Evidence** resolves one real inventory host and pins the base
commit, or the original open PR head for feedback. It reads the full Git tree and
all supported baseline/group declarations, target overlays, merge tasks,
entrypoint, and inventory files. Blob hashes verify complete contents; truncated
or oversized snapshots fail closed. Vault/secret files are excluded. Collection
is limited to 64 files and 128 KiB of source text, not silent truncation.

The same job runs Ansible setup on the target with privilege escalation to read
identity, device, filesystem, and LVM facts. It does not change configuration.
Only selected facts are returned; environment variables and SSH credentials are
not included. Up to eight kernel keys explicitly named by an event are also read
through bounded `/proc/sys` slurps. No sysctl command or write is executed. This
supplies actual measurements without relying on a model's tool choice. Unavailable
reads remain explicit gaps; the investigator may attempt a focused MCP read. The investigator uses read-only Linux/GitHub MCP tools for the
specific missing measurements, with a prompt budget of 12 calls. The reviewer
has 8 calls for focused challenge. These are reasoning budgets, not engine
resource quotas. Neither role has write tools.

A fixed helper creates short case-local evidence IDs (`E01`, `E02`, ...) from
successful collection and runtime tool results. Code binds each ID to an immutable
record; the model does not have to copy long hashes. The proposer selects IDs; code supplies exact source paths, excerpts,
repository identity, blob SHAs, and PR identity. Agents cannot supply invented
citations. AAP still checks successful paired calls/results, pinned revision,
target identity, exact excerpts, host and repository support, and unresolved
blockers. The writer repeats validation immediately before publication.

Observed-state adoption also requires the exact selected host measurement and
value. Proposal rationale and assumptions are mandatory. Missing diagnostic facts
remain blocking; unknown historical provenance or future human approval alone do
not. Independent approval with an empty blocker list is required for publication.
A no-change decision also requires verified evidence. Existing legacy policy
contracts remain accepted for already published workflows.

Monitoring payloads should name `target_host`; vague or ambiguous targets fail
before any investigation. For existing PR feedback the helper preserves the
original repository, PR number and head. New PR reports include a small target
context marker to make subsequent feedback unambiguous.

## AAP publication contract

`JT - AO Prepare Proposal` parses the author's complete file contents and computes
exact typed YAML leaf changes against the pinned snapshot. It has no SSH/GitHub
credentials, cannot approve or publish, and returns explicit blockers for invalid
files, no-op edits, lost comments or unsupported variables. The independent reviewer
sees these exact deltas before approval; the model does not calculate them.

`JT - AO GitOps Action` receives a lossless `ao_payload_b64` package containing
`ao_plan`, `ao_report`, `ao_review`, `ao_execution_id`, `ao_evidence`, and
`ao_evidence_review`. Fixed decoding marks nested strings as literal data, so
Jinja present in a repository or model response is preserved rather than executed
by Ansible. The preparation and evidence jobs use the same transport. Legacy direct
fields remain supported for existing offline callers. Older workflows
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
Each citation includes a labeled preview of the matched tool output; complete
matched excerpts remain in AAP artifacts when a preview is shortened.
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

Apply the platform revision, adapter helper, AAP template and AO drafts together.
Do not delete the existing AO workflows; CasC updates their drafts in place.
From the demo root with your configured Python environment and dependencies:

```bash
ansible-playbook playbooks/casc.yml
```

Use `--vault-id @prompt` when your vault is encrypted. The platform AAP project
must sync to the pushed revision before creating **JT - AO Collect Evidence** and
**JT - AO Prepare Proposal**.
That template uses the fleet inventory, machine credential and GitHub API Access.
Combined CasC reuses the verified running VMI addresses for both MCP and AAP
SSH inventory, while preserving local generated files and unrelated host variables.
Both the evidence validator and writer must use this same platform revision.
Refresh the AO editor and publish each updated draft before running mock data.

A generic drift event needs no pre-approved policy requirement. Reconciliation
back to an already-declared state can correctly return `no_change` (no repo edit):

```json
{
  "incident_id": "demo-web-01-somaxconn",
  "target_host": "web-01",
  "description": "Investigate net.core.somaxconn drift on web-01. Compare the running value with active repository declarations and propose a durable resolution for maintainer review."
}
```

To exercise a proposed observed-value adoption, make the candidate-review intent
explicit without claiming that the future policy has already been approved:

```json
{
  "incident_id": "demo-observed-value-proposal",
  "target_host": "web-01",
  "description": "Prepare a host-scoped candidate PR for maintainer review to preserve the verified observed net.core.somaxconn value if technically feasible. Future policy approval remains pending. Compare restoring the baseline as an alternative."
}
```

A capacity event uses the same workflow and schema, for example:

```json
{
  "incident_id": "demo-web-01-data-pressure",
  "target_host": "web-01",
  "description": "Monitoring reports /data is running out of space. Verify current utilization and causes, evaluate durable remedies, and propose an appropriate desired-state change if it is feasible."
}
```

Use actual monitoring observations for a live demonstration. A synthetic warning
against a healthy filesystem may correctly produce no change. Review the collected
baseline, measurements, recommendation, alternatives and independent review; then
check the AAP validation artifacts and resulting PR. Confirm that no host changed
until a human merges and the existing reconciliation automation runs. Test PR
feedback against the created PR separately. Local regressions do not establish
end-to-end live success; the testing log remains Partial until that is confirmed.

## Compact handoffs and the invocation API limit

AO limits initial agent prompts to 10,000 characters. Native HTTP nodes call the
existing GitHub MCP text adapter's fixed preparation functions. Large contexts
remain complete in opaque, short-lived MCP-readable packets; agents call
`read_handoff` to retrieve them. No evidence or full file contents are silently
truncated. Raw successful results also reach the validator outside the model
prompt. The internal helper accepts bounded JSON, not executable code, and uses
no host or controller credentials. Packet IDs grant temporary read access, so
keep them within the demo environment. Packets are memory-only, expire after one
hour, and require the adapter's existing single replica.

CasC preserves the operator's native HTTP allowlist and adds the adapter hostname.
The Linux MCP integration retains its dedicated non-secret placeholder bearer
credential for the AO connection UI; it never receives the GitHub token.
GitHub MCP keeps its separate credential. See
[GitHub MCP compatibility](github-mcp-text-adapter.md) for the text adapter.

### Optional script-node configuration

The harness now uses native HTTP preparation and **does not require script
nodes**. The earlier administrator-approved script setting remains available
as an explicit opt-in CasC option for other demo use. AO labels it Developer
Preview: it grants users with workflow-create and execution-run permissions
Python/Bash execution on workers and access to their environment variables.

To disable that earlier setting through CasC, use:

```yaml
demo_ao_manage_script_nodes: true
demo_ao_script_nodes_enabled: false
```

Then run `ansible-playbook playbooks/casc.yml`. Other installations can leave
`demo_ao_manage_script_nodes: false` (the default); HTTP preparation works with
script execution disabled. `demo_ao_namespace` and `demo_ao_resource_name`
identify the operator resource for configuring precise native HTTP access.
