"""Build a lossless, compact agent handoff within AO's invocation API limit.

Runs as an AO Python script activity. Inputs arrive only through environment
variables, never executable code. Full collector traces remain on their original
nodes for the independent AAP validator and writer, outside model prompts.
"""

import json
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


def prepare_prompt(template, contexts, stage):
    # One substitution pass prevents user/repository text resembling a marker
    # from becoming another executable template or replacing another input.
    import re

    def substitute(match):
        value = compact_handoff(contexts[int(match.group(1))])
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    prompt = re.sub(r"@@HANDOFF_(\d+)@@", substitute, template)
    if "\0" in prompt:
        raise ValueError(f"{stage}: handoff contains a null byte")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise ValueError(
            f"{stage}: compact prompt is {len(prompt)} characters; AO's invocation "
            f"API limit is {MAX_PROMPT_CHARS}. No evidence was truncated and no "
            "agent was invoked. Reduce the case scope or split the handoff."
        )
    return {"prompt": prompt, "characters": len(prompt)}


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
                os.environ["PROMPT_TEMPLATE"], contexts, os.environ["HANDOFF_STAGE"]
            )
        )
    )
