# GitHub MCP text compatibility adapter

The demo can run on the installed AO image without the optional AO source patch.
GitHub MCP returns file bodies as embedded MCP resources. The installed AO MCP
adapter hides these bodies from model text and leaves a typed URI in a tool
artifact; AO then fails to checkpoint that artifact. Prompt changes and response
schemas cannot change this transport behavior. See the exact reproduction and
versions in [the maintainer patch notes](../patches/automation-orchestrator/README.md).

The compatibility adapter converts embedded text resources into ordinary MCP
text blocks before AO receives them. Both the URI and complete file body remain
visible to the model and its runtime evidence trace. JSON-RPC errors, tool error
flags, structured output, and metadata are retained. Binary resources fail
explicitly rather than being presented as successful text evidence.

```text
AO GitHub integration
  -> github-mcp-server.<namespace>.svc:8083/mcp
  -> Python text adapter in the existing GitHub MCP pod
  -> 127.0.0.1:<original GitHub MCP container port>/mcp
  -> GitHub
```

The original Service port remains available. The adapter uses the same hostname
already allowed by AO, so no AO source, image, operator, or allowlist change is
needed. Its management/execution credential is the existing GitHub credential.
The incoming bearer header goes only to the fixed local GitHub MCP upstream;
the adapter stores no credential and logs no headers or request bodies. Linux
MCP continues to use its separate dummy UI bearer credential.

Only `get_commit`, `get_file_contents`, `pull_request_read`, `search_code`, and
`list_commits` are exposed. The AAP writer still performs all publication. The
adapter supports the upstream's stateless Streamable HTTP requests, accepting
JSON or SSE results and returning JSON. Server push and unrelated MCP methods
are intentionally unavailable. Requests are capped at 1 MiB and responses at
4 MiB; the upstream request timeout is 20 seconds.

## Apply

From the repository root, use the existing Python environment with the project's
requirements and collections installed. The normal combined configuration run
now creates or updates the sidecar before updating AO's GitHub integration:

```bash
ansible-playbook playbooks/casc.yml \
  -e ansible_python_interpreter="$(command -v python)"
```

For an encrypted Vault, add `--vault-id @prompt`. Keep
`demo_ao_github_mcp_url` set to the **original** MCP URL on port `8082`; CasC
computes the adapter URL from that hostname and
`demo_ao_github_text_adapter_port` (default `8083`). Do not set the original URL
to the compatibility port. Compatibility mode defaults to enabled.

To configure or refresh only the Kubernetes sidecar, without rotating AO's EDA
service-account credentials or importing workflows:

```bash
ansible-playbook playbooks/setup/05_configure_github_mcp_text_adapter.yml \
  -e ansible_python_interpreter="$(command -v python)"
```

That standalone playbook does **not** change AO's integration URL. Use combined
CasC for initial configuration. A narrow API update already pointed the current
live GitHub integration at `http://github-mcp-server.koray.svc:8083/mcp` and its
validation and tool refresh succeeded. Existing workflow integration/tool UUIDs
were preserved, so the same workflows use this endpoint.

The setup needs Kubernetes API access to the existing GitHub MCP Deployment and
Service in `openshift_vm_namespace`. It creates a source-only ConfigMap, adds a
Python sidecar and Service port, and waits for rollout readiness. It pins the
upstream GitHub MCP container to its already-running image digest, preserving
its arguments, environment and mounts. It does not upgrade the GitHub MCP server.
The Python image defaults to public `registry.access.redhat.com/ubi9/python-312:latest`;
set `demo_ao_github_text_adapter_image` to a tested digest if desired.

CasC also declares **JT - GitHub MCP Text Adapter Setup**, its **Cleanup**
counterpart, and **WF - GitHub MCP Text Adapter Setup**. The native Kubernetes
bearer credential is injected as environment variables. Set
`demo_mcp_setup_execution_environment` to an EE containing `kubernetes.core` and
the Kubernetes Python SDK (the project's `context/` declares both). These JTs
operate on the adapter only; run the AO demo after setup and perform confirmed
cleanup separately after restoring the original integration endpoint.

## Verify and test

```bash
oc get pods -n koray -l app=github-mcp-server
oc get service github-mcp-server -n koray
```

Expect both `github-mcp-server` and `ao-text-adapter` containers ready and Service
ports `8082` and `8083`. In AO, validate the existing GitHub integration. Its
five read-only tools should be available. Run the incident workflow with the
[mock-event instructions](ao-harness.md#apply-and-live-test) after publishing any new draft.
Do not pin old agent outputs: they are not fresh runtime evidence.

On 2026-11-26 the isolated `scope_request` test completed, including four real
GitHub calls. A file read's tool output contained the complete policy YAML and
AO checkpointed successfully. No downstream investigation, AAP job, host change,
or PR publication was launched by that test. Full incident and PR feedback runs
remain maintainer validation steps.

## Roll back

Set `demo_ao_github_text_adapter_enabled: false` in local demo variables and run
combined CasC first. This restores AO's original GitHub MCP endpoint. On the
unpatched AO version, embedded file reads will again encounter the original bug.
Then remove only the compatibility resources:

```bash
ansible-playbook playbooks/cleanup/04_remove_github_mcp_text_adapter.yml \
  -e ansible_python_interpreter="$(command -v python)" \
  -e demo_github_text_adapter_remove_confirm=true
```

Cleanup retains the original GitHub MCP Deployment, Service, credentials, and
original port. The upstream image stays pinned to its current digest. The
long-term AO source patch remains available for the AO maintainer; installing
it is not required for this workaround.
