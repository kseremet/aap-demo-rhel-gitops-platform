# RHEL GitOps Platform Demo

This folder is the platform/bootstrap side of the RHEL GitOps event demo.

## Purpose

Own the disposable lab environment and AAP bootstrap. This repository provisions
RHEL VMs on OpenShift Virtualization, discovers their management addresses,
creates the AAP inventory and Machine Credential, and configures AAP projects and
job templates.

The companion `aap-demo-rhel-gitops-state` folder owns the human-maintained RHEL
desired state. Do not put RHEL System Role desired-state changes here.

## Runtime model

1. Generate an SSH key under `runtime/`.
2. Provision the KubeVirt VMs with the public key.
3. Discover VM management IPs and write the visible lab inventory.
4. Create or update the AAP Machine Credential from the private key.
5. Configure AAP so reconciliation points to the state repository.
6. Run the GitOps reconciliation job.

The private key is runtime state. It must remain ignored and must never be
committed. Local Ansible runs may use the key file; AAP runs persist it only in
the AAP credential created during bootstrap.

## Ownership rules

- VM shape, lifecycle, cloud-init, and OpenShift access belong here.
- Generated lab inventory and management IPs belong here.
- AAP Configuration as Code belongs here.
- RHEL configuration intent belongs in the state repository.
- Do not rewrite the state repository from discovery playbooks.

## Local and AAP operation

Local execution is supported from this repository with the generated inventory.
AAP executes provisioning and bootstrap jobs from this repository, then runs the
reconciliation project sourced from the separate state repository.

## Current status

This is a new scaffold. The existing `../aap-demo-rhel-gitops` demo is preserved
as-is and is not a source of truth for this repository.
