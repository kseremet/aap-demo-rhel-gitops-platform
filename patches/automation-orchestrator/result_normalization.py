"""Make MCP resources visible to the model and safe for graph checkpoints.

Compatibility fix for AO's langchain-mcp-adapters 0.1.14 conversion boundary.
Run before StructuredTool constructs/emits its ToolMessage so traces, models,
and checkpoint storage all receive the same complete text.
"""

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any

from pydantic import TypeAdapter

_JSON_VALUE = TypeAdapter(Any)


def normalize_mcp_tool_result(content: Any, artifacts: Any) -> tuple[Any, Any]:
    """Preserve artifacts as JSON data and expose embedded text resources."""
    if artifacts is None:
        return content, artifacts

    # mode=json converts MCP's nested AnyUrl objects into URI strings. Plain
    # model_dump()/dict() retains AnyUrl and breaks JsonPlus/msgpack checkpoints.
    safe_artifacts = _JSON_VALUE.dump_python(artifacts, mode="json", by_alias=True)
    displayed = (
        content
        if isinstance(content, str)
        else (
            "\n".join(
                part if isinstance(part, str) else part.get("text", "")
                for part in content
                if isinstance(part, (str, dict))
            )
            if isinstance(content, list)
            else ""
        )
    )
    additions = []
    for artifact in safe_artifacts if isinstance(safe_artifacts, list) else []:
        if not isinstance(artifact, dict) or artifact.get("type") != "resource":
            continue
        resource = artifact.get("resource")
        if not isinstance(resource, dict):
            continue
        text = resource.get("text")
        if not isinstance(text, str) or not text or text in displayed:
            continue
        uri = resource.get("uri", "")
        additions.append(f"MCP text resource {uri}:\n{text}")

    if additions:
        if isinstance(content, str):
            content = (
                "\n\n".join([content, *additions])
                if content
                else "\n\n".join(additions)
            )
        elif isinstance(content, list) and all(
            isinstance(part, str) for part in content
        ):
            content = "\n\n".join([*content, *additions])
        elif isinstance(content, list):
            content = [
                *content,
                *({"type": "text", "text": text} for text in additions),
            ]
    return content, safe_artifacts


def normalize_mcp_tool_coroutine(
    coroutine: Callable[..., Awaitable[Any]],
) -> Callable[..., Awaitable[Any]]:
    """Wrap only the adapter's content-and-artifact result, preserving runtime injection."""
    if getattr(coroutine, "_ao_mcp_result_normalized", False):
        return coroutine

    @wraps(coroutine)
    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        result = await coroutine(*args, **kwargs)
        if isinstance(result, tuple) and len(result) == 2:
            return normalize_mcp_tool_result(*result)
        return result

    setattr(wrapped, "_ao_mcp_result_normalized", True)
    return wrapped
