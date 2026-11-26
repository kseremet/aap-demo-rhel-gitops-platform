"""Exercise the compatibility boundary using actual HTTP and SSE responses."""

import copy
import importlib.util
import json
from pathlib import Path
import threading
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

SOURCE = Path(__file__).parents[1] / "files/ao/github_mcp_text_adapter.py"
sys.path.insert(0, str(SOURCE.parent))
spec = importlib.util.spec_from_file_location("github_mcp_text_adapter", SOURCE)
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


def resource_result():
    return {
        "jsonrpc": "2.0",
        "id": 7,
        "result": {
            "content": [
                {"type": "text", "text": "successfully downloaded text file"},
                {
                    "type": "resource",
                    "resource": {
                        "uri": "repo://owner/repo/policy.yml",
                        "mimeType": "text/yaml",
                        "text": "net.core.somaxconn: 4096\n",
                    },
                    "annotations": {"audience": ["assistant"]},
                    "_meta": {"source": "git"},
                },
            ],
            "structuredContent": {"path": "policy.yml"},
            "_meta": {"test": True},
        },
    }


def test_file_body_visible_without_resource_artifacts():
    original = resource_result()
    before = copy.deepcopy(original)
    actual = adapter.normalize_response(original, "tools/call")
    assert original == before
    assert all(x["type"] == "text" for x in actual["result"]["content"])
    block = actual["result"]["content"][1]
    assert "net.core.somaxconn: 4096\n" in block["text"]
    assert "repo://owner/repo/policy.yml" in block["text"]
    assert block["annotations"] == {"audience": ["assistant"]}
    assert block["_meta"] == {"source": "git"}
    assert actual["result"]["structuredContent"] == {"path": "policy.yml"}
    assert actual["result"]["_meta"] == {"test": True}


@pytest.mark.parametrize("kind", ["image", "audio", "resource"])
def test_binary_content_fails_closed(kind):
    payload = resource_result()
    payload["result"]["content"] = [
        {"type": kind, "resource": {"uri": "repo://binary", "blob": "AAAA"}}
    ]
    out = adapter.normalize_response(payload, "tools/call")["result"]
    assert out["isError"] is True
    assert out["content"][0]["type"] == "text"
    assert "cannot expose" in out["content"][0]["text"]
    assert "AAAA" not in out["content"][0]["text"]


def test_links_empty_text_and_errors():
    payload = resource_result()
    payload["result"]["content"] = [
        {"type": "resource_link", "uri": "repo://link", "name": "link"},
        {"type": "resource", "resource": {"uri": "repo://empty", "text": ""}},
    ]
    payload["result"]["isError"] = True
    actual = adapter.normalize_response(payload, "tools/call")["result"]
    assert actual["isError"]
    assert all(x["type"] == "text" for x in actual["content"])
    assert "repo://link" in actual["content"][0]["text"]
    error = adapter.rpc_error(7, -32000, "original error")
    assert adapter.normalize_response(error, "tools/call") == error


@pytest.fixture
def servers():
    class Upstream(BaseHTTPRequestHandler):
        calls = []
        status = 200
        use_sse = True
        payload = resource_result()

        def log_message(self, *_):
            pass

        def do_POST(self):
            self.calls.append(
                (
                    self.path,
                    dict(self.headers),
                    json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                )
            )
            self.send_response(self.status)
            self.send_header(
                "Content-Type",
                "text/event-stream" if self.use_sse else "application/json",
            )
            self.send_header("Mcp-Session-Id", "upstream-session")
            self.end_headers()
            if self.status == 200:
                data = json.dumps(self.payload).encode()
                if self.use_sse:
                    self.wfile.write(
                        b'event: message\ndata: {"jsonrpc":"2.0","method":"notifications/progress","params":{}}\n\n'
                    )
                    self.wfile.write(b"event: message\ndata: " + data + b"\n\n")
                else:
                    self.wfile.write(data)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    proxy = adapter.AdapterServer(
        ("127.0.0.1", 0), f"http://127.0.0.1:{upstream.server_port}/mcp", timeout=2
    )
    threads = [
        threading.Thread(target=s.serve_forever, daemon=True) for s in (upstream, proxy)
    ]
    for thread in threads:
        thread.start()

    def call(method="tools/call", params=None, auth=True, raw=None):
        data = (
            raw
            if raw is not None
            else json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": method,
                    "params": (
                        params
                        if params is not None
                        else {"name": "get_file_contents", "arguments": {}}
                    ),
                }
            ).encode()
        )
        headers = {
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-03-26",
        }
        if auth:
            headers["Authorization"] = "Bearer fake-github-only-token"
        req = Request(
            f"http://127.0.0.1:{proxy.server_port}/mcp", data=data, headers=headers
        )
        try:
            response = urlopen(req, timeout=4)
        except HTTPError as error:
            response = error
        with response:
            body = response.read()
            return (
                response.status,
                dict(response.headers),
                json.loads(body) if body else None,
            )

    Upstream.proxy_url = f"http://127.0.0.1:{proxy.server_port}"
    yield Upstream, call
    for server in (proxy, upstream):
        server.shutdown()
        server.server_close()
    for thread in threads:
        thread.join(timeout=3)


@pytest.mark.parametrize("sse", [False, True])
def test_json_and_sse_roundtrip(servers, sse):
    upstream, call = servers
    upstream.use_sse = sse
    status, headers, body = call()
    assert status == 200
    assert headers["Content-Type"] == "application/json"
    assert headers["Mcp-Session-Id"] == "upstream-session"
    assert "net.core.somaxconn: 4096" in body["result"]["content"][1]["text"]
    path, forwarded, _ = upstream.calls[0]
    assert path == "/mcp"
    assert forwarded["Authorization"] == "Bearer fake-github-only-token"
    assert forwarded["MCP-Protocol-Version"] == "2025-03-26"


