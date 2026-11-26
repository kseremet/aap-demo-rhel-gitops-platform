"""Stateless MCP compatibility adapter for AO's existing GitHub integration.

Uses only Python's standard library. Converts embedded resources to text before
AO's MCP SDK sees them; no typed URI artifacts reach its graph checkpoint. The
incoming bearer credential is forwarded only to the fixed upstream MCP URL and
is never stored or logged. Only the demo's five read-only GitHub tools and local handoff retrieval are exposed.
"""

import copy
from handoff_packets import HANDOFF_TOOL, PacketStore
from prepare_agent_prompt import prepare_prompt
from review_checks import prepare_review
from ao_evidence import normalize_assessment
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import ssl
from urllib.parse import urlsplit

READ_TOOLS = frozenset(
    {
        "get_commit",
        "get_file_contents",
        "pull_request_read",
        "search_code",
        "list_commits",
    }
)
METHODS = frozenset(
    {
        "initialize",
        "notifications/initialized",
        "notifications/cancelled",
        "ping",
        "tools/list",
        "tools/call",
    }
)
MAX_REQUEST_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class AdapterError(Exception):
    """A bounded, non-secret error suitable for the MCP client."""


def rpc_error(identifier, code, message):
    return {
        "jsonrpc": "2.0",
        "id": identifier,
        "error": {"code": code, "message": message},
    }


def normalize_response(payload, method):
    """Preserve JSON-RPC errors, structured output and metadata; flatten content."""
    result = copy.deepcopy(payload)
    if not isinstance(result, dict) or not isinstance(result.get("result"), dict):
        return result
    output = result["result"]
    if method == "initialize":
        # This adapter implements request/response tools, without server push.
        output["capabilities"] = {"tools": {}}
    if method == "tools/list" and isinstance(output.get("tools"), list):
        output["tools"] = [
            t
            for t in output["tools"]
            if isinstance(t, dict) and t.get("name") in READ_TOOLS
        ]
    if method != "tools/call" or not isinstance(output.get("content"), list):
        return result
    content = []
    for block in output["content"]:
        if not isinstance(block, dict):
            raise AdapterError("Malformed upstream MCP content block")
        kind = block.get("type")
        if kind == "text":
            content.append(block)
            continue
        metadata = {k: block[k] for k in ("annotations", "_meta") if k in block}
        resource = block.get("resource", {})
        if (
            kind == "resource"
            and isinstance(resource, dict)
            and isinstance(resource.get("text"), str)
        ):
            content.append(
                {
                    "type": "text",
                    "text": f"MCP text resource {resource.get('uri', '')}:\n{resource['text']}",
                    **metadata,
                }
            )
        elif kind == "resource_link":
            content.append(
                {
                    "type": "text",
                    "text": "MCP resource link: "
                    + json.dumps(block, ensure_ascii=False, separators=(",", ":")),
                    **metadata,
                }
            )
        else:
            # Binary/image/audio content is not diagnostic YAML/text evidence.
            # Return an explicit MCP failure instead of fabricating a text read.
            output["isError"] = True
            uri = resource.get("uri", "") if isinstance(resource, dict) else ""
            content.append(
                {
                    "type": "text",
                    "text": f"The GitHub MCP text adapter cannot expose {kind!r} as text evidence. Resource URI: {uri}",
                    **metadata,
                }
            )
    output["content"] = content
    return result


def read_rpc_response(response, identifier, limit=MAX_RESPONSE_BYTES):
    """Accept JSON or a bounded SSE response, returning the final RPC message.

    The upstream GitHub server is stateless. Buffer a tool's SSE messages until
    its final result, then answer AO with application/json, also permitted by
    Streamable HTTP. Progress/log notifications are not relayed as server push.
    """
    content_type = (
        response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
    )
    if content_type == "application/json":
        data = response.read(limit + 1)
        if len(data) > limit:
            raise AdapterError("Upstream MCP response exceeds the configured limit")
        return json.loads(data)
    if content_type != "text/event-stream":
        raise AdapterError("Upstream MCP response is neither JSON nor SSE")
    size = 0
    data_lines = []
    while True:
        line = response.readline(limit - size + 1)
        size += len(line)
        if size > limit:
            raise AdapterError("Upstream MCP response exceeds the configured limit")
        if not line or line in (b"\n", b"\r\n"):
            if data_lines:
                event = json.loads(b"\n".join(data_lines))
                if (
                    isinstance(event, dict)
                    and event.get("id") == identifier
                    and ("result" in event or "error" in event)
                ):
                    return event
                if isinstance(event, dict) and "method" in event and "id" in event:
                    raise AdapterError(
                        "Server-initiated MCP requests are not supported by the text adapter"
                    )
            data_lines = []
            if not line:
                raise AdapterError(
                    "Upstream SSE ended without the requested MCP result"
                )
        elif line.startswith(b"data:"):
            data_lines.append(line[5:].removeprefix(b" ").rstrip(b"\r\n"))


class AdapterServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, upstream_url, timeout=20):
        upstream = urlsplit(upstream_url)
        if (
            upstream.scheme not in {"http", "https"}
            or not upstream.hostname
            or upstream.username
            or upstream.password
            or upstream.query
            or upstream.fragment
        ):
            raise ValueError(
                "Configure one HTTP(S) upstream URL without credentials, query or fragment"
            )
        self.packets = PacketStore()
        self.upstream = upstream
        self.upstream_timeout = timeout
        super().__init__(address, AdapterHandler)


class AdapterHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "GitHubMCPTextAdapter/1"

    def log_message(self, format, *args):
        # Do not log headers, request bodies, URLs, file contents or credentials.
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(self.server.upstream_timeout + 5)

    def send_body(
        self, status, body=b"", content_type="application/json", headers=None
    ):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.close_connection = True
        if body:
            self.wfile.write(body)

    def send_json(self, status, value, headers=None):
        self.send_body(
            status,
            json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(),
            headers=headers,
        )

    def do_GET(self):
        if self.path == "/healthz":
            self.send_json(200, {"status": "ok"})
        else:
            self.send_body(405, headers={"Allow": "POST"})

    def do_DELETE(self):
        # There are no adapter-side sessions to terminate.
        self.send_body(405, headers={"Allow": "POST"})

    def do_POST(self):
        if self.path in (
            "/handoffs",
            "/prepare-prompt",
            "/review-checks",
            "/prepare-assessment",
        ):
            # Internal, bounded JSON storage only. No bearer or host credential
            # is needed or forwarded; the returned random ID grants read access.
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if (
                    self.headers.get("Transfer-Encoding")
                    or not 0 < length <= MAX_REQUEST_BYTES
                ):
                    raise ValueError("A bounded handoff Content-Length is required")
                if (
                    self.headers.get("Content-Type", "").split(";")[0]
                    != "application/json"
                ):
                    raise ValueError("Handoff upload requires application/json")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("Handoff request must be an object")
                if self.path == "/prepare-assessment":
                    if set(data) != {"report", "packet", "review", "repository_url"}:
                        raise ValueError(
                            "Assessment preparation requires report, packet, review and repository_url"
                        )
                    self.send_json(
                        200,
                        normalize_assessment(
                            data["report"],
                            data["packet"],
                            data["review"],
                            data["repository_url"],
                        ),
                    )
                elif self.path == "/review-checks":
                    if set(data) != {"review"}:
                        raise ValueError("Review preparation requires only review")
                    self.send_json(200, prepare_review(data["review"]))
                elif self.path == "/prepare-prompt":
                    if (
                        set(data) != {"template", "contexts", "stage"}
                        or not isinstance(data["template"], str)
                        or not isinstance(data["contexts"], list)
                        or not isinstance(data["stage"], str)
                    ):
                        raise ValueError(
                            "Preparation requires template, contexts and stage"
                        )
                    self.send_json(
                        200,
                        prepare_prompt(
                            data["template"],
                            data["contexts"],
                            data["stage"],
                            store=self.server.packets.put,
                        ),
                    )
                else:
                    if set(data) != {"contexts"}:
                        raise ValueError("Handoff upload requires only contexts")
                    self.send_json(201, self.server.packets.put(data["contexts"]))
            except (ValueError, OSError, IndexError, TypeError):
                self.send_json(
                    400,
                    {
                        "error": "Invalid or oversized handoff input; no agent was invoked"
                    },
                )
            return
        if self.path != "/mcp":
            self.send_json(404, rpc_error(None, -32600, "Unknown MCP endpoint"))
            return
        authorization = self.headers.get("Authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            self.send_json(
                401,
                rpc_error(None, -32001, "A GitHub bearer credential is required"),
                {"WWW-Authenticate": "Bearer"},
            )
            return
        identifier = None
        upstream = None
        try:
            if (
                self.headers.get("Transfer-Encoding")
                or self.headers.get("Content-Length") is None
            ):
                self.send_json(
                    411, rpc_error(None, -32600, "A bounded Content-Length is required")
                )
                return
            length = int(self.headers["Content-Length"])
            if not 0 < length <= MAX_REQUEST_BYTES:
                self.send_json(
                    413,
                    rpc_error(None, -32600, "MCP request exceeds the configured limit"),
                )
                return
            if (
                not self.headers.get("Content-Type", "")
                .lower()
                .startswith("application/json")
            ):
                self.send_json(
                    415,
                    rpc_error(None, -32600, "MCP requests must use application/json"),
                )
                return
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise AdapterError("Incomplete MCP request")
            request = json.loads(raw)
            if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
                self.send_json(
                    400,
                    rpc_error(
                        None, -32600, "A single JSON-RPC 2.0 request is required"
                    ),
                )
                return
            identifier = request.get("id")
            method = request.get("method")
            if not isinstance(method, str) or method not in METHODS:
                self.send_json(
                    200,
                    rpc_error(
                        identifier,
                        -32601,
                        "The text adapter exposes request/response tools only",
                    ),
                )
                return
            params = request.get("params", {})
            if (
                method == "tools/call"
                and isinstance(params, dict)
                and params.get("name") == "read_handoff"
            ):
                try:
                    args = params.get("arguments", {})
                    if not isinstance(args, dict) or set(args) != {"packet_id"}:
                        raise ValueError("read_handoff requires only packet_id")
                    output = self.server.packets.get(args["packet_id"])
                    result = {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    output, ensure_ascii=False, separators=(",", ":")
                                ),
                            }
                        ]
                    }
                except ValueError as error:
                    result = {
                        "isError": True,
                        "content": [{"type": "text", "text": str(error)}],
                    }
                self.send_json(
                    200, {"jsonrpc": "2.0", "id": identifier, "result": result}
                )
                return
            if method == "tools/call" and (
                not isinstance(params, dict)
                or not isinstance(params.get("name"), str)
                or params.get("name") not in READ_TOOLS
            ):
                self.send_json(
                    200,
                    rpc_error(
                        identifier,
                        -32602,
                        "Only the configured read-only GitHub tools are available",
                    ),
                )
                return
            target = self.server.upstream
            if target.scheme == "https":
                upstream = http.client.HTTPSConnection(
                    target.hostname,
                    target.port,
                    timeout=self.server.upstream_timeout,
                    context=ssl.create_default_context(),
                )
            else:
                upstream = http.client.HTTPConnection(
                    target.hostname, target.port, timeout=self.server.upstream_timeout
                )
            headers = {
                "Authorization": authorization,
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Accept-Encoding": "identity",
            }
            for name in (
                "MCP-Protocol-Version",
                "Mcp-Session-Id",
                "Last-Event-ID",
                "X-Request-ID",
            ):
                if self.headers.get(name):
                    headers[name] = self.headers[name]
            upstream.request("POST", target.path or "/mcp", body=raw, headers=headers)
            response = upstream.getresponse()
            forwarded = {
                name: response.getheader(name)
                for name in (
                    "MCP-Protocol-Version",
                    "Mcp-Session-Id",
                    "WWW-Authenticate",
                    "Retry-After",
                )
                if response.getheader(name)
            }
            if 300 <= response.status < 400:
                raise AdapterError(
                    "Upstream redirects are not followed by the MCP text adapter"
                )
            if response.status in (202, 204):
                self.send_body(response.status, headers=forwarded)
                return
            if response.status != 200:
                data = response.read(MAX_RESPONSE_BYTES + 1)
                if len(data) > MAX_RESPONSE_BYTES:
                    raise AdapterError(
                        "Upstream error response exceeds the configured limit"
                    )
                self.send_body(
                    response.status,
                    data,
                    response.getheader("Content-Type", "application/json"),
                    forwarded,
                )
                return
            payload = read_rpc_response(response, identifier)
            payload = normalize_response(payload, method)
            if method == "tools/list" and isinstance(
                payload.get("result", {}).get("tools"), list
            ):
                payload["result"]["tools"].append(HANDOFF_TOOL)
            self.send_json(200, payload, forwarded)
        except (AdapterError, json.JSONDecodeError, ValueError) as error:
            message = (
                str(error)
                if isinstance(error, AdapterError)
                else "Invalid MCP JSON or HTTP framing"
            )
            self.send_json(
                502 if upstream is not None else 400,
                rpc_error(identifier, -32603, message),
            )
        except (OSError, http.client.HTTPException):
            self.send_json(
                502,
                rpc_error(identifier, -32603, "Upstream GitHub MCP connection failed"),
            )
        finally:
            if upstream is not None:
                upstream.close()


if __name__ == "__main__":
    server = AdapterServer(
        (
            os.environ.get("LISTEN_HOST", "0.0.0.0"),
            int(os.environ.get("LISTEN_PORT", "8080")),
        ),
        os.environ["MCP_UPSTREAM_URL"],
        timeout=int(os.environ.get("MCP_UPSTREAM_TIMEOUT", "20")),
    )
    server.serve_forever()
