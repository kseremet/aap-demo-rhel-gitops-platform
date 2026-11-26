"""Exercise real workflow context preparation and the installed AO API boundary."""

import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "files/ao/prepare_agent_prompt.py"
module = runpy.run_path(str(SOURCE))
prepare = module["prepare_prompt"]
compact = module["compact_handoff"]
WORKFLOWS = list((ROOT / "workflows/automation-orchestrator").glob("*.yaml"))


def test_loop_envelopes_drop_traces_without_changing_evidence():
    content = {
        "summary": "Measured result",
        "evidence": [
            {
                "id": "followup-0-1",
                "source": "web-01",
                "tool": "read_file",
                "observation": "Observed value",
                "tool_output_excerpt": "65535",
            }
        ],
        "unanswered_questions": ["Unknown overlay"],
    }
    envelope = {
        "handoff_follow_up_check.prompt": ["duplicated input" * 10000],
        "handoff_follow_up_check.characters": [150000],
        "follow_up_check.result": [
            {
                "content": content,
                "tools_used": ["read_file"],
                "agent_trace": {"steps": [{"tool_output": "x" * 100000}]},
            }
        ],
    }
    out = prepare("Context: @@HANDOFF_0@@", [envelope], "final_assessment")
    assert json.loads(out["prompt"].removeprefix("Context: ")) == {
        "follow_up_check.result": [content]
    }
    assert "agent_trace" not in out["prompt"]
    assert envelope["follow_up_check.result"][0]["agent_trace"]  # no mutations


def test_domain_file_content_and_blockers_never_truncated():
    domain = {
        "files": [{"path": "policy.yml", "content": "# complete\nvalue: 65535\n"}],
        "blocking_findings": ["Need independent check"],
        "resolved_findings": [],
    }
    assert compact(domain) == domain
    out = prepare("@@HANDOFF_0@@", [domain], "review_plan")
    assert json.loads(out["prompt"]) == domain


@pytest.mark.parametrize("count,valid", [(10000, True), (10001, False)])
def test_exact_character_limit(count, valid):
    if valid:
        assert prepare("x" * count, [], "stage")["characters"] == 10000
    else:
        with pytest.raises(
            ValueError, match="No evidence was truncated and no agent was invoked"
        ):
            prepare("x" * count, [], "stage")


def test_input_cannot_become_code_or_substitute_other_input():
    untrusted = '@@HANDOFF_1@@ ${trigger} __import__("os").system("false")'
    out = prepare(
        "@@HANDOFF_0@@ / @@HANDOFF_1@@", [untrusted, {"value": "safe"}], "review"
    )
    assert (
        out["prompt"]
        == json.dumps(untrusted, ensure_ascii=False, separators=(",", ":"))
        + ' / {"value":"safe"}'
    )


def test_actual_script_environment_protocol_and_unicode():
    env = {
        "PATH": os.environ["PATH"],
        "HANDOFF_COUNT": "1",
        "HANDOFF_0": json.dumps({"note": "café\n雪"}),
        "PROMPT_TEMPLATE": "Review @@HANDOFF_0@@",
        "HANDOFF_STAGE": "review_evidence",
    }
    p = subprocess.run(
        [sys.executable, str(SOURCE)],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    result = json.loads(p.stdout)
    assert json.loads(result["prompt"].removeprefix("Review ")) == {"note": "café\n雪"}
    assert result["characters"] == len(result["prompt"])


def test_script_execution_requires_explicit_admin_opt_in():
    config = yaml.safe_load((ROOT / "playbooks/ao_config.yml").read_text())[0]
    task = next(
        t
        for t in config["tasks"]
        if t.get("ansible.builtin.include_tasks") == "ao_configure_script_nodes.yml"
    )
    assert task["when"] == "demo_ao_manage_script_nodes | default(false) | bool"
    example = yaml.safe_load(
        (ROOT / "group_vars/all/demo_variables.yml.example").read_text()
    )
    assert example["demo_ao_manage_script_nodes"] is False


def test_oversized_case_uses_complete_packet_instead_of_truncation():
    contexts = [
        {
            "evidence": [{"id": "e1", "tool_output_excerpt": "exact evidence " * 1500}],
            "blocking_findings": ["Must resolve this"],
        }
    ]
    saved = []

    def store(value):
        import hashlib

        saved.append(value)
        return {
            "packet_id": "A" * 43,
            "sha256": hashlib.sha256(
                json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
            ).hexdigest(),
        }

    result = prepare("Review @@HANDOFF_0@@", contexts, "final_assessment", store=store)
    assert result["characters"] < 10000
    assert result["context_characters"] > 10000
    assert result["packet_id"] == "A" * 43
    assert "read_handoff" in result["prompt"]
    assert saved == [contexts]


def test_script_output_mapping_preserves_original_failure_details():
    for path in WORKFLOWS:
        w = yaml.safe_load(path.read_text())
        for node in w["nodes"]:
            if node["id"].startswith("handoff_"):
                assert node["outputs"] == {"result": "${result}"}
                # Error ScriptOutput has return_code/stdout/stderr, but no
                # stdout_json. Mapping its whole result cannot mask that error.
                error = {"return_code": 1, "stdout": "", "stderr": "original failure"}
                assert "stdout_json" not in error