def test_credentials_required(servers):
    upstream, call = servers
    assert call(auth=False)[0] == 401
    assert upstream.calls == []


@pytest.mark.parametrize(
    "method,params",
    [
        ("tools/call", {"name": "create_pull_request"}),
        ("resources/read", {}),
        ({"bad": 1}, {}),
        ("tools/call", {"name": ["invalid"]}),
    ],
)
def test_unsupported_requests_never_reach_github(servers, method, params):
    upstream, call = servers
    status, _, body = call(method, params)
    assert status == 200
    assert "error" in body
    assert upstream.calls == []


def test_invalid_json_is_client_error(servers):
    upstream, call = servers
    assert call(raw=b"{")[0] == 400
    assert upstream.calls == []


def test_no_redirect_or_credential_forward_to_other_host(servers):
    upstream, call = servers
    upstream.status = 302
    status, _, body = call()
    assert status == 502
    assert "redirects are not followed" in body["error"]["message"]
    assert len(upstream.calls) == 1


def test_original_tool_error_preserved(servers):
    upstream, call = servers
    upstream.payload = {
        "jsonrpc": "2.0",
        "id": 7,
        "result": {
            "isError": True,
            "content": [{"type": "text", "text": "404 Not Found"}],
        },
    }
    assert call()[2] == upstream.payload


def test_only_read_tools_and_no_push_capability(servers):
    upstream, call = servers
    upstream.payload = {
        "jsonrpc": "2.0",
        "id": 7,
        "result": {
            "tools": [
                {"name": x}
                for x in ["get_commit", "get_file_contents", "create_pull_request"]
            ]
        },
    }
    assert [x["name"] for x in call("tools/list")[2]["result"]["tools"]] == [
        "get_commit",
        "get_file_contents",
        "read_handoff",
    ]
    upstream.payload = {
        "jsonrpc": "2.0",
        "id": 7,
        "result": {"capabilities": {"tools": {"listChanged": True}, "resources": {}}},
    }
    assert call("initialize")[2]["result"]["capabilities"] == {"tools": {}}


def test_accepted_notification(servers):
    upstream, call = servers
    upstream.status = 202
    assert call("notifications/initialized")[0] == 202


def test_response_size_limit(servers):
    upstream, call = servers
    upstream.payload["result"]["content"][1]["resource"]["text"] = "a" * (
        adapter.MAX_RESPONSE_BYTES + 1
    )
    assert call()[0] == 502


def test_large_handoff_roundtrip_without_upstream_or_truncation(servers):
    upstream, call = servers
    from prepare_agent_prompt import prepare_prompt, upload_contexts

    contexts = [
        {
            "evidence": ["complete exact excerpt " * 1000],
            "blocking_findings": ["unresolved"],
        }
    ]
    result = prepare_prompt(
        "Review @@HANDOFF_0@@",
        contexts,
        "final_assessment",
        store=lambda values: upload_contexts(upstream.proxy_url + "/handoffs", values),
    )
    assert result["context_characters"] > 10000 and result["characters"] < 10000
    status, _, body = call(
        params={"name": "read_handoff", "arguments": {"packet_id": result["packet_id"]}}
    )
    assert status == 200 and not body["result"].get("isError")
    loaded = json.loads(body["result"]["content"][0]["text"])
    assert loaded["contexts"] == contexts
    assert upstream.calls == []  # bearer and packet are never forwarded to GitHub
    assert call(params={"name": "read_handoff", "arguments": {"packet_id": "unknown"}})[
        2
    ]["result"]["isError"]


def test_handoff_store_expiry_and_quota():
    from handoff_packets import PacketStore

    clock = [0]
    store = PacketStore(
        ttl=10, max_bytes=120, max_packet_bytes=100, clock=lambda: clock[0]
    )
    packet = store.put(["data" * 12])
    assert store.get(packet["packet_id"])["contexts"] == ["data" * 12]
    with pytest.raises(ValueError, match="quota"):
        store.put(["more" * 20])
    with pytest.raises(ValueError, match="256 KiB"):
        store.put(["oversize" * 100])
    clock[0] = 11
    with pytest.raises(ValueError, match="expired"):
        store.get(packet["packet_id"])
    assert store.put(["new"])["packet_id"] != packet["packet_id"]


def test_native_http_preparation_accepts_full_loop_larger_than_script_env(servers):
    upstream, call = servers
    from prepare_agent_prompt import compact_handoff

    contexts = [
        {
            "follow_up_check.result": [
                {
                    "content": {
                        "evidence": [
                            {
                                "id": "e1",
                                "tool_output_excerpt": "exact evidence " * 1500,
                            }
                        ],
                        "blocking_findings": ["unresolved"],
                    },
                    "agent_trace": {"steps": "transcript " * 6000},
                }
            ]
        }
    ]
    body = json.dumps(
        {
            "template": "Review @@HANDOFF_0@@",
            "contexts": contexts,
            "stage": "final_assessment",
        }
    ).encode()
    assert len(body) > 32768
    request = Request(
        upstream.proxy_url + "/prepare-prompt",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=4) as response:
        assert response.status == 200
        prepared = json.load(response)
    assert prepared["characters"] < 10000 and prepared["context_characters"] > 10000
    loaded = call(
        params={
            "name": "read_handoff",
            "arguments": {"packet_id": prepared["packet_id"]},
        }
    )[2]
    assert json.loads(loaded["result"]["content"][0]["text"])[
        "contexts"
    ] == compact_handoff(contexts)
    assert upstream.calls == []
