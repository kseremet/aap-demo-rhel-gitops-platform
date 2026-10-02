# Testing log

Tracks testing progress for this demo. For procedural verification steps, see [setup.md](setup.md).

**Status values:** `Not tested` · `Pass` · `Partial` · `Fail` · `Blocked`

## Status summary

### Setup playbooks

| Component | Status | Last tested | Notes |
|---|---|---|---|
| AI control VM configuration (`setup/04_configure_ai_vm.yml`) | Not tested | — | Ansible-managed container deployment and MCP client selection are pending validation. |

### Roles

| Component | Status | Last tested | Notes |
|---|---|---|---|
| AI control (`ai_control`) | Not tested | — | Containerized MCP resources and local/container mode selection are pending validation. |

## Open issues

- The user confirmed a successful manual MCP-over-HTTP tool call to `web-01`; the Ansible-managed deployment and CrewAI integration have not yet been run end to end.
