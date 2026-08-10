![Status](https://img.shields.io/badge/Status-Scaffold-yellow)
![Red Hat AAP](https://img.shields.io/badge/AAP-2.6%2B-red)
![OpenShift Virtualization](https://img.shields.io/badge/OpenShift%20Virtualization-KubeVirt-blue)

# RHEL GitOps Platform Bootstrap

## Introduction

This repository owns the platform side of the event demo **From Admin to
Maintainer: Agentic Ops and GitOps for RHEL**. It creates the disposable RHEL
lab, exposes the management inventory to administrators, and bootstraps AAP.

It is deliberately separate from the Git repository that contains RHEL desired
state. This prevents VM recreation, generated IP addresses, SSH key generation,
and AAP bootstrap changes from being mixed with human GitOps configuration
commits.

## Repository Boundary

| Concern | Owner |
|---|---|
| KubeVirt VM lifecycle | This repository |
| SSH key generation and AAP Machine Credential | This repository |
| Discovered management IP inventory | This repository and AAP inventory |
| AAP organizations, projects, credentials, and job templates | This repository |
| RHEL System Role desired state | `aap-demo-rhel-gitops-state` |

## Intended Flow

```text
Generate key -> Provision VMs -> Discover IPs -> Create AAP inventory
     -> Create AAP Machine Credential -> Configure AAP
     -> Reconcile rhel-gitops-state
```

## Quick Start

This split is currently a scaffold. The commands below are the intended
implementation and event order; setup and CasC playbooks are being added before
the first end-to-end run.

The platform repository is run before the state repository. Use a local clone
for initial lab creation, or run the same playbooks as restricted AAP bootstrap
job templates.

### 1. Prepare local configuration

```bash
cd aap-demo-rhel-gitops-platform
cp ansible.cfg.example ansible.cfg
cp group_vars/all/demo_variables.yml.example group_vars/all/demo_variables.yml
cp vault.yml.example vault.yml
```

Create and activate the project-local Python environment before running
Ansible. This avoids modifying Homebrew's externally managed Python:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
ansible-vault encrypt vault.yml
```

Edit `group_vars/all/demo_variables.yml` and set the AAP URL, state repository
URL, OpenShift API URL, namespace, and VM environment values. Edit `vault.yml`
with the AAP and OpenShift secrets. Do not put the SSH private key in Git.

### 2. Install collections

```bash
ansible-galaxy collection install \
  -r collections/requirements.yml \
  -p collections
```

The collection installation provides Ansible modules; `requirements.txt`
provides the Python SDKs those modules import. Ansible must use the same Python
interpreter where these packages are installed. Check it with:

```bash
ansible localhost -m ansible.builtin.setup -a 'filter=ansible_python*' -i environments/lab/inventory.yml
```

For AAP execution, build or select an Execution Environment using
`context/execution-environment.yml`; installing collections alone does not add
the Kubernetes Python SDK.

### 3. Create the lab and bootstrap AAP

For the shortest path, run the ordered bootstrap. It keeps the generated private
key available through the entire local or AAP job execution, then imports it into
the AAP Machine Credential before the job ends.

```bash
ansible-playbook playbooks/bootstrap.yml --vault-id @prompt
```

The key is created under ignored `runtime/`. Its public half is injected into
the VMs. The private half is used to create the AAP Machine Credential and is
never committed. In AAP, run this as one restricted bootstrap job rather than as
separate workflow nodes so the temporary key remains available.

For troubleshooting or demonstrations of each platform phase, run the ordered
playbooks separately:

```bash
ansible-playbook playbooks/setup/01_generate_keys.yml
ansible-playbook playbooks/setup/02_provision_vms.yml --vault-id @prompt
ansible-playbook playbooks/setup/03_discover_inventory.yml --vault-id @prompt
ansible-playbook playbooks/aap_config.yml --vault-id @prompt
```

When running the phases separately in AAP, provide
`vault_demo_ssh_private_key` as an encrypted Vault value because AAP job
workspaces are temporary.

After discovery, inspect the addresses that will be shown to the audience:

```bash
ansible-inventory \
  -i environments/lab/inventory.yml \
  --graph
```

### 4. Inspect the generated inventory

```bash
ansible-inventory \
  -i environments/lab/inventory.yml \
  --graph
```

The discovery playbook also writes `group_vars/all/generated_inventory.yml`,
which is consumed by CasC to create AAP hosts and groups.

### 5. Start reconciliation

Run locally while developing, or launch the reconciliation job template in AAP:

```bash
ansible-playbook \
  -i environments/lab/inventory.yml \
  ../aap-demo-rhel-gitops-state/site.yml
```

The final event demo should use AAP for this step so the audience can see the
Project sync and job launch after a Git commit.

### 6. Reset the lab

Destroying VMs is a platform operation and must be deliberate:

```bash
ansible-playbook playbooks/cleanup/01_remove_vms.yml --vault-id @prompt
```

Resetting the platform lab must not reset or rewrite the state repository.

## Layout

| Path | Purpose |
|---|---|
| `playbooks/setup/` | Ordered key, VM, and discovery operations |
| `playbooks/cleanup/` | Explicit VM teardown operations |
| `playbooks/aap_config.yml` | AAP Configuration as Code entry point |
| `environments/lab/inventory.yml` | Visible generated lab inventory |
| `group_vars/all/` | Platform and AAP configuration variables |
| `runtime/` | Ignored local runtime material, including private keys |
| `templates/` | KubeVirt and inventory templates |

## Status

The ordered setup, discovery, cleanup, and bootstrap playbooks are now present.
End-to-end execution still requires installing the declared collections and
supplying environment-specific values. The existing
`aap-demo-rhel-gitops` demo remains intact while the split implementation is
built and validated incrementally.
