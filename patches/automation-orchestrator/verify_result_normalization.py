"""Offline regression checks using the AO runtime's installed MCP/LangGraph stack.

Run beside result_normalization.py with the AO image's Python. No model calls,
MCP connections, application configuration changes, or database writes occur.
"""

import inspect
import json
import operator
import unittest
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool, ToolException
from langchain_mcp_adapters.tools import (
    _convert_call_tool_result,
    convert_mcp_tool_to_langchain_tool,
)
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode
from mcp.types import Tool as MCPTool
from mcp.types import (
    BlobResourceContents,
    CallToolResult,
    EmbeddedResource,
    TextContent,
    TextResourceContents,
)

from result_normalization import normalize_mcp_tool_coroutine, normalize_mcp_tool_result

TEXT = "---\nkernel_settings_web_servers:\n  backlog:\n    name: net.core.somaxconn\n    value: '65535'\n"
URI = "repo://diagnostic/group_vars/web_servers/kernel_settings.yml"
SUMMARY = "successfully downloaded text file"


def resource_result():
    return CallToolResult(
        content=[
            TextContent(type="text", text=SUMMARY),
            EmbeddedResource(
                type="resource",
                _meta={"diagnostic": True},
                resource=TextResourceContents(
                    uri=URI, mimeType="text/plain", text=TEXT
                ),
            ),
        ]
    )


def converted_resource():
    return _convert_call_tool_result(resource_result())


class NormalizationTests(unittest.TestCase):
    def test_reproduces_unpatched_checkpoint_failure(self):
        content, artifact = converted_resource()
        self.assertNotIn(TEXT, content)
        message = ToolMessage(
            content=content, artifact=artifact, tool_call_id="diagnostic"
        )
        with self.assertRaisesRegex(TypeError, "ToolMessage"):
            JsonPlusSerializer().dumps_typed(message)

    def test_resource_text_reaches_model_and_checkpoint_as_json(self):
        content, artifact = normalize_mcp_tool_result(*converted_resource())
        self.assertIn(TEXT, content)
        self.assertIn(URI, content)
        self.assertIn(SUMMARY, content)
        self.assertEqual(artifact[0]["resource"]["uri"], URI)
        self.assertEqual(artifact[0]["_meta"], {"diagnostic": True})
        self.assertEqual(artifact[0]["resource"]["text"], TEXT)
        json.dumps(artifact)
        message = ToolMessage(
            content=content, artifact=artifact, tool_call_id="diagnostic"
        )
        serializer = JsonPlusSerializer()
        restored = serializer.loads_typed(serializer.dumps_typed(message))
        self.assertEqual(restored.content, content)
        self.assertEqual(restored.artifact, artifact)

    def test_plain_results_are_unchanged(self):
        self.assertEqual(normalize_mcp_tool_result("65535\n", None), ("65535\n", None))

    def test_already_visible_text_is_not_duplicated(self):
        _, artifact = converted_resource()
        content, _ = normalize_mcp_tool_result(TEXT, artifact)
        self.assertEqual(content, TEXT)

    def test_multiple_text_contents_are_preserved(self):
        _, artifact = converted_resource()
        content, _ = normalize_mcp_tool_result(["first", "second"], artifact)
        self.assertIn("first\n\nsecond", content)
        self.assertIn(TEXT, content)

    def test_binary_resource_is_preserved_without_exposing_blob_as_text(self):
        artifact = [
            EmbeddedResource(
                type="resource",
                resource=BlobResourceContents(
                    uri="repo://diagnostic/blob",
                    mimeType="application/octet-stream",
                    blob="AAE=",
                ),
            )
        ]
        content, normalized = normalize_mcp_tool_result("binary resource", artifact)
        self.assertEqual(content, "binary resource")
        self.assertEqual(normalized[0]["resource"]["blob"], "AAE=")
        message = ToolMessage(
            content=content, artifact=normalized, tool_call_id="diagnostic"
        )
        JsonPlusSerializer().dumps_typed(message)


class AsyncNormalizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_wrapper_preserves_runtime_signature_and_is_idempotent(self):
        async def original(runtime: Any = None, **arguments):
            return converted_resource()

        wrapped = normalize_mcp_tool_coroutine(original)
        self.assertEqual(inspect.signature(wrapped), inspect.signature(original))
        self.assertIs(normalize_mcp_tool_coroutine(wrapped), wrapped)
        content, _ = await wrapped(runtime=object())
        self.assertIn(TEXT, content)

    async def test_tool_errors_propagate_without_becoming_successful_evidence(self):
        async def missing(runtime: Any = None, **arguments):
            raise ToolException("404 Not Found")

        with self.assertRaisesRegex(ToolException, "404 Not Found"):
            await normalize_mcp_tool_coroutine(missing)()

    async def test_stream_events_and_model_history_contain_complete_file_text(self):
        class FakeSession:
            async def call_tool(self, name, arguments, **kwargs):
                return resource_result()

        tool = convert_mcp_tool_to_langchain_tool(
            FakeSession(),
            MCPTool(
                name="diagnostic_file",
                description="Offline file resource",
                inputSchema={"type": "object", "properties": {}},
            ),
        )
        self.assertIsInstance(tool, StructuredTool)
        tool.coroutine = normalize_mcp_tool_coroutine(tool.coroutine)

        class State(TypedDict):
            messages: Annotated[list, operator.add]

        async def agent(state):
            if any(isinstance(m, ToolMessage) for m in state["messages"]):
                self.assertIn(TEXT, state["messages"][-1].content)
                return {"messages": [AIMessage(content="File content verified.")]}
            return {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {"name": tool.name, "args": {}, "id": "diagnostic"}
                        ],
                    )
                ]
            }

        graph = StateGraph(State)
        graph.add_node("agent", agent)
        graph.add_node("tools", ToolNode([tool]))
        graph.set_entry_point("agent")
        graph.add_conditional_edges(
            "agent", lambda s: "tools" if s["messages"][-1].tool_calls else END
        )
        graph.add_edge("tools", "agent")
        seen_result = False
        async for event in graph.compile(checkpointer=MemorySaver()).astream_events(
            {"messages": []},
            {"configurable": {"thread_id": "offline-diagnostic"}},
            version="v2",
        ):
            if event["event"] == "on_tool_end":
                self.assertIn(TEXT, event["data"]["output"].content)
                seen_result = True
        self.assertTrue(seen_result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
