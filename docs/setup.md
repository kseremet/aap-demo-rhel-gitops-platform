# Setup

## Bootstrap order

Run the complete platform bootstrap from the repository root:

```bash
ansible-playbook playbooks/bootstrap.yml --vault-id @prompt
```

The bootstrap performs these operations in order:

1. Generates the ignored runtime SSH key.
2. Provisions the four RHEL fleet VMs and the `ai-01-gitops` control VM.
3. Discovers all VM management addresses.
4. Configures `ai-01` with the AI Python environment and both Linux MCP runtimes.
5. Applies AAP Configuration as Code.

The AI control VM is excluded from the AAP `RHEL Fleet` inventory. It is
available locally under the generated `control_plane` inventory group and is
not targeted by the state repository's `site.yml` reconciliation.

## AI control VM configuration

`playbooks/setup/04_configure_ai_vm.yml` configures the fifth VM and the
containerized MCP resources in the same OpenShift namespace. It installs Python
3.12, creates `/home/cloud-user/.venv`, and installs:

- `linux-mcp-server==1.4.1`
- `fastmcp==2.14.5`
- `mcp`
- CrewAI with OpenAI-compatible provider support
- FastAPI, Uvicorn, HTTPX, OpenAI, Requests, and dotenv support

The generated private SSH key is imported into the AAP Machine Credential and
copied into an OpenShift Secret for the containerized MCP server. The source key
stays under ignored `runtime/` and must not be committed.

The local MCP server remains installed on the control VM and is available over
stdio. The playbook also deploys `linux-mcp-server` as a one-replica OpenShift
Deployment with an in-namespace ClusterIP Service. Its SSH configuration is
generated from discovered fleet addresses; the private key is stored in an
OpenShift Secret. The container uses `emptyDir` for its log files, while its
console logs are available through the normal container log stream.

By default CrewAI uses the containerized server at
`http://linux-mcp-server:8000/mcp`. To switch to the local stdio server, set
`demo_ai_mcp_mode: local` in `group_vars/all/demo_variables.yml` and rerun the AI
configuration playbook. Set it back to `container` to use the OpenShift Service
again. Both backends use the same SSH aliases and diagnostic tool interface.

The containerized demo policy allows all tools for all hosts to any client that
can reach the Service, using the `cloud-user` SSH identity. SSH host-key
verification is disabled for this demo. Restrict network access and replace
these settings with authenticated, least-privilege policy before using this
pattern beyond a disposable lab.

## Model configuration

Set `rhc_organization` and `rhc_repos_baseline` in
`group_vars/all/demo_variables.yml`. Set `vault_rhc_activation_key` in the
encrypted `vault.yml`. The AI VM is registered with Red Hat before package
installation so BaseOS and AppStream are available.

Copy the examples before running bootstrap:

```bash
cp group_vars/all/demo_variables.yml.example group_vars/all/demo_variables.yml
cp vault.yml.example vault.yml
ansible-vault encrypt vault.yml
```

Set non-secret model values in `group_vars/all/demo_variables.yml`:

```yaml
demo_ai_llm_provider: openai
demo_ai_llm_model: gpt-6-luna
demo_ai_llm_api: responses
demo_ai_llm_base_url: https://api.openai.com/v1
```

For DeepSeek, use its OpenAI-compatible endpoint:

```yaml
demo_ai_llm_provider: deepseek
demo_ai_llm_model: deepseek-chat
demo_ai_llm_api: completions
demo_ai_llm_base_url: https://api.deepseek.com/v1
```

Set the secret in `vault.yml`:

```yaml
vault_ai_llm_api_key: CHANGE_ME
```

The bootstrap writes these values to the AI VM's mode `0600` file:
`/home/cloud-user/agentic-aiops/.env`.

## Run the CrewAI investigation agent

Add `vault_github_token` to the encrypted `vault.yml`, then rerun the AI
configuration playbook so the agent can create a branch and pull request. SSH
to `ai-01` and run:

```bash
/home/cloud-user/agentic-aiops/incident_agent.py \
  "Investigate web-01 because net.core.somaxconn appears inconsistent with the web-server policy. Determine the root cause and propose a GitOps remediation pull request. Do not modify the host directly."
```

