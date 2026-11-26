# AO MCP embedded-resource compatibility fix

For the demo on an unmodified AO installation, use the
[GitHub MCP text compatibility sidecar](../../docs/github-mcp-text-adapter.md).
The source patch below remains an optional long-term fix for the AO maintainer.

Status: prepared and verified in memory; **not applied to the running AO**.
This is an AO backend source fix, separate from the demo workflows and AAP CasC.

## Reproduced cause

Invocation `4065cd0e-697a-48db-84a7-f59af4bcea44` called GitHub tools and read the
repository commit, directory listing, and a variable file. The installed MCP
adapter converts GitHub file responses into a summary in `ToolMessage.content`
and an `EmbeddedResource` in `ToolMessage.artifact`. That resource contains:

- The actual file body, which is therefore absent from the model-visible content.
- A Pydantic `AnyUrl` URI, which survives Python-mode model dumping and makes
  LangGraph's JsonPlus/msgpack checkpoint serialization fail with
  `Type is not msgpack serializable: ToolMessage`.

A failed read of a missing host overlay occurred in the same invocation, but a
plain error ToolMessage and AO's error handler serialize correctly. An isolated
embedded-resource ToolMessage reproduces the exact failure without a model,
network connection, or application changes.

Verified runtime:

- Backend image: `registry.redhat.io/ansible-automation-platform/automation-orchestrator-backend-rhel9@sha256:e669f85d1cbf10b499d335d4e1aad541410c6832986a5662ef92c73e82c4fc92`
- `langchain-core==1.5.3`, `langchain-mcp-adapters==0.1.14`
- `langgraph==1.2.10`, `langgraph-checkpoint==4.1.1`
- `ormsgpack==1.12.2`, `pydantic==2.13.4`
- Original `mcp_provider.py` SHA256:
  `3da6f5a09b0a6b750cd1c9c9fab85638a722e484460a19518c9fa24bd6a42781`

## Fix

`normalize-mcp-resources.patch` adds `result_normalization.py` to AO's MCP provider
and wraps its asynchronous content-and-artifact tools before `StructuredTool`
constructs a ToolMessage. It:

1. Converts artifact models to JSON-safe data, preserving URI strings and metadata.
2. Adds complete embedded text-resource bodies and their URIs to model-visible
   content. The same text reaches runtime tool-result traces and evidence checks.
3. Preserves binary artifacts without adding blob contents to the model prompt.
4. Leaves tool exceptions, non-artifact results, runtime argument injection,
   response schemas, and graph checkpointing enabled.

Normalizing only after the tool wrapper returns is too late for the original
tool-result streaming event. This fix runs at the coroutine conversion boundary
so model history, tool traces, and checkpoint storage agree.

The platform evidence validator also accepts either MCP revision argument `sha`
or `ref`, requires the exact inspected commit, and rejects conflicting aliases.
That correction is a demo change and does not repair the AO runtime by itself.

## Verification

Nine offline regression checks passed against the exact installed container
libraries in a separate Python process. No application files were changed. Checks
include the original crash, JSON-safe artifact round-trip, complete file text in
model history and streaming events, plain results, binary resources, duplicate
text avoidance, wrapper signature/idempotence, and error propagation.

Run the standalone checks with the AO image's Python and MCP/LangGraph libraries:

```bash
python verify_result_normalization.py
```

The verifier imports the adjacent `result_normalization.py`. Both files are
self-contained and use no model calls, MCP connections, credentials, or database.

## Applying and rolling back

Apply the patch to a writable AO source checkout and rebuild the backend image
through the AO maintainer's image-build process. For example, from that checkout:

```bash
git apply --check /path/to/platform/patches/automation-orchestrator/normalize-mcp-resources.patch
git apply /path/to/platform/patches/automation-orchestrator/normalize-mcp-resources.patch
```

Do not edit the workbench's read-only `references/syntara` checkout or hot-edit
running pods. The installed AO is operator-managed. Its custom resource exposes
no backend image override; the operator deployment supplies `RELATED_IMAGE_BACKEND`.
A deployment must use an agreed operator/image configuration process so the
operator does not restore the original image or lose a pod-local edit. This
requires a separate rollout decision from the workflow import.

Retain the original backend image digest above and the operator configuration
before rollout. Roll back through that same configuration process and confirm
that the managed backend/background-worker/worker pods return to their previous
image and become ready. No workflow execution or PR publication is part of rollout.

After deploying the rebuilt image:

1. Run the first AO node with only the incident trigger mocked.
2. Confirm `get_file_contents` tool results include actual variable-file text,
   not only the download notification, and no checkpoint TypeError occurs.
3. Apply the platform CasC so AAP syncs the updated `sha`/`ref` evidence validator.
4. Continue the full incident-to-PR and maintainer-feedback live tests. A missing
   file remains an observation/error to interpret; it must never be invented or
   silently promoted to successful evidence.
