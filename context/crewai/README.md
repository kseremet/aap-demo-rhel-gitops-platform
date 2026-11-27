![Status](https://img.shields.io/badge/Status-WIP-yellow)

# CrewAI execution environment

Build this dependency-only image on **native x86_64 Linux** with Podman and
Ansible Builder 3.1.1. The main platform EE specification is unchanged.
CrewAI runs in `/opt/crewai`, independently of the supported base image's
Ansible/Runner Python packages. Direct dependencies match the tested AI VM.

No API tokens, `.env`, vault, SSH key, repository checkout or token-bearing
`ansible.cfg` enters the image. The EE jobs use only `ansible.builtin`, so no
Automation Hub collection credentials are needed during the build.

## Build and push on the Linux machine

Copy this directory to the Linux host, preserving `context/crewai/` relative to
your build working directory. A build archive may be used while these files are
still uncommitted. It contains only this directory, not the rest of the project.

Run from the directory containing `context/`:

```bash
uname -m  # must print x86_64
python3 -m venv /tmp/crewai-ee-builder
source /tmp/crewai-ee-builder/bin/activate
pip install 'ansible-builder==3.1.1'

# Use your Red Hat registry credentials for the supported base image.
podman login registry.redhat.io

# Replace YOUR_ORG with your registry namespace; use a new tag for each build.
export EE_IMAGE=quay.io/YOUR_ORG/ee-rhel-gitops-crewai:1.15.17-1

ansible-builder build \
  --file context/crewai/execution-environment.yml \
  --context context/crewai/context \
  --container-runtime podman \
  --extra-build-cli-args='--platform=linux/amd64' \
  --tag "$EE_IMAGE"
```

The build runs an import/architecture/SQLite check. Then verify the normal EE
commands and a nonroot CrewAI execution before pushing:

```bash
podman image inspect "$EE_IMAGE" --format '{{.Architecture}}'  # amd64
podman run --rm "$EE_IMAGE" ansible --version
podman run --rm "$EE_IMAGE" ansible-runner --version
podman run --rm --user 100123:0 \
  -e HOME=/tmp -e XDG_CACHE_HOME=/tmp/.cache \
  --tmpfs /tmp:rw,size=512m \
  "$EE_IMAGE" /opt/crewai/bin/python /opt/crewai-smoke.py

podman login quay.io
podman push --digestfile crewai-ee.digest "$EE_IMAGE"
cat crewai-ee.digest
```

For a trusted demo PAH registry with a self-signed certificate, substitute its
hostname and, if necessary, use `podman login <PAH-HOST> --tls-verify=false` and
`podman push "$EE_IMAGE" --tls-verify=false`. Match the Controller registry
credential's certificate validation setting to that registry.

If the build fails, share the failing step and dependency error. Do not remove
version pins or the SQLite check to make an unverified image pass. The Mac can
validate/generate this specification without running a container:

```bash
ansible-builder create \
  --file context/crewai/execution-environment.yml \
  --context /tmp/crewai-ee-generated \
  --output-filename Containerfile
```

If `systemd-python` fails at `RUN /output/scripts/assemble` with missing
`Python.h`, use the updated specification and regenerate the build context with
`ansible-builder build`. The supported base includes collections whose native
dependencies need Python 3.12 and system-library development headers in
`prepend_builder`. These build prerequisites are installed in that stage, before
`assemble`; installing them only in the final stage cannot fix this error.
Rerunning `podman build` against an older generated Containerfile will retain the
error.

## Cutover after the image is available

**Current demo state: EE cutover applied and read-only verification passed.**
The user built and smoke-tested `quay.io/koray/ee-rhel-gitops-crewai:1.15.17-1`.
Local CasC pins digest
`sha256:03e015c98fcc8301d18cd78f60134d9cc9c053580486e102feae9515c169f686`.
Both primary JTs use this image; the independent VM fallbacks remain available.
A real incident/feedback cycle through the EE is still pending user testing.
Fresh configurations default to VM mode until an available image is selected.

The planned local configuration is:

```yaml
# group_vars/all/demo_variables.yml
demo_ai_runtime: ee
demo_execution_environment_name: RHEL GitOps CrewAI EE
demo_execution_environment_image: quay.io/YOUR_ORG/ee-rhel-gitops-crewai@sha256:YOUR_DIGEST
demo_ee_registry_auth: false
```

For a private registry, set `demo_ee_registry_auth: true`, set
`hub_registry_host` to the actual registry hostname, and provide
`vault_hub_registry_username` and `vault_hub_registry_password` in encrypted
`vault.yml`. The existing model and GitHub tokens become an AAP custom credential;
they are injected as environment variables when each job starts.

Before switching, the updated project content must be available in AAP's synced
SCM revision. Apply `ansible-playbook playbooks/casc.yml --vault-id @prompt` from
the platform repository root after image and project availability are verified.
CasC registers the EE, API-token credential type/credential and optional registry
credential, then switches both existing AI JTs to the new localhost playbooks.
The existing EDA routes keep their JT names.

CasC also provides `JT - AI EE Verification`. It uses the same workspace and
credentials, checks a fresh checkout and authenticated GitHub access, reads
web-01's OS release through the external MCP, imports both entrypoints and asks
the configured model for READY without tools. It publishes no PR and makes no
host changes. Check its Phoenix trace after running it.

The shared model selection is `demo_ai_llm_model` (default `gpt-6-luna`). CasC
stores it on both primary JTs and reconciles only `AI_LLM_MODEL` in the VM's
existing `.env`; credentials, provider settings and external MCP remain intact.
A native CrewAI banner saying tracing is disabled
refers to hosted AMP tracing; the separate OpenInference/Phoenix export remains
enabled and was verified from AAP.

Each EE job renders the shared agent templates and tracing helper from the AAP
project into a private temporary directory, clones current state, runs the crew,
and removes the workspace even after failure. Linux MCP remains external and
Phoenix remains the trace destination. Script-only changes require a project
sync; dependency changes require a new image build.

## VM fallback

CasC creates two independently runnable alternatives during EE cutover:

- `JT - AI Incident Investigation (VM fallback)` — pass `ai_incident_prompt`.
- `JT - AI PR Review (VM fallback)` — pass `pr_review_event`.

They retain the original playbooks, AI inventory and SSH credential and use
`demo_ai_vm_execution_environment` (default `Default execution environment`),
so they do not depend on pulling the new CrewAI EE. No VM or installed runtime
is removed. To restore the primary JTs to VM execution, set `demo_ai_runtime: vm`
and reapply CasC; the explicit VM EE assignment replaces the CrewAI image.
