"""Exercise provenance rejection using AO's actual call/result trace shape."""

from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest

from test_ao_gitops_writer import REPO, handoff, runtime_evidence

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "ao_evidence", ROOT / "playbooks/ai/filter_plugins/ao_evidence.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def verdict(report, packet, review=None):
    return MODULE.validate_evidence(
        report, packet, review or {"blocking_findings": []}, REPO
    )


@pytest.mark.parametrize("decision", ["ready", "no_change"])
def test_successful_pinned_observations_support_a_success_outcome(handoff, decision):
    report = handoff[1]
    report["decision"] = decision
    result = verdict(report, runtime_evidence(report))
    assert result["valid"], result
    assert result["successful_tool_results"] == 4


@pytest.mark.parametrize(
    "failure",
    [
        "missing_trace",
        "calls_only",
        "failed_result",
        "mcp_error",
        "timeout",
        "wrong_call_id",
        "invented_excerpt",
        "invented_tool",
        "unpinned_read",
        "wrong_host",
        "missing_identity",
        "missing_commit",
        "blockers",
        "missing_blockers",
        "hypothesis",
        "blocked",
        "duplicate_evidence",
    ],
)
def test_unverified_or_blocked_assessments_cannot_pass(handoff, failure):
    report = handoff[1]
    packet = runtime_evidence(report)
    result = packet["host"]["result"]["agent_trace"]["steps"][-1]
    if failure == "missing_trace":
        packet = {"result": {"tools_used": ["read_file"], "content": report}}
    elif failure == "calls_only":
        packet["host"]["result"]["agent_trace"]["steps"].pop()
    elif failure == "failed_result":
        result["status"] = "failed"
    elif failure == "mcp_error":
        result["tool_output"] = '{"isError":true,"text":"net.core.somaxconn=65535"}'
    elif failure == "timeout":
        result["tool_output"] = "TimeoutError: net.core.somaxconn=65535"
    elif failure == "wrong_call_id":
        result["call_id"] = "another-call"
    elif failure == "invented_excerpt":
        report["evidence"][0]["tool_output_excerpt"] = "invented host measurement"
    elif failure == "invented_tool":
        report["evidence"][0]["tool"] = "sysctl/read from OS"
    elif failure == "unpinned_read":
        packet["file"]["result"]["agent_trace"]["steps"][0]["tool_input"][
            "ref"
        ] = "main"
    elif failure == "wrong_host":
        packet["host"]["result"]["agent_trace"]["steps"][0]["tool_input"][
            "host"
        ] = "localhost"
    elif failure == "missing_identity":
        del packet["identity"]
    elif failure == "missing_commit":
        del packet["commit"]
    elif failure == "blockers":
        report["blocking_findings"] = ["Cannot determine whether automation failed."]
    elif failure == "missing_blockers":
        del report["blocking_findings"]
    elif failure == "hypothesis":
        report["conclusion_status"] = "hypothesis"
    elif failure == "blocked":
        report["decision"] = "blocked"
    elif failure == "duplicate_evidence":
        report["evidence"].append(deepcopy(report["evidence"][0]))
    result = verdict(report, packet)
    assert not result["valid"], failure
    assert result["blocking_findings"]


def test_empty_follow_up_does_not_clear_reviewer_blockers(handoff):
    report = handoff[1]
    review = {"blocking_findings": ["Policy source is unverified."], "follow_up": []}
    assert not verdict(report, runtime_evidence(report), review)["valid"]
    report["resolved_findings"] = [
        {"finding": review["blocking_findings"][0], "evidence_ids": ["invented-id"]}
    ]
    assert not verdict(report, runtime_evidence(report), review)["valid"]
    report["resolved_findings"][0]["evidence_ids"] = ["repo-1"]
    assert verdict(report, runtime_evidence(report), review)["valid"]


def test_loop_aggregates_preserve_provenance(handoff):
    report = handoff[1]
    packet = runtime_evidence(report)
    host = packet.pop("host")
    packet["loop"] = {"follow_up_check.result": [host["result"]]}
    assert verdict(report, packet)["valid"]


@pytest.mark.parametrize(
    "report,packet,review", [(None, {}, {}), ({}, [], {}), ({}, {}, None), ({}, {}, {})]
)
def test_malformed_input_is_blocked(report, packet, review):
    assert not MODULE.validate_evidence(report, packet, review, REPO)["valid"]


@pytest.mark.parametrize("valid", [True, False])
def test_read_only_aap_job_returns_a_routable_verdict(tmp_path, handoff, valid):
    import json
    import os
    import shutil
    import subprocess

    report = handoff[1]
    payload = {
        "ao_report": report,
        "ao_evidence": runtime_evidence(report) if valid else {},
        "ao_evidence_review": {"blocking_findings": []},
        "demo_state_scm_url": REPO,
    }
    shutil.copytree(
        ROOT / "playbooks/ai",
        tmp_path / "ai",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    extra = tmp_path / "extra.json"
    extra.write_text(json.dumps(payload))
    completed = subprocess.run(
        [
            "ansible-playbook",
            "-i",
            "localhost,",
            "ai/validate_ao_evidence.yml",
            "-e",
            "@" + str(extra),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **os.environ,
            "ANSIBLE_SHOW_CUSTOM_STATS": "true",
            "ANSIBLE_NOCOLOR": "true",
        },
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "ao_evidence_validation" in completed.stdout
    assert f'"valid": {str(valid).lower()}' in completed.stdout


def test_scalar_measurements_and_display_namespace_preserve_provenance(handoff):
    report = handoff[1]
    packet = runtime_evidence(report)
    packet["host"]["result"]["agent_trace"]["steps"][-1]["tool_output"] = "65535\n"
    report["evidence"][0].update(
        tool="functions.read_file", tool_output_excerpt="65535"
    )
    assert verdict(report, packet)["valid"]
    report["evidence"][0]["tool_output_excerpt"] = "655"
    assert not verdict(report, packet)["valid"]
    report["evidence"][0]["tool_output_excerpt"] = "65535"
    report["evidence"][0]["source"] = "/unrelated/file"
    assert not verdict(report, packet)["valid"]


@pytest.mark.parametrize(
    "claim,valid",
    [
        ('{"hostname": "web-01-gitops"}', True),
        ('{"hostname":"invented-host"}', False),
        ('{"nonexistent":"web-01-gitops"}', False),
        ('{"count":true}', False),
        ("{}", False),
    ],
)
def test_structured_result_fields_are_compared_without_fuzzy_matching(claim, valid):
    assert (
        MODULE.matches_output(claim, '{"hostname":"web-01-gitops","count":1}') is valid
    )