The agent loads `/home/cloud-user/agentic-aiops/.env` automatically. You may
still source that file manually when inspecting or overriding runtime values.

The agent uses read-only Linux MCP tools for investigation. It does not modify
managed hosts directly. Its repository write operation creates a GitHub branch
and pull request, which may contain changes to existing state or new roles and
files for human review. The PR body records the original incident, agent
analysis, validation context, and a machine-readable run metadata block so a
future PR-management agent can reconstruct the initial context.

## Reconfigure only the AI VM

After discovery has completed, rerun only the AI configuration playbook:

```bash
ansible-playbook playbooks/setup/04_configure_ai_vm.yml --vault-id @prompt
```

## Launch the AI agent from AAP

After pushing the platform changes to the platform repository, refresh the
discovered inventory and apply AAP Configuration as Code:

```bash
ansible-playbook playbooks/setup/03_discover_inventory.yml --vault-id @prompt
ansible-playbook playbooks/aap_config.yml --vault-id @prompt
```

In Controller, launch `JT - AI Incident Investigation` from the `AI Control`
inventory. Enter the incident description in the survey. The job runs the
already-provisioned agent on `ai-01`; it does not modify managed hosts directly
and returns the generated GitHub pull request in the job output.

## EDA GitHub issue trigger

Phase 2 adds an EDA rulebook that listens for GitHub issue webhooks. An issue
with the `ai-investigate` label launches the same AI incident job template with
the issue title and body as its prompt.

Set the GitHub webhook secret before applying CasC:

```yaml
# encrypted vault.yml
vault_github_webhook_secret: CHANGE_ME
```

The EDA credential uses the AAP Controller OAuth token already stored in
`vault_aap_token`; no additional Controller password is required. After CasC
creates the `GitHub AI Incident Issues` rulebook activation, configure a GitHub
webhook for the state repository using the activation's webhook URL, the shared
secret, and the Issues event. Create an issue with the `ai-investigate` label
to test the flow.

When the rulebook changes, sync the `RHEL GitOps EDA` project in the EDA UI
before applying CasC again. CasC does not force a project sync on every run, so
it will not race with an in-progress manual sync.

## Apply Controller and EDA configuration

```bash
ansible-playbook playbooks/casc.yml --vault-id @prompt
```

This configures the CrewAI job templates and the GitHub EDA activation, deploys
the internal Phoenix dashboard, then deploys both CrewAI scripts from the role
templates to the existing AI VM.
Script deployment validates Python syntax and imports, keeps remote backups,
and preserves CrewAI/OpenAI versions, credentials, state checkout and selected MCP
backend. It reconciles `AI_LLM_MODEL` and `AI_LLM_API` in the VM's `.env` with the shared
`demo_ai_llm_model` and `demo_ai_llm_api` settings, currently `gpt-6-luna` and
`responses`. Initial runtime provisioning
remains in `04_configure_ai_vm.yml`.
Managed AAP host addresses, including
`ai-01`, and existing Linux MCP SSH aliases are refreshed from live VMIs without
rewriting generated local files. The activation is briefly disabled for updates
and enabled after configuration; review webhook deliveries during maintenance
for any events that need redelivery.

Verify the `AI Control` inventory contains the current `ai-01` address and both
CrewAI job templates use that inventory and the machine credential. Check agent
imports, authenticated read-only GitHub access, the configured model, and Linux
MCP tool discovery before a controlled incident-to-PR test. Maintainer feedback
must update that same PR; marked agent comments must not retrigger the rulebook.

For script-only updates through the same CasC entry point, run:

```bash
ansible-playbook playbooks/casc.yml --tags crewai_runtime --skip-tags aap --vault-id @prompt
```

This path resolves the AI VM's live address and does not disable EDA or change
AAP objects. `--skip-tags aap` also skips the dispatch role's mandatory argument
validation tasks. Both incident and feedback agents can list repository directories,
search tracked filenames and content, and read discovered policy files. The
tools exclude Git metadata and paths escaping the checkout, and bound their
output. Review agents use the local base checkout alongside the PR diff.

## Watch CrewAI execution in Phoenix

