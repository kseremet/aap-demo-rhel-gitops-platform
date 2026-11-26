"""Build a lossless, compact agent handoff within AO's invocation API limit.

Runs as fixed code in the adapter's HTTP endpoint. Inputs are JSON data,
never executable code. The CLI is retained for offline compatibility checks. Full collector traces remain on their original
nodes for the independent AAP validator and writer, outside model prompts.
"""

import json
import hashlib
import os

MAX_PROMPT_CHARS = 10000


def compact_handoff(value):
    """Unwrap AO loop invocation results without copying tool/reasoning traces."""
    if isinstance(value, list):
        return [compact_handoff(item) for item in value]
    if not isinstance(value, dict):
        return value
    # The loop also aggregates its preparation node's large prompt. That is
    # duplicated input, not a diagnostic observation; retain only check results.
    if "follow_up_check.result" in value:
        return {
            "follow_up_check.result": compact_handoff(value["follow_up_check.result"])
        }
    # A loop aggregates follow_up_check.result as invocation result envelopes.
    # Domain objects (evidence items, plans, file content) stay intact.
    if isinstance(value.get("content"), dict) and (
        "agent_trace" in value
        or "tools_used" in value
        or "used_tools" in value
        or set(value) == {"content"}
    ):
        return compact_handoff(value["content"])
    if isinstance(value.get("result"), dict) and "id" in value:
        return compact_handoff(value["result"])
    return {key: compact_handoff(item) for key, item in value.items()}


def prepare_prompt(template, contexts, stage, store=None):
    # One substitution pass prevents user/repository text resembling a marker
    # from becoming another executable template or replacing another input.
    import re

    def substitute(match):
        value = compact_handoff(contexts[int(match.group(1))])
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    contexts = [compact_handoff(value) for value in contexts]
    prompt = re.sub(r"@@HANDOFF_(\d+)@@", substitute, template)
    original_characters = len(prompt)
    packet = None
    if len(prompt) > MAX_PROMPT_CHARS and store is not None:
        # Preserve the complete contexts as one MCP-readable packet. Large tool
        # results are supported by AO; only the initial invocation has this cap.
        packet = store(contexts)
        packet_id = packet["packet_id"]
        prompt = (
            f"Before analysis, call read_handoff with packet_id={packet_id} to "
            "load ALL numbered context sections. Its SHA-256 is "
            + packet["sha256"]
            + ". This read is one of the four allowed tool calls. Preserve the "
            "packet's exact evidence and blockers; do not infer missing context.\n\n"
            + re.sub(
                r"@@HANDOFF_(\d+)@@",
                lambda m: "[Context " + m.group(1) + " in the read_handoff packet]",
                template,
            )
        )
    if "\0" in prompt:
        raise ValueError(f"{stage}: handoff contains a null byte")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise ValueError(
            f"{stage}: compact prompt is {len(prompt)} characters; AO's invocation "
            f"API limit is {MAX_PROMPT_CHARS}. No evidence was truncated and no "
            "agent was invoked. Configure the handoff store or split the case."
        )
    return {
        "prompt": prompt,
        "characters": len(prompt),
        "context_characters": original_characters,
        "packet_id": packet["packet_id"] if packet else None,
    }


def upload_contexts(url, contexts):
    import http.client
    from urllib.parse import urlsplit

    target = urlsplit(url)
    if (
        target.scheme not in ("http", "https")
        or not target.hostname
        or target.username
        or target.password
        or target.query
        or target.fragment
        or target.path != "/handoffs"
    ):
        raise ValueError(
            "Configure a fixed HTTP(S) handoff endpoint without credentials or query"
        )
    cls = (
        http.client.HTTPSConnection
        if target.scheme == "https"
        else http.client.HTTPConnection
    )
    connection = cls(target.hostname, target.port, timeout=15)
    try:
        connection.request(
            "POST",
            target.path,
            body=json.dumps({"contexts": contexts}, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        # Never follow redirects or send credentials to this endpoint.
        if response.status != 201:
            raise ValueError(
                f"Handoff store returned HTTP {response.status}; no agent was invoked"
            )
        raw = response.read(4097)
        if len(raw) > 4096:
            raise ValueError("Handoff store returned oversized metadata")
        packet = json.loads(raw)
        import re

        if not re.fullmatch(
            r"[A-Za-z0-9_-]{43}", packet.get("packet_id", "")
        ) or not re.fullmatch(r"[0-9a-f]{64}", packet.get("sha256", "")):
            raise ValueError("Handoff store returned invalid metadata")
        expected = (
            __import__("hashlib")
            .sha256(
                json.dumps(contexts, ensure_ascii=False, separators=(",", ":")).encode()
            )
            .hexdigest()
        )
        if packet["sha256"] != expected:
            raise ValueError("Handoff packet digest mismatch")
        return packet
    finally:
        connection.close()


if __name__ == "__main__":
    contexts = []
    for index in range(int(os.environ["HANDOFF_COUNT"])):
        raw = os.environ[f"HANDOFF_{index}"]
        try:
            contexts.append(json.loads(raw))
        except json.JSONDecodeError:
            contexts.append(raw)
    print(
        json.dumps(
            prepare_prompt(
                os.environ["PROMPT_TEMPLATE"],
                contexts,
                os.environ["HANDOFF_STAGE"],
                store=(
                    (
                        lambda values: upload_contexts(
                            os.environ["HANDOFF_STORE_URL"], values
                        )
                    )
                    if os.environ.get("HANDOFF_STORE_URL")
                    else None
                ),
            )
        )
    )
