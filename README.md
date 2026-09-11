![Status](https://img.shields.io/badge/Status-Ready-green)
![Red Hat AAP](https://img.shields.io/badge/AAP-2.6%2B-red)
![OpenShift Virtualization](https://img.shields.io/badge/OpenShift%20Virtualization-KubeVirt-blue)
![CasC](https://img.shields.io/badge/CasC-infra.aap__configuration-blue)

# RHEL GitOps Platform Bootstrap

## Introduction

This repository owns the platform side of the event demo **From Admin to
Maintainer: Agentic Ops and GitOps for RHEL**. It creates the disposable RHEL
lab on OpenShift Virtualization, exposes the management inventory to
administrators, and bootstraps AAP with Configuration as Code.

It is deliberately separate from the Git repository that contains RHEL desired
state. This prevents VM recreation, generated IP addresses, SSH key generation,
and AAP bootstrap changes from being mixed with human GitOps configuration
commits.

## Prerequisites

| Requirement | Details |
|---|---|
| OpenShift | Cluster with KubeVirt / OpenShift Virtualization |
| RHEL DataSource | `rhel9` in `openshift-virtualization-os-images` (or configure in variables) |
| Multus network | Second NIC network (e.g. `default/localnet-vlan150`) |
| AAP | Instance with admin access and PAT token |
| Python | 3.9+ for the local virtual environment |
| Git | Sibling clone of `aap-demo-rhel-gitops-state` |

## Quick Start

### 1. Clone and configure

```bash
git clone https://github.com/kseremet/aap-demo-rhel-gitops-platform.git
cd aap-demo-rhel-gitops-platform

cp ansible.cfg.example ansible.cfg
cp group_vars/all/demo_variables.yml.example group_vars/all/demo_variables.yml
cp vault.yml.example vault.yml
```

### 2. Create the local Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
ansible-vault encrypt vault.yml
```

### 3. Edit configuration files

Edit `group_vars/all/demo_variables.yml`:
- `demo_state_scm_url` — URL of the state repository
- `demo_platform_scm_url` — URL of this platform repository
- `aap_hostname` — AAP host (without `https://`)
- `openshift_api_url` — OpenShift API URL
- `openshift_vm_namespace` — namespace for the VMs
- `openshift_vm_multus_network_name` — Multus network attachment

Edit `vault.yml`:
- `vault_openshift_token` — OpenShift API token
- `vault_aap_token` — AAP PAT
- `vault_demo_ssh_private_key` — optional, the bootstrap uses the runtime key by default

### 4. Install collections

```bash
ansible-galaxy collection install \
  -r collections/requirements.yml \
  -p collections
```

### 5. Bootstrap the lab

One command creates the SSH key, provisions the RHEL fleet and AI control VM,
discovers management addresses, configures the AI control VM, and configures AAP:

```bash
ansible-playbook playbooks/bootstrap.yml --vault-id @prompt
```

The key is created under ignored `runtime/`. Its public half is injected into
the VMs via cloud-init. The private half is imported into the AAP Machine
Credential and discarded from the job workspace.

### 6. Inspect the result

```bash
ansible-inventory -i environments/lab/inventory.yml --graph
```

The discovery step also writes `environments/lab/discovered_facts.yml` with MAC
addresses and IPs for every VM. This file is used by the pre-demo preparation
step below.

The `ai-01` control VM is intentionally excluded from the AAP `RHEL Fleet`
inventory. It is configured by the platform bootstrap and reserved for the
CrewAI and Linux MCP portions of the demo. The local inventory exposes it under
the separate `control_plane` group.

### 7. Pre-demo preparation

Before the event, populate the state repository's host variable MAC placeholders
with the discovered values:

```bash
ansible-playbook utils/populate_host_vars.yml -i environments/lab/inventory.yml

# Verify no placeholders remain:
grep -r 'CHANGE_ME.*MAC' ../aap-demo-rhel-gitops-state/host_vars/
```

Commit the populated host_vars in the state repository:

```bash
cd ../aap-demo-rhel-gitops-state
git add host_vars && git commit -m "Populate host MAC addresses" && git push
```

## Repository Boundary

| Concern | Owner |
|---|---|
| KubeVirt VM lifecycle | This repository |
| SSH key generation and AAP Machine Credential | This repository |
| Discovered management IP inventory | This repository and AAP inventory |
| AI control VM and its CrewAI/Linux MCP runtime | This repository |
| AAP organizations, projects, credentials, and job templates | This repository |
| Pre-demo MAC population utilities | This repository |
| RHEL System Role desired state | `aap-demo-rhel-gitops-state` |

## Reset the Lab

The master cleanup removes AAP objects, deletes the VMs, restores generated
inventory files to boilerplate, removes the discovered facts snapshot, and
deletes the runtime SSH key:

```bash
ansible-playbook \
  playbooks/cleanup.yml \
  --vault-id @prompt \
  -e demo_aap_cleanup_confirm=true \
  -e demo_destroy_confirm=true
```

Individual stages remain available for troubleshooting:

```bash
ansible-playbook playbooks/cleanup/01_remove_aap.yml --vault-id @prompt -e demo_aap_cleanup_confirm=true
ansible-playbook playbooks/cleanup/02_remove_vms.yml --vault-id @prompt -e demo_destroy_confirm=true
ansible-playbook playbooks/cleanup/03_reset_generated_files.yml
```

Resetting the platform lab does not reset or rewrite the state repository.

## Layout

| Path | Purpose |
|---|---|
| `playbooks/bootstrap.yml` | Single-command lab creation and AAP bootstrap |
| `playbooks/setup/` | Ordered key, VM, and discovery operations |
| `playbooks/cleanup.yml` | Master teardown and generated-file reset entry point |
| `playbooks/cleanup/` | AAP teardown, VM deletion, and local reset stages |
| `playbooks/aap_config.yml` | AAP Configuration as Code entry point |
| `group_vars/all/` | Platform and AAP configuration variables |
| `environments/lab/` | Generated inventory and discovered VM facts |
| `templates/` | KubeVirt VM specs and inventory templates |
| `utils/` | Pre-demo MAC population and uncomment scripts |
| `runtime/` | Ignored local runtime material, including private keys |
| `context/` | Execution Environment definition for AAP |

The bootstrap also runs `playbooks/setup/04_configure_ai_vm.yml` to prepare the
AI control VM and its SSH/MCP runtime.

## Job Templates

After CasC is applied, the following AAP job templates are available:

| Name | Playbook | Purpose |
|---|---|---|
| JT - RHEL GitOps Reconciliation | `site.yml` (state repo) | Reconcile declared RHEL state |
| JT - RHEL GitOps Provision VMs | `playbooks/setup/02_provision_vms.yml` | Create KubeVirt VMs |
| JT - RHEL GitOps Discover Inventory | `playbooks/setup/03_discover_inventory.yml` | Populate inventory from VMI facts |
| JT - RHEL GitOps Cleanup | `playbooks/cleanup.yml` | Full lab teardown |

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `kubernetes.core.k8s` fails with Python import error | Python SDKs not installed in venv | `source .venv/bin/activate && python -m pip install -r requirements.txt` |
| VMI discovery has no `ipAddress` | Guest agent not yet reporting | Discovery waits and retries; re-run if needed |
| Key written under `playbooks/setup/runtime/` | Running playbook from wrong directory | Always run from the repository root |
| `401 Unauthorized` from AAP API | Token variable name mismatch | CasC expects `aap_token`, not `controller_oauthtoken` |
| `aap_hostname` missing scheme in URI check | Hostname stored without `https://` | The cleanup playbook prepends it internally |

## Companion Repository

After the lab is bootstrapped and host MACs are populated, the audience-facing
demo continues in `aap-demo-rhel-gitops-state`. See its README for the live demo
workflow.

## AI Control VM

The platform provisions `ai-01-gitops` as a fifth VM with 2 vCPUs and 4 GiB of
memory. It is a control system, not a RHEL fleet target. The bootstrap installs
Python 3.12, creates `/home/cloud-user/.venv`, installs the Linux MCP server and
CrewAI dependencies, and configures SSH aliases to `web-01`, `web-02`, `db-01`,
and `app-01`.

The Linux MCP server runs locally in stdio mode and uses passwordless SSH to
perform diagnostics on the selected target. No MCP server is installed on the
fleet nodes. The future CrewAI service reads its model settings from
`/home/cloud-user/agentic-aiops/.env`.

Set `demo_ai_llm_provider`, `demo_ai_llm_model`, and `demo_ai_llm_base_url` in
`group_vars/all/demo_variables.yml`. Set `vault_ai_llm_api_key` in `vault.yml`
and encrypt that file before use.
