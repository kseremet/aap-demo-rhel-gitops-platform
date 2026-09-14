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
4. Configures `ai-01` with the AI Python environment and Linux MCP runtime.
5. Applies AAP Configuration as Code.

The AI control VM is excluded from the AAP `RHEL Fleet` inventory. It is
available locally under the generated `control_plane` inventory group and is
not targeted by the state repository's `site.yml` reconciliation.

## AI control VM configuration

`playbooks/setup/04_configure_ai_vm.yml` configures the fifth VM. It installs
Python 3.12, creates `/home/cloud-user/.venv`, and installs:

- `linux-mcp-server==1.4.1`
- `fastmcp==2.14.5`
- `mcp`
- CrewAI with OpenAI-compatible provider support
- FastAPI, Uvicorn, HTTPX, OpenAI, Requests, and dotenv support

The MCP server is installed on the control VM only. It uses the generated
runtime SSH key to connect to the RHEL targets as `cloud-user`. The generated
`~/.ssh/config` provides aliases matching the stable inventory names.

The server is prepared for stdio use: the future CrewAI client will start the
local MCP process and pass a target alias such as `web-02` in each tool call.

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
demo_ai_llm_model: gpt-5.6-luna
demo_ai_llm_base_url: https://api.openai.com/v1
```

For DeepSeek, use its OpenAI-compatible endpoint:

```yaml
demo_ai_llm_provider: deepseek
demo_ai_llm_model: deepseek-chat
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
set -a
source /home/cloud-user/agentic-aiops/.env
set +a
/home/cloud-user/agentic-aiops/incident_agent.py \
  "Investigate web-01 because net.core.somaxconn appears inconsistent with the web-server policy. Determine the root cause and propose a GitOps remediation pull request. Do not modify the host directly."
```

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

Set these values before applying CasC:

```yaml
# encrypted vault.yml
vault_eda_controller_password: CHANGE_ME
vault_github_webhook_secret: CHANGE_ME
```

The EDA credential uses the AAP controller username and password schema needed
by `run_job_template`; the existing `vault_aap_token` is used for CasC
authentication and is not silently reused as a password. After CasC creates the
`GitHub AI Incident Issues` rulebook activation, configure a GitHub webhook for
the state repository using the activation's webhook URL, the shared secret,
and the Issues event. Create an issue with the `ai-investigate` label to test
the flow.

## Teardown

The existing cleanup playbook removes all VMs described in `vm_specs.yml`,
including the AI control VM:

```bash
ansible-playbook playbooks/cleanup.yml \
  --vault-id @prompt \
  -e demo_aap_cleanup_confirm=true \
  -e demo_destroy_confirm=true
```