Phoenix runs as one nonroot pod in the demo namespace with a 2 GiB persistent
volume and SQLite. It uses a ClusterIP Service, with no public Route, external
database, collector, cloud account or license key. This demo endpoint has no
application authentication and is intended for the trusted cluster network;
keep it internal. Its telemetry, external resource fetching and MCP server are
disabled. The server image and tracing instrumentation versions are pinned.

With Telepresence connected, open:

```text
http://phoenix.koray.svc.cluster.local:6006
```

Select the `rhel-gitops` project and start the incident or PR-review JT. Completed
tool and model spans are exported every second while the crew continues running;
the root span finishes at job exit. Refresh the trace list if necessary. AAP
output includes the dashboard URL, project, trace ID and final report, with verbose
CrewAI narration disabled while tracing is enabled. Trace data can include model
prompts, repository content and diagnostic results.

For another namespace, substitute `openshift_vm_namespace` in the URL. Without
Telepresence, use a local port-forward and open `http://localhost:6006`:

```bash
oc -n koray port-forward service/phoenix 6006:6006
```

To deploy only Phoenix and the instrumented scripts without updating AAP/EDA:

```bash
ansible-playbook playbooks/casc.yml --tags phoenix,crewai_runtime --skip-tags aap --vault-id @prompt
```

Full `casc.yml` includes the same deployment. Initial AI VM setup also installs
the helper and pinned tracing packages through the shared role tasks. Tracing
uses a separate `tracing.json`; existing credentials and the external Linux MCP
settings in `.env` are preserved. An exporter failure does not undo a PR or
prevent the crew from running. Set `demo_phoenix_enabled: false` and reapply the
runtime to disable tracing and restore verbose output.

To remove the dashboard, first disable tracing as above, then run:

```bash
ansible-playbook playbooks/cleanup/04_remove_phoenix.yml -e demo_phoenix_cleanup_confirm=true --vault-id @prompt
```

The PVC is retained. Add `-e demo_phoenix_delete_data=true` only to permanently
delete its trace history. The setup and cleanup playbooks affect only Phoenix.

## PR review feedback loop

For the prepared x86_64 CrewAI execution environment, build and cutover
instructions are in [the CrewAI EE guide](../context/crewai/README.md). VM mode
remains active until the image is built, pushed, verified and selected through
CasC. The EE cutover also creates independent VM fallback job templates.

The platform also provides `JT - AI PR Review`. Enable these additional GitHub
webhook events for the existing EDA route:

- Pull requests
- Pull request reviews
- Pull request review comments
- Issue comments

Human review submissions and comments on AI-generated `aiops/` branches launch
the PR review agent. It reads the conversation and existing diff, performs
additional read-only MCP diagnostics when needed, updates the existing PR
branch, and posts a marked summary comment. The rulebook ignores those marked
agent comments to prevent feedback loops.

## Teardown

The existing cleanup playbook removes all VMs described in `vm_specs.yml`,
including the AI control VM:

```bash
ansible-playbook playbooks/cleanup.yml \
  --vault-id @prompt \
  -e demo_aap_cleanup_confirm=true \
  -e demo_destroy_confirm=true
```

### Native tool-call verification

Both agent LLM factories use `demo_ai_llm_api: responses` by default, passed as
`AI_LLM_API` in EE jobs and reconciled in the VM environment by `casc.yml`.
This retains reasoning with native function tools on `gpt-6-luna`, avoiding
Chat Completions retries that disable reasoning. For a provider that only
supports Chat Completions, explicitly select `completions` and a compatible model.
No EE rebuild is needed for this change with the pinned CrewAI 1.15.17 image.

Launch **JT - AI EE Verification** to check the EE. The verifier exercises
both production LLM factories: each model must call a read-only repository tool
and repeat a fresh receipt available only in its result. A plain model reply
cannot pass. The check also verifies authenticated GitHub access and external
Linux MCP access, without publication or remediation tools.

The same verifier is deployed on the VM by CasC. To check the fallback, run as
`cloud-user` on `ai-01`:

```bash
cd /home/cloud-user/agentic-aiops
/home/cloud-user/.venv/bin/python verify_crewai_ee.py
```

In Phoenix, confirm successful Responses spans and repository tool execution for
both checks, with no ChatCompletion errors.
